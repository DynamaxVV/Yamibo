from __future__ import annotations

import json

from yamibo_mcp.db.connection import connect
from yamibo_mcp.db.migrations import migrate
from yamibo_mcp.db.repositories.series import SeriesRepository
from yamibo_mcp.domain.models import TitleSnapshot
from yamibo_mcp.server.resource_uris import series_chapters_uri
from yamibo_mcp.server.schemas import thread_summary_payload
from yamibo_mcp.time_utils import utc_now_iso
from yamibo_mcp.storage.paths import StoragePaths


def _generate_series_index_markdown(repo: SeriesRepository, *, limit: int = 10000) -> str:
    rows = repo.list_series(limit=limit)
    lines = ["# Series Index", ""]
    for row in rows:
        series_id = int(row["series_id"])
        lines.append(f"## {row['canonical_title'] or '(untitled)'}")
        lines.append(f"- series_id: {series_id}")
        lines.append(f"- series_key: {row['series_key'] or ''}")
        lines.append(f"- author: {row['author_guess'] or ''}")
        lines.append(f"- threads: {row['thread_count']}")
        lines.append(f"- chapters_resource_uri: {series_chapters_uri(series_id)}")
        threads = repo.list_threads_for_series(series_id)
        if threads:
            lines.append("- chapters:")
            for thread in threads[:20]:
                thread_tid = int(thread["tid"])
                lines.append(
                    f"  - {thread['chapter_name'] or thread['display_title'] or thread['raw_title']} "
                    f"(tid={thread_tid}, context={thread_summary_payload(thread).get('resources', {}).get('context')})"
                )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _generate_series_chapters_json(repo: SeriesRepository, *, series_id: int) -> str:
    series = repo.get_series(series_id)
    if series is None:
        raise ValueError(f"series not found: {series_id}")
    threads = repo.list_threads_for_series(series_id)
    payload = {
        "series_id": series_id,
        "canonical_title": series["canonical_title"],
        "series_key": series["series_key"],
        "aliases": json.loads(series["aliases_json"] or "[]"),
        "thread_count": len(threads),
        "chapters": [
            {
                "tid": int(thread["tid"]),
                "title": thread["display_title"] or thread["raw_title"],
                "chapter_name": thread["chapter_name"],
                "chapter_index": thread["chapter_index"],
                "chapter_index_end": thread.get("chapter_index_end"),
                "archive_status": thread["archive_status"],
                "sync_time": thread["sync_time"],
                "resources": thread_summary_payload(thread, include_export=True)["resources"],
            }
            for thread in threads
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _write_series_artifacts(settings) -> dict[str, object]:
    paths = StoragePaths(settings.data_dir)
    index_path = paths.series_index()
    index_path.parent.mkdir(parents=True, exist_ok=True)

    conn = connect(settings.db_path)
    try:
        migrate(conn)
        repo = SeriesRepository(conn)
        series_rows = repo.list_series(limit=10000)
        index_text = _generate_series_index_markdown(repo, limit=10000)
        index_path.write_text(index_text, encoding="utf-8")
        chapter_paths: list[str] = []
        for row in series_rows:
            series_id = int(row["series_id"])
            chapter_path = paths.series_chapters(series_id)
            chapter_path.parent.mkdir(parents=True, exist_ok=True)
            chapter_path.write_text(_generate_series_chapters_json(repo, series_id=series_id), encoding="utf-8")
            chapter_paths.append(str(chapter_path.relative_to(settings.data_dir)))
    finally:
        conn.close()
    return {
        "series_index_path": str(index_path.relative_to(settings.data_dir)),
        "series_chapters_count": len(chapter_paths),
        "series_chapter_paths": chapter_paths[:50],
    }


def handle_title_refine(repo, job, worker_id: str, lease_seconds: int, settings) -> None:
    repo.update_stage(job.job_id, "rebuild_series", progress_current=0, progress_total=1)
    conn = repo.conn
    try:
        conn.execute("UPDATE threads SET series_id = NULL, needs_series_review = 0")
        conn.execute("DELETE FROM series")
        rows = conn.execute(
            """
            SELECT
              tp.tid,
              tp.raw_title,
              tp.display_title,
              tp.group_name,
              tp.author_guess,
              tp.core_title_guess,
              tp.normalized_core_title,
              tp.series_key,
              tp.title_aliases_json,
              tp.chapter_name,
              tp.chapter_index,
              tp.chapter_index_end,
              tp.chapter_title,
              tp.subtitle,
              tp.tags_json,
              tp.confidence,
              tp.parser_version,
              tp.needs_review
            FROM title_parse tp
            ORDER BY tp.tid ASC
            """
        ).fetchall()
        total = len(rows)
        repo.update_stage(job.job_id, "rebuild_series", progress_current=0, progress_total=total or 1)
        series_repo = SeriesRepository(conn)
        rebuilt = 0
        for row in rows:
            title = TitleSnapshot(
                raw_title=row["raw_title"],
                display_title=row["display_title"],
                group_name=row["group_name"],
                author_guess=row["author_guess"],
                core_title_guess=row["core_title_guess"],
                normalized_core_title=row["normalized_core_title"],
                series_key=row["series_key"],
                title_aliases=json.loads(row["title_aliases_json"] or "[]"),
                chapter_name=row["chapter_name"],
                chapter_index=row["chapter_index"],
                chapter_index_end=row["chapter_index_end"],
                chapter_title=row["chapter_title"],
                subtitle=row["subtitle"],
                tags=json.loads(row["tags_json"] or "[]"),
                confidence=float(row["confidence"] or 0.0),
                needs_review=bool(row["needs_review"]),
                parser_version=row["parser_version"],
            )
            series_id, needs_review = series_repo.resolve_for_title(title)
            conn.execute(
                """
                UPDATE threads
                SET series_id = ?, needs_series_review = ?
                WHERE tid = ?
                """,
                (series_id, 1 if needs_review else 0, row["tid"]),
            )
            rebuilt += 1
            repo.heartbeat(job.job_id, worker_id, lease_seconds)
            repo.update_stage(job.job_id, "rebuild_series", progress_current=rebuilt, progress_total=total or 1)
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    artifacts = {"rebuilt_threads": rebuilt}
    artifacts.update(_write_series_artifacts(settings))
    repo.succeed(job.job_id, artifacts=artifacts)
