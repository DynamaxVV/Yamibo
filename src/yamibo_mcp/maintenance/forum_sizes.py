from __future__ import annotations

import json
import sqlite3
import threading
from collections import defaultdict
from pathlib import Path
from typing import Any

from yamibo_mcp.config import Settings
from yamibo_mcp.db.connection import connect
from yamibo_mcp.db.migrations import migrate
from yamibo_mcp.storage.atomic import atomic_write_text
from yamibo_mcp.storage.paths import StoragePaths
from yamibo_mcp.time_utils import utc_now_iso

FORUM_SIZE_CACHE_REFRESH_SECONDS = 12 * 60 * 60
FORUM_SIZE_CACHE_RELATIVE_PATH = Path("cache/forum_sizes.json")
_CACHE_LOCK = threading.Lock()


def forum_size_cache_path(settings: Settings) -> Path:
    return settings.data_dir / FORUM_SIZE_CACHE_RELATIVE_PATH


def read_forum_size_cache(settings: Settings) -> dict[str, Any] | None:
    path = forum_size_cache_path(settings)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    forums = data.get("forums")
    if not isinstance(forums, dict):
        data["forums"] = {}
    return data


def _thread_dir_size(thread_dir: Path) -> int:
    total = 0
    if not thread_dir.exists():
        return 0
    for path in thread_dir.rglob("*"):
        try:
            if path.is_file():
                total += path.stat().st_size
        except FileNotFoundError:
            continue
    return total


def refresh_forum_size_cache(settings: Settings, conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    with _CACHE_LOCK:
        own_conn = conn is None
        if conn is None:
            conn = connect(settings.db_path)
            migrate(conn)
        try:
            rows = conn.execute(
                "SELECT forum_id, tid FROM threads WHERE forum_id IS NOT NULL ORDER BY forum_id, tid"
            ).fetchall()
            thread_ids_by_forum: dict[int, list[int]] = defaultdict(list)
            for row in rows:
                thread_ids_by_forum[int(row["forum_id"])].append(int(row["tid"]))

            forum_rows = conn.execute(
                "SELECT forum_id, name, name_en, content_kind, enabled FROM forums ORDER BY forum_id"
            ).fetchall()
            paths = StoragePaths(settings.data_dir)
            updated_at = utc_now_iso()
            forums: dict[str, dict[str, Any]] = {}
            for forum in forum_rows:
                forum_id = int(forum["forum_id"])
                archive_bytes = sum(_thread_dir_size(paths.thread_dir(tid)) for tid in thread_ids_by_forum.get(forum_id, []))
                forums[str(forum_id)] = {
                    "archive_bytes": archive_bytes,
                    "thread_count": len(thread_ids_by_forum.get(forum_id, [])),
                    "updated_at": updated_at,
                    "name": forum["name"],
                    "name_en": forum["name_en"] if "name_en" in forum.keys() else None,
                    "content_kind": forum["content_kind"],
                    "enabled": bool(forum["enabled"]),
                }

            payload = {
                "version": 1,
                "updated_at": updated_at,
                "forums": forums,
            }
            atomic_write_text(forum_size_cache_path(settings), json.dumps(payload, ensure_ascii=False, indent=2))
            return payload
        finally:
            if own_conn and conn is not None:
                conn.close()
