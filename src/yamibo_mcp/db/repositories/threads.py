from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

from yamibo_mcp.domain.models import ThreadSnapshot
from yamibo_mcp.domain.validation import validate_floor_sequence
from yamibo_mcp.domain.content import build_content_snapshot
from yamibo_mcp.db.repositories.series import SeriesRepository
from yamibo_mcp.db.repositories.postgres_search import search_threads_postgres
from yamibo_mcp.db.repositories.sqlite_search import search_threads_sqlite
from yamibo_mcp.time_utils import utc_now_iso
from yamibo_mcp.yamibo.title.normalizer import normalize_display_title, normalize_series_key


class ThreadsRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def _uses_sqlite_fts(self) -> bool:
        backend = getattr(self.conn, "backend", None)
        return backend not in {"postgres", "postgresql"}

    def _bool_value(self, value: bool) -> bool | int:
        return bool(value) if getattr(self.conn, "backend", None) in {"postgres", "postgresql"} else (1 if value else 0)

    def _bool_true_clause(self, column: str) -> str:
        return f"{column} IS TRUE" if getattr(self.conn, "backend", None) in {"postgres", "postgresql"} else f"{column} = 1"

    def _thread_list_filters(
        self,
        *,
        q: str | None = None,
        forum_id: int | None = None,
        days: int | None = None,
        archive_status: str | None = None,
    ) -> tuple[str, list[object]]:
        filters: list[str] = []
        args: list[object] = []
        backend = getattr(self.conn, "backend", None)
        if q:
            keywords = [kw for kw in q.strip().split() if kw]
            if keywords:
                if backend in {"postgres", "postgresql"}:
                    like_terms = []
                    for keyword in keywords:
                        like = f"%{keyword}%"
                        like_terms.append(
                            "("
                            "COALESCE(t.raw_title, '') ILIKE ? OR "
                            "COALESCE(t.display_title, '') ILIKE ? OR "
                            "COALESCE(t.publisher, '') ILIKE ? OR "
                            "COALESCE(t.content_preview, '') ILIKE ? OR "
                            "COALESCE(tp.core_title_guess, '') ILIKE ? OR "
                            "COALESCE(tp.series_key, '') ILIKE ?"
                            ")"
                        )
                        args.extend([like, like, like, like, like, like])
                    filters.append(" AND ".join(like_terms))
                else:
                    filters.append("thread_fts MATCH ?")
                    args.append(" AND ".join(keywords))
        if forum_id is not None:
            filters.append("t.forum_id = ?")
            args.append(forum_id)
        if days is not None:
            if backend in {"postgres", "postgresql"}:
                cutoff = datetime.now(timezone.utc) - timedelta(days=max(days, 0))
                filters.append("COALESCE(t.pub_time, TIMESTAMPTZ 'epoch') >= ?")
            else:
                cutoff = (datetime.now(timezone.utc) - timedelta(days=max(days, 0))).strftime("%Y-%m-%d")
                filters.append("substr(COALESCE(t.pub_time, ''), 1, 10) >= ?")
            args.append(cutoff)
        if archive_status:
            if archive_status == "none":
                filters.append("COALESCE(t.archive_status, '') = ''")
            else:
                filters.append("t.archive_status = ?")
                args.append(archive_status)
        where = f"WHERE {' AND '.join(filters)}" if filters else ""
        return where, args

    def _thread_list_select(self) -> str:
        return """
            SELECT
              t.tid, t.raw_title, t.display_title, t.publisher, t.pub_time, t.sync_time,
              t.archive_status, t.validation_status, t.context_path, t.series_id, t.export_path,
              t.forum_id, t.content_kind, t.category,
              t.local_reply_count, t.reply_count_checked_at, t.reply_count_mismatch_reason,
              t.remote_last_reply_at_raw, t.remote_last_reply_at, t.remote_last_replier,
              t.remote_reply_count, t.remote_observed_at, t.remote_observed_from,
              tp.core_title_guess, tp.series_key, tp.chapter_name, tp.chapter_index, tp.chapter_index_end, tp.group_name, tp.author_guess, tp.needs_review,
              (SELECT COUNT(*) FROM floors f WHERE f.tid = t.tid) AS floor_count,
              (SELECT f.pub_time FROM floors f WHERE f.tid = t.tid ORDER BY f.floor_no DESC, f.pid DESC LIMIT 1) AS last_floor_pub_time,
              (SELECT f.publisher FROM floors f WHERE f.tid = t.tid ORDER BY f.floor_no DESC, f.pid DESC LIMIT 1) AS last_floor_publisher
        """

    def _thread_list_from_clause(self, *, q: str | None = None) -> str:
        backend = getattr(self.conn, "backend", None)
        if backend in {"postgres", "postgresql"}:
            return "FROM threads t LEFT JOIN title_parse tp ON tp.tid = t.tid"
        from_clause = "FROM threads t LEFT JOIN title_parse tp ON tp.tid = t.tid"
        if q and q.strip():
            from_clause = "FROM threads t JOIN thread_fts ON thread_fts.tid = t.tid LEFT JOIN title_parse tp ON tp.tid = t.tid"
        return from_clause

    def _thread_list_order_clause(self, sort_key: str, sort_dir: str) -> str:
        backend = getattr(self.conn, "backend", None)
        sort_key = sort_key if sort_key in {"sync_time", "pub_time", "reply_count", "remote_last_reply_at"} else "sync_time"
        sort_dir = "asc" if sort_dir == "asc" else "desc"
        if backend in {"postgres", "postgresql"} and sort_key in {"sync_time", "remote_last_reply_at"}:
            null_order = "FIRST" if sort_dir == "asc" else "LAST"
            return f"t.{sort_key} {sort_dir.upper()} NULLS {null_order}, t.tid {sort_dir.upper()}"
        floor_count_expr = "(SELECT COUNT(*) FROM floors f WHERE f.tid = t.tid)"
        order_expr = {
            "sync_time": "COALESCE(t.sync_time, TIMESTAMPTZ 'epoch')" if backend in {"postgres", "postgresql"} else "COALESCE(t.sync_time, '')",
            "pub_time": "COALESCE(t.pub_time, TIMESTAMPTZ 'epoch')" if backend in {"postgres", "postgresql"} else "COALESCE(t.pub_time, '')",
            "reply_count": (
                "COALESCE("
                "t.remote_reply_count, "
                "t.local_reply_count, "
                f"CASE WHEN {floor_count_expr} > 0 THEN {floor_count_expr} - 1 ELSE 0 END"
                ")"
            ),
            "remote_last_reply_at": (
                "COALESCE(t.remote_last_reply_at, "
                "(SELECT f.pub_time FROM floors f WHERE f.tid = t.tid ORDER BY f.floor_no DESC, f.pid DESC LIMIT 1), "
                "TIMESTAMPTZ 'epoch')"
                if backend in {"postgres", "postgresql"}
                else "COALESCE(t.remote_last_reply_at, "
                "(SELECT f.pub_time FROM floors f WHERE f.tid = t.tid ORDER BY f.floor_no DESC, f.pid DESC LIMIT 1), '')"
            ),
        }[sort_key]
        return f"{order_expr} {sort_dir.upper()}, t.tid {sort_dir.upper()}"

    def upsert_snapshot(
        self,
        snapshot: ThreadSnapshot,
        *,
        forum_id: int | None = None,
        category: str | None = None,
        context_path: str | None = None,
        archive_status: str = "complete",
        missing_image_urls: list[str] | None = None,
        title_warnings: dict[str, object] | None = None,
    ) -> None:
        floor_sequence_errors = validate_floor_sequence(snapshot.floors)
        if floor_sequence_errors:
            raise ValueError(
                "invalid floor sequence: " + "; ".join(floor_sequence_errors)
            )
        now = utc_now_iso()
        max_pid = max((floor.pid for floor in snapshot.floors), default=None)
        content = build_content_snapshot(snapshot, forum_id=forum_id)
        primary_media_type = "image" if any(asset.asset_type == "image" for asset in content.assets) else "text"
        series_repo = SeriesRepository(self.conn)
        from yamibo_mcp.domain.forums import resolve_forum
        profile = resolve_forum(forum_id)
        if profile.default_series_key:
            series_id, needs_series_review = series_repo.resolve_for_forum(forum_id, commit=False)
        elif forum_id is None or profile.forum_id in {30, 55}:
            # Preserve the repository API's historical default: omitted forum_id
            # is the comic/novel title-series path.
            series_id, needs_series_review = series_repo.resolve_for_title(snapshot.title, commit=False)
        else:
            series_id, needs_series_review = series_repo.resolve_for_quarantine(profile.forum_id, commit=False)
        missing_images_json = json.dumps(missing_image_urls or [], ensure_ascii=False)
        self.conn.execute(
            """
            INSERT INTO threads (
              tid, series_id, page_type, raw_title, display_title, publisher, publisher_uid,
              pub_time, sync_time, last_pid, permission, image_count, context_path,
              archive_status, validation_status, missing_images_json, needs_title_review, needs_series_review,
              forum_id, content_kind, primary_media_type, category, local_reply_count
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'valid', ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(tid) DO UPDATE SET
              series_id = excluded.series_id,
              page_type = excluded.page_type,
              raw_title = excluded.raw_title,
              display_title = excluded.display_title,
              publisher = excluded.publisher,
              publisher_uid = excluded.publisher_uid,
              pub_time = excluded.pub_time,
              sync_time = excluded.sync_time,
              last_pid = excluded.last_pid,
              permission = excluded.permission,
              image_count = excluded.image_count,
              context_path = excluded.context_path,
              archive_status = excluded.archive_status,
              validation_status = excluded.validation_status,
              missing_images_json = excluded.missing_images_json,
              needs_title_review = excluded.needs_title_review,
              needs_series_review = excluded.needs_series_review,
              forum_id = excluded.forum_id,
              content_kind = excluded.content_kind,
              primary_media_type = excluded.primary_media_type,
              category = excluded.category,
              local_reply_count = excluded.local_reply_count
            """,
            (
                snapshot.tid,
                series_id,
                snapshot.page_type,
                snapshot.raw_title,
                snapshot.display_title,
                snapshot.publisher,
                snapshot.publisher_uid,
                snapshot.pub_time,
                now,
                max_pid,
                snapshot.permission,
                snapshot.image_count,
                context_path,
                archive_status,
                missing_images_json,
                self._bool_value(snapshot.title.needs_review),
                self._bool_value(needs_series_review),
                content.forum_id,
                content.content_kind,
                primary_media_type,
                category,
                max(0, len(snapshot.floors) - 1),
            ),
        )
        self._upsert_title(snapshot, title_warnings=title_warnings)
        self._upsert_floors(snapshot)
        self._upsert_fts(snapshot)
        self._sync_local_reply_metadata_for_tid(snapshot.tid)

    def _upsert_title(self, snapshot: ThreadSnapshot, *, title_warnings: dict[str, object] | None = None) -> None:
        title = snapshot.title
        self.conn.execute(
            """
            INSERT INTO title_parse (
              tid, raw_title, display_title, group_name, author_guess,
              core_title_guess, normalized_core_title, series_key,
              title_aliases_json, chapter_name, chapter_index, chapter_index_end, chapter_title, subtitle,
              tags_json, confidence, parser_version, needs_review, warnings_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(tid) DO UPDATE SET
              raw_title = excluded.raw_title,
              display_title = excluded.display_title,
              group_name = excluded.group_name,
              author_guess = excluded.author_guess,
              core_title_guess = excluded.core_title_guess,
              normalized_core_title = excluded.normalized_core_title,
              series_key = excluded.series_key,
              title_aliases_json = excluded.title_aliases_json,
              chapter_name = excluded.chapter_name,
              chapter_index = excluded.chapter_index,
              chapter_index_end = excluded.chapter_index_end,
              chapter_title = excluded.chapter_title,
              subtitle = excluded.subtitle,
              tags_json = excluded.tags_json,
              confidence = excluded.confidence,
              parser_version = excluded.parser_version,
              needs_review = excluded.needs_review,
              warnings_json = excluded.warnings_json
            """,
            (
                snapshot.tid,
                title.raw_title,
                title.display_title,
                title.group_name,
                title.author_guess,
                title.core_title_guess,
                title.normalized_core_title,
                title.series_key,
                json.dumps(title.title_aliases, ensure_ascii=False),
                title.chapter_name,
                title.chapter_index,
                title.chapter_index_end,
                title.chapter_title,
                title.subtitle,
                json.dumps(title.tags, ensure_ascii=False),
                title.confidence,
                title.parser_version,
                self._bool_value(title.needs_review),
                json.dumps(title_warnings, ensure_ascii=False) if title_warnings is not None else None,
            ),
        )

    def _upsert_floors(self, snapshot: ThreadSnapshot) -> None:
        current_pids = [floor.pid for floor in snapshot.floors]
        if current_pids:
            placeholders = ",".join("?" for _ in current_pids)
            self.conn.execute(
                f"DELETE FROM floors WHERE tid = ? AND pid NOT IN ({placeholders})",
                (snapshot.tid, *current_pids),
            )
        else:
            self.conn.execute("DELETE FROM floors WHERE tid = ?", (snapshot.tid,))
        for floor in snapshot.floors:
            self.conn.execute(
                """
                INSERT INTO floors (pid, tid, floor_no, publisher, publisher_uid, content, pub_time, has_images, content_hash, quote_text, reply_text)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)
                ON CONFLICT(pid) DO UPDATE SET
                  tid = excluded.tid,
                  floor_no = excluded.floor_no,
                  publisher = excluded.publisher,
                  publisher_uid = excluded.publisher_uid,
                  content = excluded.content,
                  pub_time = excluded.pub_time,
                  has_images = excluded.has_images,
                  quote_text = excluded.quote_text,
                  reply_text = excluded.reply_text
                """,
                (
                    floor.pid,
                    floor.tid,
                    floor.floor_no,
                    floor.publisher,
                    floor.publisher_uid,
                    floor.content,
                    floor.pub_time,
                    self._bool_value(floor.has_images),
                    floor.quote_text,
                    floor.reply_text,
                ),
            )

    def _upsert_fts(self, snapshot: ThreadSnapshot) -> None:
        if not self._uses_sqlite_fts():
            return
        content_preview = "\n".join(floor.content for floor in snapshot.floors[:3])
        self.conn.execute("DELETE FROM thread_fts WHERE tid = ?", (snapshot.tid,))
        self.conn.execute(
            """
            INSERT INTO thread_fts (tid, title, core_title, author, group_name, content_preview, catalog_text)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot.tid,
                snapshot.display_title,
                snapshot.title.core_title_guess,
                snapshot.title.author_guess,
                snapshot.title.group_name,
                content_preview,
                "",
            ),
        )

    def get_thread(self, tid: int) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM threads WHERE tid = ?", (tid,)).fetchone()

    def count_threads_for_series(self, series_id: int) -> int:
        row = self.conn.execute("SELECT COUNT(*) AS c FROM threads WHERE series_id = ?", (series_id,)).fetchone()
        return int(row["c"]) if row is not None else 0

    def delete_thread(self, tid: int) -> tuple[dict[str, object], dict[str, object]]:
        thread_row = self.get_thread(tid)
        if thread_row is None:
            raise ValueError(f"thread not found: {tid}")
        title_row = self.get_title_parse(tid)
        floor_rows = self.list_floors(tid)
        before = {
            "thread": dict(thread_row),
            "title_parse": None if title_row is None else dict(title_row),
            "floors": [dict(row) for row in floor_rows],
        }
        if self._uses_sqlite_fts():
            self.conn.execute(
                "DELETE FROM rag_chunks_fts WHERE chunk_id IN (SELECT chunk_id FROM rag_chunks WHERE tid = ?)",
                (tid,),
            )
        self.conn.execute("DELETE FROM rag_chunks WHERE tid = ?", (tid,))
        self.conn.execute("DELETE FROM assets WHERE tid = ?", (tid,))
        self.conn.execute("DELETE FROM content_blocks WHERE tid = ?", (tid,))
        self.conn.execute("DELETE FROM catalog WHERE tid = ?", (tid,))
        self.conn.execute("DELETE FROM sync_runs WHERE tid = ?", (tid,))
        self.conn.execute("DELETE FROM floors WHERE tid = ?", (tid,))
        self.conn.execute("DELETE FROM title_parse WHERE tid = ?", (tid,))
        if self._uses_sqlite_fts():
            self.conn.execute("DELETE FROM thread_fts WHERE tid = ?", (tid,))
        self.conn.execute("DELETE FROM threads WHERE tid = ?", (tid,))
        return before, {"tid": tid, "deleted": True}

    def reset_series_assignments(self) -> None:
        self.conn.execute(
            "UPDATE threads SET series_id = NULL, needs_series_review = ?",
            (self._bool_value(False),),
        )
        self.conn.execute("DELETE FROM series")

    def list_title_parse_rows(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            """
            SELECT
              tp.tid,
              t.forum_id,
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

    def set_thread_series(self, tid: int, series_id: int, needs_series_review: bool) -> None:
        self.conn.execute(
            """
            UPDATE threads
            SET series_id = ?, needs_series_review = ?
            WHERE tid = ?
            """,
            (series_id, self._bool_value(needs_series_review), tid),
        )

    def update_archive_metadata(
        self,
        tid: int,
        *,
        display_title: str | None,
        chapter_name: str | None,
        chapter_index: float | None,
        author_guess: str | None,
        group_name: str | None,
    ) -> None:
        thread_row = self.get_thread(tid)
        if thread_row is None:
            raise ValueError(f"thread not found: {tid}")
        title_row = self.get_title_parse(tid)
        final_display_title = normalize_display_title(
            display_title or thread_row["display_title"] or thread_row["raw_title"] or ""
        )
        if not final_display_title:
            raise ValueError("display_title is required")
        self.conn.execute(
            """
            UPDATE threads
            SET display_title = ?, sync_time = ?
            WHERE tid = ?
            """,
            (final_display_title, utc_now_iso(), tid),
        )
        if title_row is None:
            core_title_guess = normalize_display_title(
                thread_row["display_title"] or thread_row["raw_title"] or final_display_title
            )
            normalized_core_title = normalize_series_key(core_title_guess)
            series_key = normalize_series_key(core_title_guess)
            self.conn.execute(
                """
                INSERT INTO title_parse (
                  tid, raw_title, display_title, group_name, author_guess,
                  core_title_guess, normalized_core_title, series_key,
                  title_aliases_json, chapter_name, chapter_index, chapter_index_end, chapter_title, subtitle,
                  tags_json, confidence, parser_version, needs_review, warnings_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, '[]', ?, ?, NULL, NULL, NULL, '[]', ?, ?, ?, NULL)
                """,
                (
                    tid,
                    thread_row["raw_title"] or final_display_title,
                    final_display_title,
                    group_name,
                    author_guess,
                    core_title_guess,
                    normalized_core_title,
                    series_key,
                    chapter_name,
                    chapter_index,
                    1.0,
                    "manual-edit",
                    self._bool_value(False),
                ),
            )
        else:
            self.conn.execute(
                """
                UPDATE title_parse
                SET display_title = ?, chapter_name = ?, chapter_index = ?, author_guess = ?, group_name = ?
                WHERE tid = ?
                """,
                (final_display_title, chapter_name, chapter_index, author_guess, group_name, tid),
            )
        if self._uses_sqlite_fts():
            fts_existing = self.conn.execute(
                "SELECT content_preview, catalog_text FROM thread_fts WHERE tid = ?",
                (tid,),
            ).fetchone()
            self.conn.execute("DELETE FROM thread_fts WHERE tid = ?", (tid,))
            title_core = self.get_title_parse(tid)
            self.conn.execute(
                """
                INSERT INTO thread_fts (tid, title, core_title, author, group_name, content_preview, catalog_text)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tid,
                    final_display_title,
                    None if title_core is None else title_core["core_title_guess"],
                    author_guess,
                    group_name,
                    "" if fts_existing is None else (fts_existing["content_preview"] or ""),
                    "" if fts_existing is None else (fts_existing["catalog_text"] or ""),
                ),
            )

    def get_title_parse(self, tid: int) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM title_parse WHERE tid = ?", (tid,)).fetchone()

    def list_title_review_items(self, *, limit: int = 100) -> list[sqlite3.Row]:
        return self.conn.execute(
            f"""
            SELECT
              t.tid, t.raw_title, t.display_title, t.series_id,
              t.needs_title_review, t.needs_series_review,
              tp.group_name, tp.author_guess, tp.core_title_guess, tp.series_key,
              tp.title_aliases_json, tp.chapter_name, tp.chapter_index, tp.chapter_index_end, tp.chapter_title,
              tp.subtitle, tp.tags_json, tp.confidence, tp.needs_review
            FROM threads t
            LEFT JOIN title_parse tp ON tp.tid = t.tid
            WHERE {self._bool_true_clause("t.needs_title_review")}
               OR {self._bool_true_clause("t.needs_series_review")}
               OR {self._bool_true_clause("tp.needs_review")}
            ORDER BY t.sync_time DESC, t.tid DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    def confirm_title_review(self, tid: int) -> tuple[dict[str, object], dict[str, object]]:
        before_row = self.conn.execute(
            """
            SELECT
              t.tid, t.needs_title_review, t.needs_series_review,
              tp.needs_review AS title_parse_needs_review
            FROM threads t
            LEFT JOIN title_parse tp ON tp.tid = t.tid
            WHERE t.tid = ?
            """,
            (tid,),
        ).fetchone()
        if before_row is None:
            raise ValueError(f"thread not found: {tid}")
        before = dict(before_row)
        self.conn.execute(
            "UPDATE title_parse SET needs_review = ? WHERE tid = ?",
            (self._bool_value(False), tid),
        )
        self.conn.execute(
            "UPDATE threads SET needs_title_review = ? WHERE tid = ?",
            (self._bool_value(False), tid),
        )
        after_row = self.conn.execute(
            """
            SELECT
              t.tid, t.needs_title_review, t.needs_series_review,
              tp.needs_review AS title_parse_needs_review
            FROM threads t
            LEFT JOIN title_parse tp ON tp.tid = t.tid
            WHERE t.tid = ?
            """,
            (tid,),
        ).fetchone()
        return before, dict(after_row)

    def update_title_review(
        self,
        tid: int,
        *,
        display_title: str,
        group_name: str | None,
        author_guess: str | None,
        core_title_guess: str,
        series_key: str | None,
        title_aliases: list[str] | None,
        chapter_name: str | None,
        chapter_index: float | None,
        chapter_index_end: float | None,
        chapter_title: str | None,
        subtitle: str | None,
        tags: list[str] | None,
        confidence: float | None,
        needs_review: bool,
    ) -> tuple[dict[str, object], dict[str, object]]:
        thread_row = self.get_thread(tid)
        title_row = self.get_title_parse(tid)
        if thread_row is None or title_row is None:
            raise ValueError(f"thread not found: {tid}")

        normalized_display_title = normalize_display_title(display_title or thread_row["display_title"] or thread_row["raw_title"] or "")
        normalized_core_title = normalize_display_title(core_title_guess or title_row["core_title_guess"] or "")
        final_series_key = normalize_series_key(series_key or normalized_core_title)
        if not normalized_display_title:
            raise ValueError("display_title is required")
        if not normalized_core_title:
            raise ValueError("core_title_guess is required")
        if not final_series_key:
            raise ValueError("series_key is required")

        aliases = [item for item in (title_aliases or []) if item]
        tag_list = [item for item in (tags or []) if item]
        final_confidence = float(title_row["confidence"] if confidence is None else confidence)

        before = {
            "thread": dict(thread_row),
            "title_parse": dict(title_row),
        }

        from yamibo_mcp.domain.models import TitleSnapshot

        title = TitleSnapshot(
            raw_title=title_row["raw_title"] or thread_row["raw_title"] or normalized_display_title,
            display_title=normalized_display_title,
            group_name=group_name,
            author_guess=author_guess,
            core_title_guess=normalized_core_title,
            normalized_core_title=normalize_series_key(normalized_core_title),
            series_key=final_series_key,
            title_aliases=aliases,
            chapter_name=chapter_name,
            chapter_index=chapter_index,
            chapter_index_end=chapter_index_end,
            chapter_title=chapter_title,
            subtitle=subtitle,
            tags=tag_list,
            confidence=final_confidence,
            needs_review=needs_review,
            parser_version=title_row["parser_version"] or "title-v1",
        )
        series_id, needs_series_review = SeriesRepository(self.conn).resolve_for_title(title)

        self.conn.execute(
            """
            UPDATE title_parse
            SET display_title = ?,
                group_name = ?,
                author_guess = ?,
                core_title_guess = ?,
                normalized_core_title = ?,
                series_key = ?,
                title_aliases_json = ?,
                chapter_name = ?,
                chapter_index = ?,
                chapter_index_end = ?,
                chapter_title = ?,
                subtitle = ?,
                tags_json = ?,
                confidence = ?,
                needs_review = ?,
                parser_version = ?,
                warnings_json = NULL
            WHERE tid = ?
            """,
            (
                title.display_title,
                title.group_name,
                title.author_guess,
                title.core_title_guess,
                title.normalized_core_title,
                title.series_key,
                json.dumps(title.title_aliases, ensure_ascii=False),
                title.chapter_name,
                title.chapter_index,
                title.chapter_index_end,
                title.chapter_title,
                title.subtitle,
                json.dumps(title.tags, ensure_ascii=False),
                title.confidence,
                self._bool_value(title.needs_review),
                title.parser_version,
                tid,
            ),
        )
        self.conn.execute(
            """
            UPDATE threads
            SET display_title = ?,
                series_id = ?,
                needs_title_review = ?,
                needs_series_review = ?,
                sync_time = ?
            WHERE tid = ?
            """,
            (
                title.display_title,
                series_id,
                self._bool_value(title.needs_review),
                self._bool_value(needs_series_review),
                utc_now_iso(),
                tid,
            ),
        )
        if not self._uses_sqlite_fts():
            after_thread = self.get_thread(tid)
            after_title = self.get_title_parse(tid)
            return before, {
                "thread": None if after_thread is None else dict(after_thread),
                "title_parse": None if after_title is None else dict(after_title),
            }
        fts_existing = self.conn.execute(
            "SELECT content_preview, catalog_text FROM thread_fts WHERE tid = ?",
            (tid,),
        ).fetchone()
        self.conn.execute("DELETE FROM thread_fts WHERE tid = ?", (tid,))
        self.conn.execute(
            """
            INSERT INTO thread_fts (tid, title, core_title, author, group_name, content_preview, catalog_text)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                tid,
                title.display_title,
                title.core_title_guess,
                title.author_guess,
                title.group_name,
                "" if fts_existing is None else (fts_existing["content_preview"] or ""),
                "" if fts_existing is None else (fts_existing["catalog_text"] or ""),
            ),
        )

        after_thread = self.get_thread(tid)
        after_title = self.get_title_parse(tid)
        return before, {
            "thread": None if after_thread is None else dict(after_thread),
            "title_parse": None if after_title is None else dict(after_title),
        }

    def list_threads(self, *, limit: int = 100, forum_id: int | None = None) -> list[sqlite3.Row]:
        base_select = f"""{self._thread_list_select()} {self._thread_list_from_clause()}"""
        if forum_id is not None:
            return self.conn.execute(
                f"{base_select} WHERE t.forum_id = ? ORDER BY {self._thread_list_order_clause('sync_time', 'desc')} LIMIT ?",
                (forum_id, limit),
            ).fetchall()
        return self.conn.execute(
            f"{base_select} ORDER BY {self._thread_list_order_clause('sync_time', 'desc')} LIMIT ?",
            (limit,),
        ).fetchall()

    def list_threads_page(
        self,
        *,
        page: int = 1,
        page_size: int = 50,
        q: str | None = None,
        forum_id: int | None = None,
        days: int | None = None,
        archive_status: str | None = None,
        sort_key: str = "remote_last_reply_at",
        sort_dir: str = "desc",
        total_count: int | None = None,
    ) -> dict[str, object]:
        page_size = min(max(page_size, 1), 200)
        where_clause, args = self._thread_list_filters(q=q, forum_id=forum_id, days=days, archive_status=archive_status)
        from_clause = self._thread_list_from_clause(q=q)
        if total_count is None:
            total_count = self.count_threads_filtered(
                q=q,
                forum_id=forum_id,
                days=days,
                archive_status=archive_status,
            )
        total_pages = max(1, (total_count + page_size - 1) // page_size)
        page = min(max(page, 1), total_pages)
        offset = (page - 1) * page_size
        order_clause = self._thread_list_order_clause(sort_key, sort_dir)
        rows = self.conn.execute(
            f"""
            {self._thread_list_select()}
            {from_clause}
            {where_clause}
            ORDER BY {order_clause}
            LIMIT ? OFFSET ?
            """,
            [*args, page_size, offset],
        ).fetchall()
        return {
            "page": page,
            "page_size": page_size,
            "total_count": total_count,
            "total_pages": total_pages,
            "items": rows,
        }

    def count_threads_filtered(
        self,
        *,
        q: str | None = None,
        forum_id: int | None = None,
        days: int | None = None,
        archive_status: str | None = None,
    ) -> int:
        where_clause, args = self._thread_list_filters(
            q=q,
            forum_id=forum_id,
            days=days,
            archive_status=archive_status,
        )
        # The title_parse join is needed for searching and selecting rows, but
        # adds no value to an unsearched count over the threads table.
        from_clause = self._thread_list_from_clause(q=q) if q else "FROM threads t"
        row = self.conn.execute(
            f"SELECT COUNT(*) AS total_count {from_clause} {where_clause}",
            args,
        ).fetchone()
        return int(row["total_count"] or 0)

    def count_threads(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) AS c FROM threads").fetchone()
        return int(row["c"]) if row is not None else 0

    def count_threads_by_forum(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            """
            SELECT forum_id, COUNT(*) AS cnt
            FROM threads
            WHERE forum_id IS NOT NULL
            GROUP BY forum_id
            ORDER BY cnt DESC
            """
        ).fetchall()

    def count_exported_threads(self) -> int:
        row = self.conn.execute(f"SELECT COUNT(*) AS c FROM threads WHERE {self._bool_true_clause('is_exported')}").fetchone()
        return int(row["c"]) if row is not None else 0

    def mark_exported(self, tid: int, export_path: str) -> None:
        cur = self.conn.execute(
            """
            UPDATE threads
            SET is_exported = ?, export_path = ?
            WHERE tid = ?
            """,
            (self._bool_value(True), export_path, tid),
        )
        if cur.rowcount != 1:
            raise ValueError(f"thread not found: {tid}")

    def list_exports(self, *, limit: int = 100) -> list[sqlite3.Row]:
        return self.conn.execute(
            f"""
            SELECT tid, raw_title, display_title, archive_status, is_exported, export_path
            FROM threads
            WHERE {self._bool_true_clause("is_exported")} OR export_path IS NOT NULL
            ORDER BY tid DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    def search_threads(self, query: str, *, limit: int = 50, forum_id: int | None = None) -> list[sqlite3.Row]:
        backend = getattr(self.conn, "backend", None)
        if backend in {"postgres", "postgresql"}:
            return search_threads_postgres(self.conn, query, limit=limit, forum_id=forum_id)
        return search_threads_sqlite(self.conn, query, limit=limit, forum_id=forum_id)

    def probe_archive_states(self, tids: list[int]) -> list[dict[str, object]]:
        if not tids:
            return []
        normalized_tids = list(dict.fromkeys(int(tid) for tid in tids))
        placeholders = ",".join("?" for _ in normalized_tids)
        rows = self.conn.execute(
            f"""
            SELECT
              t.tid,
              t.archive_status,
              t.sync_time,
              t.forum_id,
              t.content_kind,
              t.publisher,
              t.pub_time,
              t.local_reply_count,
              t.reply_count_checked_at,
              t.reply_count_mismatch_reason,
              t.remote_last_reply_at_raw,
              t.remote_last_reply_at,
              t.remote_last_replier,
              t.remote_reply_count,
              t.remote_observed_at,
              t.remote_observed_from,
              COALESCE((SELECT COUNT(*) FROM floors f WHERE f.tid = t.tid), 0) AS floor_count,
              (SELECT f.pid FROM floors f WHERE f.tid = t.tid ORDER BY f.floor_no DESC, f.pid DESC LIMIT 1) AS last_pid,
              (SELECT f.floor_no FROM floors f WHERE f.tid = t.tid ORDER BY f.floor_no DESC, f.pid DESC LIMIT 1) AS last_floor_no,
              (SELECT f.pub_time FROM floors f WHERE f.tid = t.tid ORDER BY f.floor_no DESC, f.pid DESC LIMIT 1) AS last_floor_pub_time
            FROM threads t
            WHERE t.tid IN ({placeholders})
            """,
            normalized_tids,
        ).fetchall()
        by_tid = {int(row["tid"]): row for row in rows}
        result: list[dict[str, object]] = []
        for tid in normalized_tids:
            row = by_tid.get(tid)
            if row is None:
                result.append(
                    {
                        "tid": tid,
                        "archived": False,
                        "archive_status": None,
                        "sync_time": None,
                        "forum_id": None,
                        "content_kind": None,
                        "publisher": None,
                        "pub_time": None,
                        "local_floor_count": 0,
                        "local_reply_count": 0,
                        "reply_count_checked_at": None,
                        "reply_count_mismatch_reason": None,
                        "remote_last_reply_at_raw": None,
                        "remote_last_reply_at": None,
                        "remote_last_replier": None,
                        "remote_reply_count": None,
                        "remote_observed_at": None,
                        "remote_observed_from": None,
                        "local_last_pid": None,
                        "local_last_floor_no": None,
                        "local_last_floor_pub_time": None,
                        "local_last_reply_at": None,
                    }
                )
                continue
            floor_count = int(row["floor_count"] or 0)
            last_floor_pub_time = row["last_floor_pub_time"]
            result.append(
                {
                    "tid": int(row["tid"]),
                    "archived": True,
                    "archive_status": row["archive_status"],
                    "sync_time": row["sync_time"],
                    "forum_id": row["forum_id"],
                    "content_kind": row["content_kind"],
                    "publisher": row["publisher"],
                    "pub_time": row["pub_time"],
                    "local_floor_count": floor_count,
                    "local_reply_count": row["local_reply_count"] if row["local_reply_count"] is not None else max(0, floor_count - 1),
                    "reply_count_checked_at": row["reply_count_checked_at"],
                    "reply_count_mismatch_reason": row["reply_count_mismatch_reason"],
                    "remote_last_reply_at_raw": row["remote_last_reply_at_raw"],
                    "remote_last_reply_at": row["remote_last_reply_at"],
                    "remote_last_replier": row["remote_last_replier"],
                    "remote_reply_count": row["remote_reply_count"],
                    "remote_observed_at": row["remote_observed_at"],
                    "remote_observed_from": row["remote_observed_from"],
                    "local_last_pid": row["last_pid"],
                    "local_last_floor_no": row["last_floor_no"],
                    "local_last_floor_pub_time": last_floor_pub_time,
                    "local_last_reply_at": last_floor_pub_time,
                }
            )
        return result

    def _sync_local_reply_metadata_for_tid(self, tid: int, *, overwrite_last_reply: bool = False) -> bool:
        row = self.conn.execute(
            """
            SELECT
              t.tid,
              t.local_reply_count,
              t.remote_last_reply_at_raw,
              t.remote_last_reply_at,
              t.remote_last_replier,
              COALESCE((SELECT COUNT(*) FROM floors f WHERE f.tid = t.tid), 0) AS floor_count,
              (SELECT f.pub_time FROM floors f WHERE f.tid = t.tid ORDER BY f.floor_no DESC, f.pid DESC LIMIT 1) AS last_floor_pub_time,
              (SELECT f.publisher FROM floors f WHERE f.tid = t.tid ORDER BY f.floor_no DESC, f.pid DESC LIMIT 1) AS last_floor_publisher
            FROM threads t
            WHERE t.tid = ?
            """,
            (tid,),
        ).fetchone()
        if row is None:
            return False

        floor_count = int(row["floor_count"] or 0)
        computed_reply_count = max(floor_count - 1, 0)
        last_floor_pub_time = row["last_floor_pub_time"]
        last_floor_publisher = row["last_floor_publisher"]
        next_remote_last_reply_raw = row["remote_last_reply_at_raw"]
        next_remote_last_reply_at = row["remote_last_reply_at"]
        next_remote_last_replier = row["remote_last_replier"]

        local_changed = row["local_reply_count"] != computed_reply_count
        if overwrite_last_reply:
            reply_changed = (
                next_remote_last_reply_raw != last_floor_pub_time
                or next_remote_last_reply_at != last_floor_pub_time
                or next_remote_last_replier != last_floor_publisher
            )
            next_remote_last_reply_raw = last_floor_pub_time
            next_remote_last_reply_at = last_floor_pub_time
            next_remote_last_replier = last_floor_publisher
        else:
            reply_changed = False
            if next_remote_last_reply_raw in {None, ""} and last_floor_pub_time not in {None, ""}:
                next_remote_last_reply_raw = last_floor_pub_time
                reply_changed = True
            if next_remote_last_reply_at in {None, ""} and last_floor_pub_time not in {None, ""}:
                next_remote_last_reply_at = last_floor_pub_time
                reply_changed = True
            if next_remote_last_replier in {None, ""} and last_floor_publisher not in {None, ""}:
                next_remote_last_replier = last_floor_publisher
                reply_changed = True

        if not local_changed and not reply_changed:
            return False

        self.conn.execute(
            """
            UPDATE threads
            SET local_reply_count = ?,
                remote_last_reply_at_raw = ?,
                remote_last_reply_at = ?,
                remote_last_replier = ?
            WHERE tid = ?
            """,
            (
                computed_reply_count,
                next_remote_last_reply_raw,
                next_remote_last_reply_at,
                next_remote_last_replier,
                int(row["tid"]),
            ),
        )
        return True

    def backfill_local_reply_metadata(self, *, overwrite_last_reply: bool = False) -> dict[str, int]:
        rows = self.conn.execute(
            """
            SELECT
              t.tid,
              t.local_reply_count,
              t.remote_last_reply_at_raw,
              t.remote_last_reply_at,
              t.remote_last_replier,
              COALESCE((SELECT COUNT(*) FROM floors f WHERE f.tid = t.tid), 0) AS floor_count,
              (SELECT f.pub_time FROM floors f WHERE f.tid = t.tid ORDER BY f.floor_no DESC, f.pid DESC LIMIT 1) AS last_floor_pub_time,
              (SELECT f.publisher FROM floors f WHERE f.tid = t.tid ORDER BY f.floor_no DESC, f.pid DESC LIMIT 1) AS last_floor_publisher
            FROM threads t
            ORDER BY t.tid ASC
            """
        ).fetchall()
        local_reply_count_updates = 0
        last_reply_updates = 0
        untouched = 0
        for row in rows:
            tid = int(row["tid"])
            local_changed = row["local_reply_count"] != max(int(row["floor_count"] or 0) - 1, 0)
            reply_changed = overwrite_last_reply or (
                row["remote_last_reply_at_raw"] in {None, ""} and row["last_floor_pub_time"] not in {None, ""}
            ) or (
                row["remote_last_reply_at"] in {None, ""} and row["last_floor_pub_time"] not in {None, ""}
            ) or (
                row["remote_last_replier"] in {None, ""} and row["last_floor_publisher"] not in {None, ""}
            )
            if not local_changed and not reply_changed:
                untouched += 1
                continue
            if local_changed:
                local_reply_count_updates += 1
            if reply_changed:
                last_reply_updates += 1

        backend = getattr(self.conn, "backend", None)
        if rows:
            if backend in {"postgres", "postgresql"}:
                self.conn.execute(
                    """
                    WITH floor_counts AS (
                        SELECT f.tid, COUNT(*) AS floor_count
                        FROM floors f
                        GROUP BY f.tid
                    ),
                    last_floors AS (
                        SELECT DISTINCT ON (f.tid)
                            f.tid,
                            CAST(f.pub_time AS TEXT) AS last_floor_pub_time_raw,
                            f.pub_time AS last_floor_pub_time,
                            f.publisher AS last_floor_publisher
                        FROM floors f
                        ORDER BY f.tid, f.floor_no DESC, f.pid DESC
                    ),
                    floor_meta AS (
                        SELECT
                            t.tid,
                            GREATEST(COALESCE(fc.floor_count, 0) - 1, 0) AS computed_reply_count,
                            lf.last_floor_pub_time_raw,
                            lf.last_floor_pub_time,
                            lf.last_floor_publisher
                        FROM threads t
                        LEFT JOIN floor_counts fc ON fc.tid = t.tid
                        LEFT JOIN last_floors lf ON lf.tid = t.tid
                    )
                    UPDATE threads AS t
                    SET local_reply_count = fm.computed_reply_count,
                        remote_last_reply_at_raw = CASE
                            WHEN :overwrite_last_reply THEN fm.last_floor_pub_time_raw
                            WHEN (t.remote_last_reply_at_raw IS NULL OR t.remote_last_reply_at_raw = '')
                                 AND fm.last_floor_pub_time_raw IS NOT NULL
                                 AND fm.last_floor_pub_time_raw <> '' THEN fm.last_floor_pub_time_raw
                            ELSE t.remote_last_reply_at_raw
                        END,
                        remote_last_reply_at = CASE
                            WHEN :overwrite_last_reply THEN fm.last_floor_pub_time
                            WHEN t.remote_last_reply_at IS NULL
                                 AND fm.last_floor_pub_time IS NOT NULL THEN fm.last_floor_pub_time
                            ELSE t.remote_last_reply_at
                        END,
                        remote_last_replier = CASE
                            WHEN :overwrite_last_reply THEN fm.last_floor_publisher
                            WHEN (t.remote_last_replier IS NULL OR t.remote_last_replier = '')
                                 AND fm.last_floor_publisher IS NOT NULL
                                 AND fm.last_floor_publisher <> '' THEN fm.last_floor_publisher
                            ELSE t.remote_last_replier
                        END
                    FROM floor_meta fm
                    WHERE t.tid = fm.tid
                      AND (
                        t.local_reply_count IS DISTINCT FROM fm.computed_reply_count
                        OR (
                            :overwrite_last_reply
                            AND (
                                t.remote_last_reply_at_raw IS DISTINCT FROM fm.last_floor_pub_time_raw
                                OR t.remote_last_reply_at IS DISTINCT FROM fm.last_floor_pub_time
                                OR t.remote_last_replier IS DISTINCT FROM fm.last_floor_publisher
                            )
                        )
                        OR (
                            NOT :overwrite_last_reply
                            AND (
                                ((t.remote_last_reply_at_raw IS NULL OR t.remote_last_reply_at_raw = '') AND fm.last_floor_pub_time_raw IS NOT NULL AND fm.last_floor_pub_time_raw <> '')
                                OR (t.remote_last_reply_at IS NULL AND fm.last_floor_pub_time IS NOT NULL)
                                OR ((t.remote_last_replier IS NULL OR t.remote_last_replier = '') AND fm.last_floor_publisher IS NOT NULL AND fm.last_floor_publisher <> '')
                            )
                        )
                      )
                    """,
                    {"overwrite_last_reply": overwrite_last_reply},
                )
            else:
                self.conn.execute(
                    """
                    UPDATE threads
                    SET local_reply_count = (
                            CASE
                                WHEN (SELECT COUNT(*) FROM floors f WHERE f.tid = threads.tid) > 0
                                    THEN (SELECT COUNT(*) FROM floors f WHERE f.tid = threads.tid) - 1
                                ELSE 0
                            END
                        ),
                        remote_last_reply_at_raw = CASE
                            WHEN :overwrite_last_reply THEN (
                                SELECT f.pub_time FROM floors f WHERE f.tid = threads.tid ORDER BY f.floor_no DESC, f.pid DESC LIMIT 1
                            )
                            WHEN (remote_last_reply_at_raw IS NULL OR remote_last_reply_at_raw = '')
                                 AND COALESCE((
                                    SELECT f.pub_time FROM floors f WHERE f.tid = threads.tid ORDER BY f.floor_no DESC, f.pid DESC LIMIT 1
                                 ), '') <> '' THEN (
                                    SELECT f.pub_time FROM floors f WHERE f.tid = threads.tid ORDER BY f.floor_no DESC, f.pid DESC LIMIT 1
                                 )
                            ELSE remote_last_reply_at_raw
                        END,
                        remote_last_reply_at = CASE
                            WHEN :overwrite_last_reply THEN (
                                SELECT f.pub_time FROM floors f WHERE f.tid = threads.tid ORDER BY f.floor_no DESC, f.pid DESC LIMIT 1
                            )
                            WHEN (remote_last_reply_at IS NULL OR remote_last_reply_at = '')
                                 AND COALESCE((
                                    SELECT f.pub_time FROM floors f WHERE f.tid = threads.tid ORDER BY f.floor_no DESC, f.pid DESC LIMIT 1
                                 ), '') <> '' THEN (
                                    SELECT f.pub_time FROM floors f WHERE f.tid = threads.tid ORDER BY f.floor_no DESC, f.pid DESC LIMIT 1
                                 )
                            ELSE remote_last_reply_at
                        END,
                        remote_last_replier = CASE
                            WHEN :overwrite_last_reply THEN (
                                SELECT f.publisher FROM floors f WHERE f.tid = threads.tid ORDER BY f.floor_no DESC, f.pid DESC LIMIT 1
                            )
                            WHEN (remote_last_replier IS NULL OR remote_last_replier = '')
                                 AND COALESCE((
                                    SELECT f.publisher FROM floors f WHERE f.tid = threads.tid ORDER BY f.floor_no DESC, f.pid DESC LIMIT 1
                                 ), '') <> '' THEN (
                                    SELECT f.publisher FROM floors f WHERE f.tid = threads.tid ORDER BY f.floor_no DESC, f.pid DESC LIMIT 1
                                 )
                            ELSE remote_last_replier
                        END
                    WHERE
                        local_reply_count IS NOT (
                            CASE
                                WHEN (SELECT COUNT(*) FROM floors f WHERE f.tid = threads.tid) > 0
                                    THEN (SELECT COUNT(*) FROM floors f WHERE f.tid = threads.tid) - 1
                                ELSE 0
                            END
                        )
                        OR (
                            :overwrite_last_reply
                            AND (
                                remote_last_reply_at_raw IS NOT (
                                    SELECT f.pub_time FROM floors f WHERE f.tid = threads.tid ORDER BY f.floor_no DESC, f.pid DESC LIMIT 1
                                )
                                OR remote_last_reply_at IS NOT (
                                    SELECT f.pub_time FROM floors f WHERE f.tid = threads.tid ORDER BY f.floor_no DESC, f.pid DESC LIMIT 1
                                )
                                OR remote_last_replier IS NOT (
                                    SELECT f.publisher FROM floors f WHERE f.tid = threads.tid ORDER BY f.floor_no DESC, f.pid DESC LIMIT 1
                                )
                            )
                        )
                        OR (
                            NOT :overwrite_last_reply
                            AND (
                                ((remote_last_reply_at_raw IS NULL OR remote_last_reply_at_raw = '') AND COALESCE((SELECT f.pub_time FROM floors f WHERE f.tid = threads.tid ORDER BY f.floor_no DESC, f.pid DESC LIMIT 1), '') <> '')
                                OR ((remote_last_reply_at IS NULL OR remote_last_reply_at = '') AND COALESCE((SELECT f.pub_time FROM floors f WHERE f.tid = threads.tid ORDER BY f.floor_no DESC, f.pid DESC LIMIT 1), '') <> '')
                                OR ((remote_last_replier IS NULL OR remote_last_replier = '') AND COALESCE((SELECT f.publisher FROM floors f WHERE f.tid = threads.tid ORDER BY f.floor_no DESC, f.pid DESC LIMIT 1), '') <> '')
                            )
                        )
                    """,
                    {"overwrite_last_reply": overwrite_last_reply},
                )

        return {
            "thread_count": len(rows),
            "local_reply_count_updates": local_reply_count_updates,
            "last_reply_updates": last_reply_updates,
            "unchanged": untouched,
        }

    def update_remote_observation_snapshot(
        self,
        *,
        tid: int,
        forum_id: int | None,
        source_kind: str,
        observation_type: str,
        remote_title: str | None = None,
        remote_category: str | None = None,
        remote_publisher: str | None = None,
        remote_posted_at_raw: str | None = None,
        remote_posted_at: str | None = None,
        remote_last_reply_at_raw: str | None = None,
        remote_last_reply_at: str | None = None,
        remote_last_replier: str | None = None,
        remote_reply_count: int | None = None,
        observed_at: str | None = None,
        observed_from: str | None = None,
    ) -> None:
        if observation_type == "latest_tail":
            self.conn.execute(
                """
                UPDATE threads
                SET remote_last_reply_at_raw = ?,
                    remote_last_reply_at = ?,
                    remote_last_replier = ?,
                    remote_reply_count = ?,
                    remote_observed_at = ?,
                    remote_observed_from = ?
                WHERE tid = ?
                """,
                (remote_last_reply_at_raw, remote_last_reply_at, remote_last_replier, remote_reply_count, observed_at, observed_from, tid),
            )

    def list_floors(self, tid: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM floors WHERE tid = ? ORDER BY floor_no ASC, pid ASC",
            (tid,),
        ).fetchall()

    def count_floors(self, tid: int) -> int:
        row = self.conn.execute("SELECT COUNT(*) AS c FROM floors WHERE tid = ?", (tid,)).fetchone()
        return int(row["c"]) if row is not None else 0

    def count_floors_window(self, tid: int, *, floor_start: int | None = None, floor_end: int | None = None) -> int:
        where = ["tid = ?"]
        params: list[object] = [tid]
        if floor_start is not None:
            where.append("floor_no >= ?")
            params.append(floor_start)
        if floor_end is not None:
            where.append("floor_no <= ?")
            params.append(floor_end)
        row = self.conn.execute(
            f"SELECT COUNT(*) AS c FROM floors WHERE {' AND '.join(where)}",
            params,
        ).fetchone()
        return int(row["c"]) if row is not None else 0

    def list_floors_page(self, tid: int, *, limit: int, offset: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM floors WHERE tid = ? ORDER BY floor_no ASC, pid ASC LIMIT ? OFFSET ?",
            (tid, limit, offset),
        ).fetchall()

    def list_floors_window(
        self,
        tid: int,
        *,
        floor_start: int | None = None,
        floor_end: int | None = None,
        limit: int,
        offset: int = 0,
    ) -> list[sqlite3.Row]:
        where = ["tid = ?"]
        params: list[object] = [tid]
        if floor_start is not None:
            where.append("floor_no >= ?")
            params.append(floor_start)
        if floor_end is not None:
            where.append("floor_no <= ?")
            params.append(floor_end)
        params.extend([limit, offset])
        return self.conn.execute(
            f"SELECT * FROM floors WHERE {' AND '.join(where)} ORDER BY floor_no ASC, pid ASC LIMIT ? OFFSET ?",
            params,
        ).fetchall()
