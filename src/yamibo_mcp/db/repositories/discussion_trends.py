from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from yamibo_mcp.time_utils import utc_now_iso


@dataclass(slots=True)
class DiscussionIndexRun:
    run_id: str
    forum_id: int
    start_date: str
    end_date: str
    version: str
    status: str
    started_at: str
    completed_at: str | None
    superseded_at: str | None
    metrics_json: dict[str, Any]
    warnings_json: list[Any]
    error_json: dict[str, Any]
    created_at: str
    updated_at: str


@dataclass(slots=True)
class DiscussionCurrentIndex:
    forum_id: int
    start_date: str
    end_date: str
    version: str
    current_run_id: str
    updated_at: str


def ensure_postgres(conn: Any) -> None:
    backend = getattr(conn, "backend", None)
    if backend not in {"postgres", "postgresql"}:
        raise ValueError("discussion trends require postgres backend")


class DiscussionTrendRepository:
    def __init__(self, conn: Any):
        ensure_postgres(conn)
        self.conn = conn

    def create_run(
        self,
        *,
        run_id: str,
        forum_id: int,
        start_date: str,
        end_date: str,
        version: str,
        status: str,
        metrics_json: dict[str, Any] | None = None,
        warnings_json: list[Any] | None = None,
        error_json: dict[str, Any] | None = None,
    ) -> DiscussionIndexRun:
        now = utc_now_iso()
        self.conn.execute(
            """
            INSERT INTO discussion_index_runs (
              run_id, forum_id, start_date, end_date, version, status,
              started_at, completed_at, superseded_at, metrics_json, warnings_json, error_json,
              created_at, updated_at
            ) VALUES (
              :run_id, :forum_id, :start_date, :end_date, :version, :status,
              :started_at, NULL, NULL, :metrics_json, :warnings_json, :error_json,
              :created_at, :updated_at
            )
            """,
            {
                "run_id": run_id,
                "forum_id": forum_id,
                "start_date": start_date,
                "end_date": end_date,
                "version": version,
                "status": status,
                "started_at": now,
                "metrics_json": _encode_jsonb(metrics_json),
                "warnings_json": _encode_jsonb_list(warnings_json),
                "error_json": _encode_jsonb(error_json),
                "created_at": now,
                "updated_at": now,
            },
        )
        _commit_if_needed(self.conn)
        return self.get_run(run_id)

    def mark_run_succeeded(
        self,
        run_id: str,
        *,
        metrics_json: dict[str, Any] | None = None,
        warnings_json: list[Any] | None = None,
    ) -> DiscussionIndexRun:
        now = utc_now_iso()
        self.conn.execute(
            """
            UPDATE discussion_index_runs
            SET status = 'succeeded',
                completed_at = :completed_at,
                updated_at = :updated_at,
                metrics_json = COALESCE(:metrics_json, metrics_json),
                warnings_json = COALESCE(:warnings_json, warnings_json)
            WHERE run_id = :run_id
            """,
            {
                "run_id": run_id,
                "completed_at": now,
                "updated_at": now,
                "metrics_json": _encode_jsonb(metrics_json) if metrics_json is not None else None,
                "warnings_json": _encode_jsonb_list(warnings_json) if warnings_json is not None else None,
            },
        )
        _commit_if_needed(self.conn)
        return self.get_run(run_id)

    def mark_run_failed(
        self,
        run_id: str,
        *,
        error_json: dict[str, Any] | None = None,
        warnings_json: list[Any] | None = None,
    ) -> DiscussionIndexRun:
        now = utc_now_iso()
        self.conn.execute(
            """
            UPDATE discussion_index_runs
            SET status = 'failed',
                completed_at = :completed_at,
                updated_at = :updated_at,
                error_json = COALESCE(:error_json, error_json),
                warnings_json = COALESCE(:warnings_json, warnings_json)
            WHERE run_id = :run_id
            """,
            {
                "run_id": run_id,
                "completed_at": now,
                "updated_at": now,
                "error_json": _encode_jsonb(error_json) if error_json is not None else None,
                "warnings_json": _encode_jsonb_list(warnings_json) if warnings_json is not None else None,
            },
        )
        _commit_if_needed(self.conn)
        return self.get_run(run_id)

    def mark_run_superseded(self, run_id: str, *, superseded_at: str | None = None) -> None:
        """Stamp a previously-current run as superseded. Idempotent."""
        now = superseded_at or utc_now_iso()
        self.conn.execute(
            """
            UPDATE discussion_index_runs
            SET superseded_at = COALESCE(superseded_at, :superseded_at),
                updated_at = :updated_at
            WHERE run_id = :run_id
            """,
            {"run_id": run_id, "superseded_at": now, "updated_at": now},
        )
        _commit_if_needed(self.conn)

    def set_current_run(
        self,
        *,
        forum_id: int,
        start_date: str,
        end_date: str,
        version: str,
        current_run_id: str,
    ) -> None:
        now = utc_now_iso()
        self.conn.execute(
            """
            INSERT INTO discussion_current_indexes (
              forum_id, start_date, end_date, version, current_run_id, updated_at
            ) VALUES (:forum_id, :start_date, :end_date, :version, :current_run_id, :updated_at)
            ON CONFLICT (forum_id, start_date, end_date, version) DO UPDATE SET
              current_run_id = EXCLUDED.current_run_id,
              updated_at = EXCLUDED.updated_at
            """,
            {
                "forum_id": forum_id,
                "start_date": start_date,
                "end_date": end_date,
                "version": version,
                "current_run_id": current_run_id,
                "updated_at": now,
            },
        )
        _commit_if_needed(self.conn)

    def get_current_run(
        self,
        *,
        forum_id: int,
        start_date: str,
        end_date: str,
        version: str,
    ) -> DiscussionIndexRun | None:
        row = self.conn.execute(
            """
            SELECT r.*
            FROM discussion_current_indexes c
            JOIN discussion_index_runs r ON r.run_id = c.current_run_id
            WHERE c.forum_id = :forum_id
              AND c.start_date = :start_date
              AND c.end_date = :end_date
              AND c.version = :version
            """,
            {
                "forum_id": forum_id,
                "start_date": start_date,
                "end_date": end_date,
                "version": version,
            },
        ).one_or_none()
        return None if row is None else self._row_to_run(row)

    def get_run(self, run_id: str) -> DiscussionIndexRun | None:
        row = self.conn.execute(
            "SELECT * FROM discussion_index_runs WHERE run_id = :run_id",
            {"run_id": run_id},
        ).one_or_none()
        return None if row is None else self._row_to_run(row)

    def acquire_window_lock(self, *, forum_id: int, start_date: str, end_date: str, version: str) -> None:
        lock_key = f"discussion-trends:{forum_id}:{start_date}:{end_date}:{version}"
        self.conn.execute("SELECT pg_advisory_xact_lock(hashtext(:lock_key))", {"lock_key": lock_key})

    def replace_topics(self, run_id: str, topics: list[dict[str, Any]]) -> dict[str, int]:
        """Replace topics for run_id. Returns {topic_key: topic_id} map for use in dependent inserts."""
        self.conn.execute(
            "DELETE FROM discussion_topics WHERE run_id = :run_id",
            {"run_id": run_id},
        )
        if not topics:
            _commit_if_needed(self.conn)
            return {}
        rows_for_insert = [
            {
                **topic,
                "run_id": run_id,
                "metadata_json": _encode_jsonb(topic.get("metadata_json")),
            }
            for topic in topics
        ]
        self.conn.executemany(
            """
            INSERT INTO discussion_topics (
              run_id, forum_id, topic_key, topic_label, topic_kind, topic_source,
              confidence, metadata_json
            ) VALUES (
              :run_id, :forum_id, :topic_key, :topic_label, :topic_kind, :topic_source,
              :confidence, :metadata_json
            )
            """,
            rows_for_insert,
        )
        _commit_if_needed(self.conn)
        rows = self.conn.execute(
            "SELECT topic_id, topic_key FROM discussion_topics WHERE run_id = :run_id",
            {"run_id": run_id},
        ).fetchall()
        return {row["topic_key"]: int(row["topic_id"]) for row in rows}

    def replace_topic_assignments(self, run_id: str, assignments: list[dict[str, Any]]) -> None:
        self.conn.execute(
            "DELETE FROM discussion_topic_assignments WHERE run_id = :run_id",
            {"run_id": run_id},
        )
        if not assignments:
            _commit_if_needed(self.conn)
            return
        rows_for_insert = [{**row, "run_id": run_id} for row in assignments]
        self.conn.executemany(
            """
            INSERT INTO discussion_topic_assignments (
              run_id, topic_id, forum_id, tid, pid, floor_no, bucket_date,
              assignment_score, assignment_source
            ) VALUES (
              :run_id, :topic_id, :forum_id, :tid, :pid, :floor_no, :bucket_date,
              :assignment_score, :assignment_source
            )
            """,
            rows_for_insert,
        )
        _commit_if_needed(self.conn)

    def replace_partition_daily(self, run_id: str, rows: list[dict[str, Any]]) -> None:
        self.conn.execute(
            "DELETE FROM discussion_partition_daily WHERE run_id = :run_id",
            {"run_id": run_id},
        )
        if not rows:
            _commit_if_needed(self.conn)
            return
        rows_for_insert = [
            {**row, "run_id": run_id, "metrics_json": _encode_jsonb(row.get("metrics_json"))}
            for row in rows
        ]
        self.conn.executemany(
            """
            INSERT INTO discussion_partition_daily (
              run_id, forum_id, bucket_date, thread_count, post_count,
              active_user_count, new_thread_count, reply_count, metrics_json
            ) VALUES (
              :run_id, :forum_id, :bucket_date, :thread_count, :post_count,
              :active_user_count, :new_thread_count, :reply_count, :metrics_json
            )
            """,
            rows_for_insert,
        )
        _commit_if_needed(self.conn)

    def replace_topic_daily(self, run_id: str, rows: list[dict[str, Any]]) -> None:
        self.conn.execute(
            "DELETE FROM discussion_topic_daily WHERE run_id = :run_id",
            {"run_id": run_id},
        )
        if not rows:
            _commit_if_needed(self.conn)
            return
        rows_for_insert = [
            {**row, "run_id": run_id, "metrics_json": _encode_jsonb(row.get("metrics_json"))}
            for row in rows
        ]
        self.conn.executemany(
            """
            INSERT INTO discussion_topic_daily (
              run_id, topic_id, forum_id, bucket_date, thread_count, post_count,
              active_user_count, assignment_count, evidence_count, metrics_json
            ) VALUES (
              :run_id, :topic_id, :forum_id, :bucket_date, :thread_count, :post_count,
              :active_user_count, :assignment_count, :evidence_count, :metrics_json
            )
            """,
            rows_for_insert,
        )
        _commit_if_needed(self.conn)

    def replace_user_daily(self, run_id: str, rows: list[dict[str, Any]]) -> None:
        self.conn.execute(
            "DELETE FROM discussion_user_daily WHERE run_id = :run_id",
            {"run_id": run_id},
        )
        if not rows:
            _commit_if_needed(self.conn)
            return
        rows_for_insert = [
            {**row, "run_id": run_id, "metrics_json": _encode_jsonb(row.get("metrics_json"))}
            for row in rows
        ]
        self.conn.executemany(
            """
            INSERT INTO discussion_user_daily (
              run_id, forum_id, bucket_date, user_key, display_name,
              thread_count, post_count, topic_count, metrics_json
            ) VALUES (
              :run_id, :forum_id, :bucket_date, :user_key, :display_name,
              :thread_count, :post_count, :topic_count, :metrics_json
            )
            """,
            rows_for_insert,
        )
        _commit_if_needed(self.conn)

    def list_window_runs(
        self,
        *,
        forum_id: int,
        start_date: str,
        end_date: str,
        version: str,
    ) -> list[DiscussionIndexRun]:
        rows = self.conn.execute(
            """
            SELECT *
            FROM discussion_index_runs
            WHERE forum_id = :forum_id
              AND start_date = :start_date
              AND end_date = :end_date
              AND version = :version
            ORDER BY COALESCE(completed_at, started_at) DESC, run_id DESC
            """,
            {
                "forum_id": forum_id,
                "start_date": start_date,
                "end_date": end_date,
                "version": version,
            },
        ).fetchall()
        return [self._row_to_run(row) for row in rows]

    def apply_retention(
        self,
        *,
        forum_id: int,
        start_date: str,
        end_date: str,
        version: str,
        current_run_id: str,
        keep_succeeded_runs: int,
    ) -> dict[str, int]:
        """Drop per-run detail tables for runs outside the retention window.

        Rules (per Step 03 spec):
        - current run: never touched.
        - most recent `keep_succeeded_runs` succeeded runs (excluding current): preserved.
        - older succeeded runs: delete from discussion_topic_assignments + discussion_rag_chunk_topics.
        - failed/cancelled runs: delete from both detail tables.

        Daily mart tables, run metadata, and report artifacts are NOT touched.

        Returns a counts dict {deleted_assignments, deleted_rag_chunk_topics, runs_pruned}.
        """
        if keep_succeeded_runs < 0:
            raise ValueError("keep_succeeded_runs must be >= 0")
        runs = self.list_window_runs(
            forum_id=forum_id,
            start_date=start_date,
            end_date=end_date,
            version=version,
        )
        # Build the keep set: current + (keep_succeeded_runs - 1) most recent succeeded
        # (the -1 because the current run is itself a succeeded run that must be preserved).
        keep_ids: set[str] = {current_run_id}
        if keep_succeeded_runs > 1:
            for run in runs:
                if run.run_id == current_run_id:
                    continue
                if run.status != "succeeded":
                    continue
                keep_ids.add(run.run_id)
                if len(keep_ids) >= keep_succeeded_runs:
                    break

        deleted_assignments = 0
        deleted_rag_chunk_topics = 0
        runs_pruned = 0
        for run in runs:
            if run.run_id in keep_ids:
                continue
            # Anything not in the keep set: failed/cancelled runs OR
            # succeeded runs older than the keep window.
            deleted_assignments += self._delete_run_assignments(run.run_id)
            deleted_rag_chunk_topics += self._delete_run_rag_chunk_topics(run.run_id)
            runs_pruned += 1
        return {
            "deleted_assignments": deleted_assignments,
            "deleted_rag_chunk_topics": deleted_rag_chunk_topics,
            "runs_pruned": runs_pruned,
        }

    def _delete_run_assignments(self, run_id: str) -> int:
        result = self.conn.execute(
            "DELETE FROM discussion_topic_assignments WHERE run_id = :run_id",
            {"run_id": run_id},
        )
        _commit_if_needed(self.conn)
        return int(getattr(result, "rowcount", 0) or 0)

    def _delete_run_rag_chunk_topics(self, run_id: str) -> int:
        result = self.conn.execute(
            "DELETE FROM discussion_rag_chunk_topics WHERE run_id = :run_id",
            {"run_id": run_id},
        )
        _commit_if_needed(self.conn)
        return int(getattr(result, "rowcount", 0) or 0)

    def get_partition_daily(self, run_id: str, forum_id: int) -> list[dict[str, Any]]:
        """Return partition_daily rows for a run, ordered by bucket_date."""
        rows = self.conn.execute(
            """
            SELECT bucket_date, thread_count, post_count, active_user_count,
                   new_thread_count, reply_count, metrics_json
            FROM discussion_partition_daily
            WHERE run_id = :run_id AND forum_id = :forum_id
            ORDER BY bucket_date
            """,
            {"run_id": run_id, "forum_id": forum_id},
        ).fetchall()
        return [
            {
                "bucket_date": str(row["bucket_date"]),
                "thread_count": int(row["thread_count"]),
                "post_count": int(row["post_count"]),
                "active_user_count": int(row["active_user_count"]),
                "new_thread_count": int(row["new_thread_count"]),
                "reply_count": int(row["reply_count"]),
                "metrics_json": _decode_jsonb(row["metrics_json"], default={}),
            }
            for row in rows
        ]

    def get_topic_daily_with_labels(
        self, run_id: str, forum_id: int
    ) -> list[dict[str, Any]]:
        """Return topic_daily rows joined with discussion_topics, ordered by topic_id, bucket_date."""
        rows = self.conn.execute(
            """
            SELECT t.topic_id, t.topic_key, t.topic_label, t.topic_kind,
                   t.topic_source, t.confidence,
                   td.bucket_date, td.thread_count, td.post_count,
                   td.active_user_count, td.assignment_count, td.evidence_count,
                   td.metrics_json
            FROM discussion_topic_daily td
            JOIN discussion_topics t ON t.topic_id = td.topic_id
            WHERE td.run_id = :run_id AND td.forum_id = :forum_id
            ORDER BY t.topic_id, td.bucket_date
            """,
            {"run_id": run_id, "forum_id": forum_id},
        ).fetchall()
        return [
            {
                "topic_id": int(row["topic_id"]),
                "topic_key": str(row["topic_key"]),
                "topic_label": str(row["topic_label"]),
                "topic_kind": str(row["topic_kind"]),
                "topic_source": str(row["topic_source"]),
                "confidence": float(row["confidence"]) if row["confidence"] is not None else None,
                "bucket_date": str(row["bucket_date"]),
                "thread_count": int(row["thread_count"]),
                "post_count": int(row["post_count"]),
                "active_user_count": int(row["active_user_count"]),
                "assignment_count": int(row["assignment_count"]),
                "evidence_count": int(row["evidence_count"]),
                "metrics_json": _decode_jsonb(row["metrics_json"], default={}),
            }
            for row in rows
        ]

    def get_user_daily(
        self, run_id: str, forum_id: int
    ) -> list[dict[str, Any]]:
        """Return user_daily rows for a run, ordered by user_key, bucket_date."""
        rows = self.conn.execute(
            """
            SELECT user_key, display_name, bucket_date, thread_count,
                   post_count, topic_count, metrics_json
            FROM discussion_user_daily
            WHERE run_id = :run_id AND forum_id = :forum_id
            ORDER BY user_key, bucket_date
            """,
            {"run_id": run_id, "forum_id": forum_id},
        ).fetchall()
        return [
            {
                "user_key": str(row["user_key"]),
                "display_name": str(row["display_name"]) if row["display_name"] is not None else None,
                "bucket_date": str(row["bucket_date"]),
                "thread_count": int(row["thread_count"]),
                "post_count": int(row["post_count"]),
                "topic_count": int(row["topic_count"]),
                "metrics_json": _decode_jsonb(row["metrics_json"], default={}),
            }
            for row in rows
        ]

    def get_report_runs(self, run_id: str) -> list[dict[str, Any]]:
        """Return report runs for a given index run_id."""
        rows = self.conn.execute(
            """
            SELECT report_kind, report_json, report_markdown, artifacts_json, created_at
            FROM discussion_report_runs
            WHERE run_id = :run_id
            ORDER BY report_kind
            """,
            {"run_id": run_id},
        ).fetchall()
        return [
            {
                "report_kind": str(row["report_kind"]),
                "report_json": _decode_jsonb(row["report_json"], default={}),
                "report_markdown": str(row["report_markdown"]),
                "artifacts_json": _decode_jsonb(row["artifacts_json"], default={}),
                "created_at": str(row["created_at"]),
            }
            for row in rows
        ]

    def insert_report_run(
        self,
        *,
        run_id: str,
        report_kind: str,
        report_json: dict[str, Any],
        report_markdown: str,
        artifacts_json: dict[str, Any] | None = None,
    ) -> None:
        now = utc_now_iso()
        self.conn.execute(
            """
            INSERT INTO discussion_report_runs (
              run_id, report_kind, report_json, report_markdown, artifacts_json,
              created_at, updated_at
            ) VALUES (
              :run_id, :report_kind, :report_json, :report_markdown, :artifacts_json,
              :created_at, :updated_at
            )
            ON CONFLICT (run_id, report_kind) DO UPDATE SET
              report_json = EXCLUDED.report_json,
              report_markdown = EXCLUDED.report_markdown,
              artifacts_json = EXCLUDED.artifacts_json,
              updated_at = EXCLUDED.updated_at
            """,
            {
                "run_id": run_id,
                "report_kind": report_kind,
                "report_json": _encode_jsonb(report_json),
                "report_markdown": report_markdown,
                "artifacts_json": _encode_jsonb(artifacts_json),
                "created_at": now,
                "updated_at": now,
            },
        )
        _commit_if_needed(self.conn)

    def get_topics_for_run(self, run_id: str) -> list[dict[str, Any]]:
        """Return topic metadata rows for a run (no daily aggregation)."""
        rows = self.conn.execute(
            """
            SELECT topic_id, topic_key, topic_label, topic_kind, topic_source,
                   confidence, metadata_json
            FROM discussion_topics
            WHERE run_id = :run_id
            ORDER BY topic_id
            """,
            {"run_id": run_id},
        ).fetchall()
        return [
            {
                "topic_id": int(row["topic_id"]),
                "topic_key": str(row["topic_key"]),
                "topic_label": str(row["topic_label"]),
                "topic_kind": str(row["topic_kind"]),
                "topic_source": str(row["topic_source"]),
                "confidence": float(row["confidence"]) if row["confidence"] is not None else None,
                "metadata_json": _decode_jsonb(row["metadata_json"], default={}),
            }
            for row in rows
        ]

    def get_topic_floor_evidence(
        self,
        run_id: str,
        topic_id: int,
        forum_id: int,
        top_k: int = 10,
    ) -> list[dict[str, Any]]:
        """SQL fallback: representative floors for a topic from topic_assignments.

        Joins discussion_topic_assignments → floors → threads.
        Topic assignments may be floor-level (pid set) or thread-level
        (pid NULL); thread-level assignments select representative floors from
        the assigned thread.
        Prefers floors with quote/reply content, then by thread post_count,
        then spreads across distinct threads.
        """
        rows = self.conn.execute(
            """
            SELECT DISTINCT ON (a.tid)
                   a.tid, f.pid, f.floor_no,
                   f.content, f.publisher AS floor_publisher, f.pub_time AS floor_pub_time,
                   f.quote_text, f.reply_text,
                   t.display_title, t.publisher AS thread_publisher
            FROM discussion_topic_assignments a
            JOIN floors f ON (
                (a.pid IS NOT NULL AND f.pid = a.pid)
                OR (a.pid IS NULL AND f.tid = a.tid)
            )
            JOIN threads t ON t.tid = a.tid
            WHERE a.run_id = :run_id
              AND a.topic_id = :topic_id
              AND a.forum_id = :forum_id
            ORDER BY a.tid,
                     (CASE WHEN f.quote_text IS NOT NULL OR f.reply_text IS NOT NULL THEN 0 ELSE 1 END),
                     a.assignment_score DESC NULLS LAST
            LIMIT :top_k
            """,
            {"run_id": run_id, "topic_id": topic_id, "forum_id": forum_id, "top_k": top_k},
        ).fetchall()

        results: list[dict[str, Any]] = []
        for row in rows:
            text = str(row["content"] or "")
            snippet = text[:220]
            publisher = row["floor_publisher"] or row["thread_publisher"] or None
            pub_time = row["floor_pub_time"] or None
            source_uri = f"yamibo://threads/{row['tid']}#floor={row['floor_no']}"
            results.append({
                "tid": int(row["tid"]),
                "pid": int(row["pid"]),
                "floor_no": int(row["floor_no"]),
                "display_title": str(row["display_title"]) if row["display_title"] else None,
                "publisher": str(publisher) if publisher else None,
                "pub_time": str(pub_time) if pub_time else None,
                "snippet": snippet,
                "source_uri": source_uri,
                "score": 0.0,
                "evidence_source": "sql",
            })
        return results

    def search_forum_floor_snippets(
        self,
        *,
        forum_id: int,
        query: str,
        top_k: int = 10,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> list[dict[str, Any]]:
        """PostgreSQL-only fallback search over archived floor text.

        This does not depend on rag_chunks. It searches raw thread/floor text so
        forum evidence and research reports can still function when RAG coverage
        is partial or missing.
        """
        terms = [term.strip() for term in query.replace("，", " ").replace("、", " ").split() if term.strip()]
        if not terms:
            return []

        pg_params: dict[str, Any] = {
            "forum_id": forum_id,
            "limit": top_k,
        }
        match_clauses: list[str] = []
        score_parts: list[str] = []
        for idx, term in enumerate(terms):
            key = f"pattern_{idx}"
            pg_params[key] = f"%{term}%"
            field_match = (
                f"(COALESCE(t.raw_title, '') ILIKE :{key} "
                f"OR COALESCE(t.display_title, '') ILIKE :{key} "
                f"OR COALESCE(f.content, '') ILIKE :{key} "
                f"OR COALESCE(f.quote_text, '') ILIKE :{key} "
                f"OR COALESCE(f.reply_text, '') ILIKE :{key})"
            )
            match_clauses.append(field_match)
            score_parts.append(f"CASE WHEN {field_match} THEN 1 ELSE 0 END")

        date_filters = ""
        if start_date is not None:
            pg_params["start_date"] = start_date
            date_filters += " AND DATE(COALESCE(f.pub_time, t.pub_time)) >= :start_date"
        if end_date is not None:
            pg_params["end_date"] = end_date
            date_filters += " AND DATE(COALESCE(f.pub_time, t.pub_time)) <= :end_date"

        rows = self.conn.execute(
            f"""
            SELECT
              t.tid,
              f.pid,
              f.floor_no,
              COALESCE(t.display_title, t.raw_title) AS display_title,
              COALESCE(f.publisher, t.publisher) AS publisher,
              COALESCE(f.pub_time, t.pub_time) AS pub_time,
              COALESCE(f.content, '') AS content,
              ({' + '.join(score_parts)})::float AS keyword_score
            FROM floors f
            JOIN threads t ON t.tid = f.tid
            WHERE t.forum_id = :forum_id
              AND ({' OR '.join(match_clauses)})
              {date_filters}
            ORDER BY keyword_score DESC,
                     COALESCE(f.pub_time, t.pub_time) DESC NULLS LAST,
                     f.floor_no ASC
            LIMIT :limit
            """,
            pg_params,
        ).fetchall()

        return [
            {
                "tid": int(row["tid"]),
                "pid": int(row["pid"]),
                "floor_no": int(row["floor_no"]),
                "display_title": str(row["display_title"]) if row["display_title"] else None,
                "publisher": str(row["publisher"]) if row["publisher"] else None,
                "pub_time": str(row["pub_time"]) if row["pub_time"] else None,
                "snippet": str(row["content"] or "")[:220],
                "source_uri": f"yamibo://threads/{row['tid']}#floor={row['floor_no']}",
                "score": float(row["keyword_score"] or 0.0),
                "evidence_source": "sql",
            }
            for row in rows
        ]

    def _row_to_run(self, row: Any) -> DiscussionIndexRun:
        return DiscussionIndexRun(
            run_id=str(row["run_id"]),
            forum_id=int(row["forum_id"]),
            start_date=str(row["start_date"]),
            end_date=str(row["end_date"]),
            version=str(row["version"]),
            status=str(row["status"]),
            started_at=str(row["started_at"]),
            completed_at=None if row["completed_at"] is None else str(row["completed_at"]),
            superseded_at=None if row["superseded_at"] is None else str(row["superseded_at"]),
            metrics_json=_decode_jsonb(row["metrics_json"], default={}),
            warnings_json=_decode_jsonb(row["warnings_json"], default=[]),
            error_json=_decode_jsonb(row["error_json"], default={}),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )


def _commit_if_needed(conn: Any) -> None:
    in_transaction = getattr(conn, "in_transaction", None)
    if callable(in_transaction) and in_transaction():
        return
    conn.commit()


def _encode_jsonb(value: Any) -> str:
    """Encode Python dict/list for a JSONB bind. None falls back to empty container.

    The discussion_index_runs JSONB columns are NOT NULL, so we coerce None
    callers to {} / [] rather than passing SQL NULL.
    """
    if value is None:
        return "{}"  # caller chose None → empty dict literal; lists use _encode_jsonb_list.
    return json.dumps(value, ensure_ascii=False, default=str)


def _encode_jsonb_list(value: Any) -> str:
    if value is None:
        return "[]"
    return json.dumps(value, ensure_ascii=False, default=str)


def _decode_jsonb(value: Any, *, default: Any) -> Any:
    """Decode a JSONB value coming back from the DB. JSONB columns may return:
    - dict/list (psycopg with native JSONB adapter on PostgreSQL),
    - str (text protocol or unit test fakes that don't decode).
    """
    if value is None:
        return default
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return default
    return value
