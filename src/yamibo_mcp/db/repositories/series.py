from __future__ import annotations

import json
import sqlite3

from yamibo_mcp.domain.models import TitleSnapshot
from yamibo_mcp.yamibo.series_matcher import build_series_match_decision
from yamibo_mcp.yamibo.title.normalizer import normalize_series_key


def _loads_list(value: str | None) -> list[str]:
    if not value:
        return []
    loaded = json.loads(value)
    return loaded if isinstance(loaded, list) else []


def _merge_unique(existing: list[str], incoming: list[str]) -> list[str]:
    merged: list[str] = []
    for item in [*existing, *incoming]:
        if item and item not in merged:
            merged.append(item)
    return merged


class SeriesRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def _bool_true_clause(self, column: str) -> str:
        backend = getattr(self.conn, "backend", None)
        return f"{column} IS TRUE" if backend in {"postgres", "postgresql"} else f"{column} = 1"

    def resolve_for_title(self, title: TitleSnapshot) -> tuple[int, bool]:
        decision = build_series_match_decision(title)
        base_key = decision.base_key
        creator_key = decision.creator_key
        alias_keys = decision.alias_keys
        aliases = decision.aliases
        needs_review = decision.needs_review

        existing = self.conn.execute(
            "SELECT * FROM series WHERE series_key = ?",
            (base_key,),
        ).fetchone()
        if existing is not None and self._creator_compatible(existing["creator_key"], creator_key):
            return self._update_series(existing, title, alias_keys, aliases, needs_review, creator_key), needs_review

        effective_key = base_key
        if existing is not None and not self._creator_compatible(existing["creator_key"], creator_key):
            suffix = creator_key or "unknown_creator"
            effective_key = f"{base_key}__author_{suffix}"
            needs_review = True

        existing = self.conn.execute(
            "SELECT * FROM series WHERE series_key = ?",
            (effective_key,),
        ).fetchone()
        if existing is not None:
            return self._update_series(existing, title, alias_keys, aliases, needs_review, creator_key), needs_review

        cur = self.conn.execute(
            """
            INSERT INTO series (
              canonical_title, normalized_title, series_key, alias_keys_json,
              aliases_json, author_guess, creator_key, merge_confidence,
              needs_review
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            RETURNING series_id
            """,
            (
                title.core_title_guess,
                title.normalized_core_title,
                effective_key,
                json.dumps(alias_keys, ensure_ascii=False),
                json.dumps(aliases, ensure_ascii=False),
                title.author_guess,
                creator_key,
                title.confidence,
                needs_review,
            ),
        )
        row = cur.fetchone()
        self.conn.commit()
        return (int(row["series_id"]) if row is not None else 0), needs_review

    def _update_series(
        self,
        row: sqlite3.Row,
        title: TitleSnapshot,
        alias_keys: list[str],
        aliases: list[str],
        needs_review: bool,
        creator_key: str | None,
    ) -> int:
        merged_alias_keys = _merge_unique(_loads_list(row["alias_keys_json"]), alias_keys)
        merged_aliases = _merge_unique(_loads_list(row["aliases_json"]), aliases)
        final_creator_key = row["creator_key"] or creator_key
        final_author = row["author_guess"] or title.author_guess
        final_needs_review = bool(row["needs_review"]) or needs_review
        confidence = max(float(row["merge_confidence"] or 0), title.confidence)
        self.conn.execute(
            """
            UPDATE series
            SET canonical_title = COALESCE(canonical_title, ?),
                normalized_title = COALESCE(normalized_title, ?),
                alias_keys_json = ?,
                aliases_json = ?,
                author_guess = ?,
                creator_key = ?,
                merge_confidence = ?,
                needs_review = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE series_id = ?
            """,
            (
                title.core_title_guess,
                title.normalized_core_title,
                json.dumps(merged_alias_keys, ensure_ascii=False),
                json.dumps(merged_aliases, ensure_ascii=False),
                final_author,
                final_creator_key,
                confidence,
                final_needs_review,
                row["series_id"],
            ),
        )
        return int(row["series_id"])

    def resolve_for_forum(self, forum_id: int) -> tuple[int, bool]:
        from yamibo_mcp.domain.forums import resolve_forum
        profile = resolve_forum(forum_id)
        if not profile.default_series_key:
            raise ValueError(f"forum {forum_id} has no default series")
        existing = self.conn.execute(
            "SELECT * FROM series WHERE series_key = ?",
            (profile.default_series_key,),
        ).fetchone()
        if existing is not None:
            return int(existing["series_id"]), False
        cur = self.conn.execute(
            """
            INSERT INTO series (
              canonical_title, normalized_title, series_key, alias_keys_json,
              aliases_json, author_guess, creator_key, merge_confidence,
              needs_review
            )
            VALUES (?, ?, ?, '[]', '[]', NULL, NULL, 1.0, ?)
            RETURNING series_id
            """,
            (profile.default_series_title, profile.default_series_title, profile.default_series_key, False),
        )
        row = cur.fetchone()
        self.conn.commit()
        return (int(row["series_id"]) if row is not None else 0), False

    def _creator_compatible(self, existing: str | None, incoming: str | None) -> bool:
        return not existing or not incoming or existing == incoming

    def list_series(self, *, limit: int = 100) -> list[sqlite3.Row]:
        return self.conn.execute(
            """
            SELECT *
            FROM (
                SELECT
                  s.*,
                  COUNT(t.tid) AS thread_count,
                  MAX(t.sync_time) AS last_sync_time
                FROM series s
                LEFT JOIN threads t ON t.series_id = s.series_id
                GROUP BY s.series_id
            ) series_rows
            ORDER BY COALESCE(series_rows.last_sync_time, series_rows.updated_at) DESC, series_rows.series_id DESC
            LIMIT ?
            """,
            (limit,),
            ).fetchall()

    def count_series(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) AS c FROM series").fetchone()
        return int(row["c"]) if row is not None else 0

    def get_series(self, series_id: int) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM series WHERE series_id = ?",
            (series_id,),
        ).fetchone()

    def list_series_review_items(self, *, limit: int = 100) -> list[sqlite3.Row]:
        return self.conn.execute(
            f"""
            SELECT
              s.*,
              COUNT(t.tid) AS thread_count
            FROM series s
            LEFT JOIN threads t ON t.series_id = s.series_id
            WHERE {self._bool_true_clause("s.needs_review")}
               OR EXISTS (
                    SELECT 1 FROM threads rt
                    WHERE rt.series_id = s.series_id
                      AND {self._bool_true_clause("rt.needs_series_review")}
                  )
            GROUP BY s.series_id
            HAVING COUNT(t.tid) > 0
            ORDER BY s.updated_at DESC, s.series_id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    def confirm_series_review(self, series_id: int) -> tuple[dict[str, object], dict[str, object]]:
        before_row = self.conn.execute(
            "SELECT * FROM series WHERE series_id = ?",
            (series_id,),
        ).fetchone()
        if before_row is None:
            raise ValueError(f"series not found: {series_id}")
        before = dict(before_row)
        self.conn.execute(
            "UPDATE series SET needs_review = ?, updated_at = CURRENT_TIMESTAMP WHERE series_id = ?",
            (False, series_id),
        )
        self.conn.execute(
            "UPDATE threads SET needs_series_review = ? WHERE series_id = ?",
            (False, series_id),
        )
        after_row = self.conn.execute(
            "SELECT * FROM series WHERE series_id = ?",
            (series_id,),
        ).fetchone()
        return before, dict(after_row)

    def update_series_metadata(
        self,
        series_id: int,
        *,
        canonical_title: str,
        series_key: str,
        author_guess: str | None,
    ) -> None:
        self.conn.execute(
            """
            UPDATE series
            SET canonical_title = ?,
                series_key = ?,
                author_guess = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE series_id = ?
            """,
            (canonical_title, series_key, author_guess, series_id),
        )

    def list_threads_for_series(self, series_id: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            """
            SELECT
              t.tid, t.raw_title, t.display_title, t.publisher, t.sync_time,
              t.archive_status, t.validation_status, t.context_path,
              tp.core_title_guess, tp.chapter_name, tp.chapter_index, tp.chapter_index_end
            FROM threads t
            LEFT JOIN title_parse tp ON tp.tid = t.tid
            WHERE t.series_id = ?
            ORDER BY tp.chapter_index IS NULL, tp.chapter_index, t.tid
            """,
            (series_id,),
        ).fetchall()

    def merge_series(self, source_series_id: int, target_series_id: int) -> tuple[dict[str, object], dict[str, object]]:
        if source_series_id == target_series_id:
            raise ValueError("source and target series must differ")
        source = self.get_series(source_series_id)
        target = self.get_series(target_series_id)
        if source is None or target is None:
            raise ValueError("source or target series not found")
        merged_aliases = _merge_unique(_loads_list(target["aliases_json"]), [source["canonical_title"], *_loads_list(source["aliases_json"])])
        merged_alias_keys = _merge_unique(_loads_list(target["alias_keys_json"]), [source["series_key"], *_loads_list(source["alias_keys_json"])])
        before = {"source": dict(source), "target": dict(target)}
        self.conn.execute(
            """
            UPDATE series
            SET aliases_json = ?, alias_keys_json = ?, needs_review = ?, updated_at = CURRENT_TIMESTAMP
            WHERE series_id = ?
            """,
            (
                json.dumps(merged_aliases, ensure_ascii=False),
                json.dumps(merged_alias_keys, ensure_ascii=False),
                False,
                target_series_id,
            ),
        )
        self.conn.execute(
            "UPDATE threads SET series_id = ?, needs_series_review = ? WHERE series_id = ?",
            (target_series_id, False, source_series_id),
        )
        self.conn.execute("DELETE FROM series WHERE series_id = ?", (source_series_id,))
        after_target = self.get_series(target_series_id)
        return before, {"target": None if after_target is None else dict(after_target)}

    def delete_series(self, series_id: int) -> tuple[dict[str, object], dict[str, object]]:
        series = self.get_series(series_id)
        if series is None:
            raise ValueError(f"series not found: {series_id}")
        thread_count = int(
            self.conn.execute("SELECT COUNT(*) FROM threads WHERE series_id = ?", (series_id,)).fetchone()[0]
        )
        if thread_count > 0:
            raise ValueError(f"series {series_id} still has {thread_count} thread(s)")
        before = dict(series)
        self.conn.execute("DELETE FROM series WHERE series_id = ?", (series_id,))
        return before, {"series_id": series_id, "deleted": True}
