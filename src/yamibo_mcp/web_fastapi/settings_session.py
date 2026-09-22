"""Browser-session authentication, independent of the selected chat backend."""
from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time

from fastapi import APIRouter, Request, HTTPException
from starlette.responses import JSONResponse

COOKIE = "yamibo_settings_session"
router = APIRouter(prefix="/api/settings", tags=["settings"])


class SettingsSessions:
    def __init__(self, settings):
        self.token = getattr(settings, "settings_access_token", None) or getattr(settings, "chat_access_token", None)
        self.failures: dict[str, list[float]] = {}
        self.lock = threading.Lock()

    def authenticated(self, request: Request) -> bool:
        if not self.token:
            return bool(request.client and request.client.host in {"127.0.0.1", "::1", "testclient"})
        value = request.cookies.get(COOKIE, "")
        try:
            nonce, signature = value.rsplit(".", 1)
        except ValueError:
            return False
        expected = hmac.new(self.token.encode(), nonce.encode(), hashlib.sha256).hexdigest()
        return bool(nonce) and hmac.compare_digest(signature, expected)


def install_settings_sessions(app, settings):
    app.state.settings_sessions = SettingsSessions(settings)

    @app.middleware("http")
    async def protect_settings(request: Request, call_next):
        path = request.url.path
        if path == "/api/settings" or path.startswith("/api/settings/"):
            if request.method not in {"GET", "HEAD", "OPTIONS"}:
                origin = request.headers.get("origin")
                allowed = {f"{request.url.scheme}://{request.url.netloc}"}
                # The same host may terminate TLS in front of the application.
                allowed.add(f"https://{request.url.netloc}")
                if origin not in allowed or request.headers.get("sec-fetch-site") == "cross-site":
                    return JSONResponse({"error": {"code": "SETTINGS_ORIGIN_DENIED", "message": "跨站请求被拒绝"}}, status_code=403)
            if path != "/api/settings/session" and not app.state.settings_sessions.authenticated(request):
                return JSONResponse({"error": {"code": "SETTINGS_AUTH_REQUIRED", "message": "请验证设置访问令牌"}}, status_code=401)
        response = await call_next(request)
        if path == "/api/settings" or path.startswith("/api/settings/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    app.include_router(router)


@router.get("/session")
def session_status(request: Request):
    sessions = request.app.state.settings_sessions
    return {"required": bool(sessions.token), "authenticated": sessions.authenticated(request)}


@router.post("/session")
def session_login(request: Request, body: dict):
    sessions = request.app.state.settings_sessions
    if not sessions.token and not sessions.authenticated(request):
        raise HTTPException(403, detail="请在部署配置中设置管理访问令牌")
    token = body.get("token")
    client = request.client.host if request.client else "unknown"
    with sessions.lock:
        now = time.monotonic()
        sessions.failures = {key: [t for t in times if now - t < 60] for key, times in sessions.failures.items() if any(now - t < 60 for t in times)}
        failures = sessions.failures.setdefault(client, [])
        if len(failures) >= 5:
            raise HTTPException(429, detail="验证过于频繁，请稍后再试")
        if sessions.token and (not isinstance(token, str) or not hmac.compare_digest(token.encode(), sessions.token.encode())):
            failures.append(now)
            raise HTTPException(401, detail="设置访问令牌不正确")
        sessions.failures.pop(client, None)
        nonce = secrets.token_urlsafe(32)
        value = (
            f"{nonce}.{hmac.new(sessions.token.encode(), nonce.encode(), hashlib.sha256).hexdigest()}"
            if sessions.token
            else nonce
        )
    response = JSONResponse({"required": bool(sessions.token), "authenticated": True})
    response.set_cookie(COOKIE, value, httponly=True, secure=request.url.scheme == "https" or request.headers.get("origin", "").startswith("https://"), samesite="strict", path="/api/settings")
    return response


@router.delete("/session")
def session_logout(request: Request):
    sessions = request.app.state.settings_sessions
    if not sessions.authenticated(request):
        raise HTTPException(401, detail="设置会话已失效")
    response = JSONResponse({"required": bool(sessions.token), "authenticated": not bool(sessions.token)})
    response.delete_cookie(COOKIE, path="/api/settings", httponly=True, samesite="strict")
    return response
