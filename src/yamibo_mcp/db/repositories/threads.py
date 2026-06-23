from __future__ import annotations

import json
import sqlite3

from yamibo_mcp.domain.models import ThreadSnapshot
from yamibo_mcp.domain.content import build_content_snapshot
from yamibo_mcp.db.repositories.series import SeriesRepository
from yamibo_mcp.time_utils import utc_now_iso
from yamibo_mcp.yamibo.title.normalizer import normalize_display_title, normalize_series_key


class ThreadsRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

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
        now = utc_now_iso()
        max_pid = max((floor.pid for floor in snapshot.floors), default=None)
        content = build_content_snapshot(snapshot, forum_id=forum_id)
        primary_media_type = "image" if any(asset.asset_type == "image" for asset in content.assets) else "text"
        series_repo = SeriesRepository(self.conn)
        from yamibo_mcp.domain.forums import resolve_forum
        profile = resolve_forum(forum_id)
        if profile.default_series_key:
            series_id, needs_series_review = series_repo.resolve_for_forum(forum_id)
        else:
            series_id, needs_series_review = series_repo.resolve_for_title(snapshot.title)
        missing_images_json = json.dumps(missing_image_urls or [], ensure_ascii=False)
        self.conn.execute(
            """
            INSERT INTO threads (
              tid, series_id, page_type, raw_title, display_title, publisher, publisher_uid,
              pub_time, sync_time, last_pid, permission, image_count, context_path,
              archive_status, validation_status, missing_images_json, needs_title_review, needs_series_review,
              forum_id, content_kind, primary_media_type, category
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'valid', ?, ?, ?, ?, ?, ?, ?)
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
              category = excluded.category
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
                1 if snapshot.title.needs_review else 0,
                1 if needs_series_review else 0,
                content.forum_id,
                content.content_kind,
                primary_media_type,
                category,
            ),
        )
        self._upsert_title(snapshot, title_warnings=title_warnings)
        self._upsert_floors(snapshot)
        self._upsert_fts(snapshot)

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
                1 if title.needs_review else 0,
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
                    1 if floor.has_images else 0,
                    floor.quote_text,
                    floor.reply_text,
                ),
            )

    def _upsert_fts(self, snapshot: ThreadSnapshot) -> None:
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
        self.conn.execute("DELETE FROM rag_chunks_fts WHERE chunk_id IN (SELECT chunk_id FROM rag_chunks WHERE tid = ?)", (tid,))
        self.conn.execute("DELETE FROM rag_chunks WHERE tid = ?", (tid,))
        self.conn.execute("DELETE FROM assets WHERE tid = ?", (tid,))
        self.conn.execute("DELETE FROM content_blocks WHERE tid = ?", (tid,))
        self.conn.execute("DELETE FROM catalog WHERE tid = ?", (tid,))
        self.conn.execute("DELETE FROM sync_runs WHERE tid = ?", (tid,))
        self.conn.execute("DELETE FROM floors WHERE tid = ?", (tid,))
        self.conn.execute("DELETE FROM title_parse WHERE tid = ?", (tid,))
        self.conn.execute("DELETE FROM thread_fts WHERE tid = ?", (tid,))
        self.conn.execute("DELETE FROM threads WHERE tid = ?", (tid,))
        return before, {"tid": tid, "deleted": True}

    def get_title_parse(self, tid: int) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM title_parse WHERE tid = ?", (tid,)).fetchone()

    def list_title_review_items(self, *, limit: int = 100) -> list[sqlite3.Row]:
        return self.conn.execute(
            """
            SELECT
              t.tid, t.raw_title, t.display_title, t.series_id,
              t.needs_title_review, t.needs_series_review,
              tp.group_name, tp.author_guess, tp.core_title_guess, tp.series_key,
              tp.title_aliases_json, tp.chapter_name, tp.chapter_index, tp.chapter_index_end, tp.chapter_title,
              tp.subtitle, tp.tags_json, tp.confidence, tp.needs_review
            FROM threads t
            LEFT JOIN title_parse tp ON tp.tid = t.tid
            WHERE t.needs_title_review = 1
               OR t.needs_series_review = 1
               OR tp.needs_review = 1
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
            "UPDATE title_parse SET needs_review = 0 WHERE tid = ?",
            (tid,),
        )
        self.conn.execute(
            "UPDATE threads SET needs_title_review = 0 WHERE tid = ?",
            (tid,),
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
                1 if title.needs_review else 0,
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
                1 if title.needs_review else 0,
                1 if needs_series_review else 0,
                utc_now_iso(),
                tid,
            ),
        )
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
        base_select = """
            SELECT
              t.tid, t.raw_title, t.display_title, t.publisher, t.pub_time, t.sync_time,
              t.archive_status, t.validation_status, t.context_path, t.series_id, t.export_path,
              t.forum_id, t.content_kind, t.category,
              tp.core_title_guess, tp.series_key, tp.chapter_name, tp.chapter_index, tp.chapter_index_end, tp.group_name, tp.author_guess, tp.needs_review,
              (SELECT COUNT(*) FROM floors f WHERE f.tid = t.tid) AS reply_count
            FROM threads t
            LEFT JOIN title_parse tp ON tp.tid = t.tid
        """
        if forum_id is not None:
            return self.conn.execute(
                f"{base_select} WHERE t.forum_id = ? ORDER BY COALESCE(t.sync_time, '') DESC, t.tid DESC LIMIT ?",
                (forum_id, limit),
            ).fetchall()
        return self.conn.execute(
            f"{base_select} ORDER BY COALESCE(t.sync_time, '') DESC, t.tid DESC LIMIT ?",
            (limit,),
        ).fetchall()

    def mark_exported(self, tid: int, export_path: str) -> None:
        cur = self.conn.execute(
            """
            UPDATE threads
            SET is_exported = 1, export_path = ?
            WHERE tid = ?
            """,
            (export_path, tid),
        )
        if cur.rowcount != 1:
            raise ValueError(f"thread not found: {tid}")

    def list_exports(self, *, limit: int = 100) -> list[sqlite3.Row]:
        return self.conn.execute(
            """
            SELECT tid, raw_title, display_title, archive_status, is_exported, export_path
            FROM threads
            WHERE is_exported = 1 OR export_path IS NOT NULL
            ORDER BY tid DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    def search_threads(self, query: str, *, limit: int = 50, forum_id: int | None = None) -> list[sqlite3.Row]:
        normalized = query.strip()
        if not normalized:
            return self.list_threads(limit=limit, forum_id=forum_id)

        keywords = [kw for kw in normalized.split() if kw]

        try:
            fts_terms = " AND ".join(keywords)
            fts_where = "WHERE thread_fts MATCH ?"
            params: list[object] = [fts_terms]
            if forum_id is not None:
                fts_where += " AND t.forum_id = ?"
                params.append(forum_id)
            params.append(limit)
            rows = self.conn.execute(
                f"""
                SELECT
                  t.tid, t.raw_title, t.display_title, t.publisher, t.pub_time, t.sync_time,
                  t.archive_status, t.validation_status, t.context_path, t.series_id, t.export_path,
                  t.forum_id, t.content_kind, t.category,
                  tp.core_title_guess, tp.series_key, tp.chapter_name, tp.needs_review,
                  bm25(thread_fts) AS rank,
                  (SELECT COUNT(*) FROM floors f WHERE f.tid = t.tid) AS reply_count
                FROM thread_fts
                JOIN threads t ON t.tid = thread_fts.tid
                LEFT JOIN title_parse tp ON tp.tid = t.tid
                {fts_where}
                ORDER BY rank
                LIMIT ?
                """,
                params,
            ).fetchall()
            if rows:
                return rows
        except sqlite3.OperationalError:
            pass

        or_cols = "(t.raw_title LIKE ? OR t.display_title LIKE ? OR t.publisher LIKE ? OR tp.core_title_guess LIKE ? OR tp.series_key LIKE ?)"
        and_parts = [f"({or_cols})" for _ in keywords]
        like_params: list[object] = []
        for kw in keywords:
            like = f"%{kw}%"
            like_params.extend([like, like, like, like, like])
        like_where = ""
        if forum_id is not None:
            like_where = " AND t.forum_id = ?"
            like_params.append(forum_id)
        like_params.append(limit)
        return self.conn.execute(
            f"""
            SELECT
              t.tid, t.raw_title, t.display_title, t.publisher, t.pub_time, t.sync_time,
              t.archive_status, t.validation_status, t.context_path, t.series_id, t.export_path,
              t.forum_id, t.content_kind, t.category,
              tp.core_title_guess, tp.series_key, tp.chapter_name, tp.needs_review,
              (SELECT COUNT(*) FROM floors f WHERE f.tid = t.tid) AS reply_count
            FROM threads t
            LEFT JOIN title_parse tp ON tp.tid = t.tid
            WHERE {" AND ".join(and_parts)}{like_where}
            ORDER BY COALESCE(t.sync_time, '') DESC, t.tid DESC
            LIMIT ?
            """,
            like_params,
        ).fetchall()

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
                    "local_reply_count": max(0, floor_count - 1),
                    "local_last_pid": row["last_pid"],
                    "local_last_floor_no": row["last_floor_no"],
                    "local_last_floor_pub_time": last_floor_pub_time,
                    "local_last_reply_at": last_floor_pub_time,
                }
            )
        return result

    def list_floors(self, tid: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM floors WHERE tid = ? ORDER BY floor_no ASC",
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
            "SELECT * FROM floors WHERE tid = ? ORDER BY floor_no ASC LIMIT ? OFFSET ?",
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
            f"SELECT * FROM floors WHERE {' AND '.join(where)} ORDER BY floor_no ASC LIMIT ? OFFSET ?",
            params,
        ).fetchall()
