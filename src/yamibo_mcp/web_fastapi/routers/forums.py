from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import quote
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends

from yamibo_mcp.db.connection import DatabaseConnection
from yamibo_mcp.db.repositories.forums import ForumsRepository
from yamibo_mcp.db.repositories.threads import ThreadsRepository
from yamibo_mcp.maintenance.forum_sizes import read_forum_size_cache, refresh_forum_size_cache
from yamibo_mcp.web_fastapi.deps import get_conn, get_settings
from yamibo_mcp.yamibo.account_pool import borrow_yamibo_client, get_account_identities
from yamibo_mcp.yamibo.client import parse_daily_checkin_profile
from yamibo_mcp.yamibo.proxy_pool import select_random_proxy

router = APIRouter(prefix="/api", tags=["forums"])
LOCAL_TIMEZONE = ZoneInfo("Asia/Shanghai")


def _daily_sign_in_stats(settings) -> dict[str, object]:
    identities = get_account_identities(settings)
    accounts: list[dict[str, object]] = []
    for identity in identities:
        account: dict[str, object] = {
            "account_id": identity.account_id,
            "recent_checkin": None,
            "month_days": None,
            "consecutive_days": None,
            "total_days": None,
            "level": None,
            "error": None,
        }
        proxy_binding = select_random_proxy(settings)
        try:
            with borrow_yamibo_client(
                settings,
                account_id=identity.account_id,
                proxy_url=proxy_binding.proxy_url if proxy_binding else None,
            ) as (_, client):
                result = client.fetch_daily_checkin_page()
            profile = parse_daily_checkin_profile(result.html)
            if profile is None:
                raise ValueError("sign-in summary not found")
            account.update(profile)
        except Exception as exc:  # noqa: BLE001 - one unavailable account must not hide other accounts
            account["error"] = str(exc)
        accounts.append(account)

    return {
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "accounts": accounts,
    }


@router.get("/forums")
def list_forums(conn: DatabaseConnection = Depends(get_conn), settings=Depends(get_settings)):
    cache = read_forum_size_cache(settings) or {}
    cache_forums = cache.get("forums") if isinstance(cache, dict) else {}
    size_map = cache_forums if isinstance(cache_forums, dict) else {}
    forums_repo = ForumsRepository(conn)
    threads_repo = ThreadsRepository(conn)
    thread_counts = {int(row["forum_id"]): int(row["cnt"]) for row in threads_repo.count_threads_by_forum()}
    rows = forums_repo.list_forums()
    return [
        {
            "forum_id": r["forum_id"], "name": r["name"],
            "name_en": r["name_en"] if "name_en" in r.keys() else None,
            "content_kind": r["content_kind"],
            "thread_count": thread_counts.get(int(r["forum_id"]), 0),
            "enabled": bool(r["enabled"]),
            "archive_size_bytes": size_map.get(str(r["forum_id"]), {}).get("archive_bytes")
            if isinstance(size_map.get(str(r["forum_id"])), dict) else None,
            "archive_size_updated_at": size_map.get(str(r["forum_id"]), {}).get("updated_at")
            if isinstance(size_map.get(str(r["forum_id"])), dict) else None,
        }
        for r in rows
    ]


@router.get("/forums/sign-in-stats")
def sign_in_stats(conn: DatabaseConnection = Depends(get_conn), settings=Depends(get_settings)):
    del conn
    return _daily_sign_in_stats(settings)


@router.post("/forums/refresh-size-cache")
def refresh_forum_size_cache_route(conn: DatabaseConnection = Depends(get_conn), settings=Depends(get_settings)):
    payload = refresh_forum_size_cache(settings, conn)
    return {
        "ok": True,
        "updated_at": payload.get("updated_at"),
        "forum_count": len(payload.get("forums", {})) if isinstance(payload.get("forums"), dict) else 0,
    }


@router.get("/fonts")
def fonts_list(settings=Depends(get_settings)):
    font_root = settings.data_dir / "fonts"
    if not font_root.exists():
        return []
    fonts = []
    for path in sorted(font_root.iterdir()):
        if not path.is_file():
            continue
        if path.suffix.lower() not in {".ttf", ".otf", ".woff", ".woff2"}:
            continue
        label = path.stem
        family = f"YamiboReading-{len(fonts)}"
        fonts.append({
            "name": path.name, "label": label, "family": family,
            "url": f"/fonts/{quote(path.name)}",
        })
    return fonts
