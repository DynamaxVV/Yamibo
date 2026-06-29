from __future__ import annotations

from urllib.parse import quote

from yamibo_mcp.db.repositories.forums import ForumsRepository
from yamibo_mcp.db.repositories.threads import ThreadsRepository
from yamibo_mcp.maintenance.forum_sizes import read_forum_size_cache, refresh_forum_size_cache
from ._helpers import json_response


def handle_forums_list(handler, conn, settings):
    cache = read_forum_size_cache(settings) or {}
    cache_forums = cache.get("forums") if isinstance(cache, dict) else {}
    size_map = cache_forums if isinstance(cache_forums, dict) else {}
    forums_repo = ForumsRepository(conn)
    threads_repo = ThreadsRepository(conn)
    thread_counts = {int(row["forum_id"]): int(row["cnt"]) for row in threads_repo.count_threads_by_forum()}
    rows = forums_repo.list_forums()
    json_response(handler, [
        {"forum_id": r["forum_id"], "name": r["name"], "name_en": r["name_en"] if "name_en" in r.keys() else None,
         "content_kind": r["content_kind"],
         "thread_count": thread_counts.get(int(r["forum_id"]), 0), "enabled": bool(r["enabled"]),
         "archive_size_bytes": size_map.get(str(r["forum_id"]), {}).get("archive_bytes") if isinstance(size_map.get(str(r["forum_id"])), dict) else None,
         "archive_size_updated_at": size_map.get(str(r["forum_id"]), {}).get("updated_at") if isinstance(size_map.get(str(r["forum_id"])), dict) else None}
        for r in rows
    ])


def handle_refresh_forum_size_cache(handler, conn, settings):
    payload = refresh_forum_size_cache(settings, conn)
    json_response(handler, {
        "ok": True,
        "updated_at": payload.get("updated_at"),
        "forum_count": len(payload.get("forums", {})) if isinstance(payload.get("forums"), dict) else 0,
    })


def handle_fonts_list(handler, settings):
    font_root = settings.data_dir / "fonts"
    if not font_root.exists():
        json_response(handler, [])
        return
    fonts = []
    for path in sorted(font_root.iterdir()):
        if not path.is_file():
            continue
        if path.suffix.lower() not in {".ttf", ".otf", ".woff", ".woff2"}:
            continue
        label = path.stem
        family = f"YamiboReading-{len(fonts)}"
        fonts.append({
            "name": path.name,
            "label": label,
            "family": family,
            "url": f"/fonts/{quote(path.name)}",
        })
    json_response(handler, fonts)
