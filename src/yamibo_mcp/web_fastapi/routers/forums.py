from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query

from yamibo_mcp.db.connection import DatabaseConnection
from yamibo_mcp.db.repositories.forums import ForumsRepository
from yamibo_mcp.db.repositories.threads import ThreadsRepository
from yamibo_mcp.maintenance.forum_sizes import read_forum_size_cache, refresh_forum_size_cache
from yamibo_mcp.maintenance.sign_in_cache import (
    SIGN_IN_CACHE_REFRESH_SECONDS,
    is_fresh,
    read_sign_in_cache,
    update_sign_in_cache_account,
)
from yamibo_mcp.web_fastapi.deps import get_conn, get_settings
from yamibo_mcp.web_fastapi.models.requests import SignInRequest
from yamibo_mcp.yamibo.account_pool import borrow_yamibo_client, get_account_identities
from yamibo_mcp.yamibo.client import parse_daily_checkin_profile
from yamibo_mcp.yamibo.proxy_pool import select_random_proxy

router = APIRouter(prefix="/api", tags=["forums"])


def _empty_sign_in_account(account_id: str) -> dict[str, object]:
    return {
        "account_id": account_id,
        "recent_checkin": None,
        "month_days": None,
        "consecutive_days": None,
        "total_days": None,
        "level": None,
        "today_status": "unavailable",
        "error": None,
    }


def _daily_sign_in_stats(settings, account_id: str | None = None) -> dict[str, object]:
    identities = get_account_identities(settings)
    if account_id is not None:
        identities = tuple(identity for identity in identities if identity.account_id == account_id)
    cached_accounts = read_sign_in_cache(settings)
    accounts: list[dict[str, object]] = []
    for identity in identities:
        account: dict[str, object] = _empty_sign_in_account(identity.account_id)
        cached = cached_accounts.get(identity.account_id)
        cached_data = cached.get("data") if isinstance(cached, dict) else None
        if isinstance(cached, dict) and isinstance(cached_data, dict) and is_fresh(cached):
            account.update(cached_data)
            account["cache_status"] = "cached"
            accounts.append(account)
            continue
        try:
            proxy_binding = select_random_proxy(settings)
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
            account["cache_status"] = "fresh"
            update_sign_in_cache_account(settings, identity.account_id, profile)
        except Exception as exc:  # noqa: BLE001 - one unavailable account must not hide other accounts
            account["error"] = str(exc)
        accounts.append(account)

    return {
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "cache_refresh_seconds": SIGN_IN_CACHE_REFRESH_SECONDS,
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
def sign_in_stats(account_id: str | None = Query(default=None), settings=Depends(get_settings)):
    return _daily_sign_in_stats(settings, account_id=account_id)


@router.get("/forums/sign-in-accounts")
def sign_in_accounts(settings=Depends(get_settings)):
    return {
        "accounts": [
            _empty_sign_in_account(identity.account_id)
            for identity in get_account_identities(settings)
        ]
    }


@router.post("/forums/sign-in")
def manual_sign_in(request: SignInRequest, settings=Depends(get_settings)):
    identity = next((item for item in get_account_identities(settings) if item.account_id == request.account_id), None)
    if identity is None:
        raise HTTPException(status_code=404, detail="account not found")
    try:
        proxy_binding = select_random_proxy(settings)
        with borrow_yamibo_client(
            settings,
            account_id=identity.account_id,
            proxy_url=proxy_binding.proxy_url if proxy_binding else None,
        ) as (_, client):
            result = client.sign_daily_checkin()
            profile = parse_daily_checkin_profile(result.html)
            if profile is None:
                try:
                    profile = parse_daily_checkin_profile(client.fetch_daily_checkin_page().html)
                except Exception:  # noqa: BLE001 - the sign-in itself already succeeded
                    profile = None
    except Exception as exc:  # noqa: BLE001 - surface the manual operation failure to the console
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    update_sign_in_cache_account(
        settings,
        identity.account_id,
        profile or {"today_status": "checked"},
        refresh_timestamp=profile is not None,
    )
    return {
        "ok": True,
        "account_id": identity.account_id,
        "today_status": profile.get("today_status") if profile else "checked",
    }


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
