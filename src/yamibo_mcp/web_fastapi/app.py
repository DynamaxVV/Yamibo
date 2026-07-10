from __future__ import annotations

import mimetypes
import os
import threading
from pathlib import Path
from urllib.parse import unquote
from urllib.request import Request as URLRequest, urlopen

import uvicorn
from fastapi import FastAPI, Request, Query
from fastapi.staticfiles import StaticFiles
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response

from yamibo_mcp.config import load_settings


def create_app(settings) -> FastAPI:
    app = FastAPI(title="Yamibo Archiver", version="0.12.1")
    app.state.settings = settings

    from yamibo_mcp.errors import JobNotFound
    from yamibo_mcp.web_fastapi.routers import (
        chat, daemon, dashboard, debug, forums, jobs, knowledge,
        rag, remote_forum, review, series, threads,
    )
    from yamibo_mcp.web_fastapi.routers import settings as settings_router

    @app.exception_handler(JobNotFound)
    async def job_not_found_handler(request: Request, exc: JobNotFound):
        return JSONResponse(status_code=404, content={"error": str(exc)})

    @app.get("/api/health")
    def health():
        return {
            "ok": True,
            "database": settings.db_backend,
            "data_dir": str(settings.data_dir),
        }

    app.include_router(dashboard.router)
    app.include_router(jobs.router)
    app.include_router(threads.router)
    app.include_router(series.router)
    app.include_router(forums.router)
    app.include_router(rag.router)
    app.include_router(review.router)
    app.include_router(settings_router.router)
    app.include_router(knowledge.router)
    app.include_router(debug.router)
    app.include_router(daemon.router)
    app.include_router(remote_forum.router)
    app.include_router(chat.router)

    @app.get("/api/avatar-proxy")
    def avatar_proxy(uid: str = Query(...), size: str = Query(default="middle")):
        avatar_url = f"https://bbs.yamibo.com/uc_server/avatar.php?uid={uid}&size={size}"
        req = URLRequest(avatar_url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                                             "Referer": "https://bbs.yamibo.com/"})
        try:
            with urlopen(req, timeout=10) as resp:
                body = resp.read()
                content_type = resp.headers.get("Content-Type", "image/jpeg")
                return Response(content=body, media_type=content_type)
        except Exception:
            return PlainTextResponse("Not found", status_code=404)

    _mount_media_routes(app, settings)
    _mount_static_files(app)

    return app


def _mount_media_routes(app: FastAPI, settings) -> None:
    from starlette.responses import FileResponse, PlainTextResponse

    data_dir = settings.data_dir

    def _safe_resolve(root: Path, raw_rel: str) -> Path | None:
        target = (root / Path(unquote(raw_rel))).resolve()
        try:
            target.relative_to(root.resolve())
        except ValueError:
            return None
        return target

    def _guess_content_type(path: Path) -> str:
        guessed, _ = mimetypes.guess_type(path.name)
        if guessed:
            if guessed.startswith("text/") or guessed in {"application/javascript", "application/json"}:
                return f"{guessed}; charset=utf-8"
            return guessed
        return {
            ".woff2": "font/woff2",
            ".woff": "font/woff",
            ".ttf": "font/ttf",
            ".otf": "font/otf",
        }.get(path.suffix.lower(), "application/octet-stream")

    @app.get("/media/{file_path:path}")
    async def serve_media(file_path: str):
        raw_rel = file_path.strip("/")
        if not raw_rel:
            return PlainTextResponse("Not found", status_code=404)
        target = _safe_resolve(data_dir, raw_rel)
        if target is None:
            return PlainTextResponse("Forbidden", status_code=403)
        if not target.is_file():
            return PlainTextResponse("Not found", status_code=404)
        return FileResponse(target, media_type=_guess_content_type(target))

    @app.get("/fonts/{file_path:path}")
    async def serve_fonts(file_path: str):
        raw_rel = file_path.strip("/")
        if not raw_rel:
            return PlainTextResponse("Not found", status_code=404)
        font_root = data_dir / "fonts"
        target = _safe_resolve(font_root, raw_rel)
        if target is None:
            return PlainTextResponse("Forbidden", status_code=403)
        if not target.is_file():
            return PlainTextResponse("Not found", status_code=404)
        return FileResponse(target, media_type=_guess_content_type(target))

    @app.get("/artifacts/jobs/{job_id}/{filename:path}")
    async def serve_job_artifact(job_id: str, filename: str):
        from yamibo_mcp.storage.paths import StoragePaths

        artifact_root = StoragePaths(data_dir).staging_job_dir(job_id)
        target = _safe_resolve(artifact_root, filename)
        if target is None:
            return PlainTextResponse("Forbidden", status_code=403)
        if not target.is_file():
            return PlainTextResponse("Not found", status_code=404)
        return FileResponse(target, media_type=_guess_content_type(target))


def _mount_static_files(app: FastAPI) -> None:
    static_dir = _resolve_static_dir()

    if static_dir.exists():
        assets_dir = static_dir / "assets"
        if assets_dir.is_dir():
            app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

    @app.get("/{full_path:path}")
    async def spa_fallback(full_path: str):
        if full_path.startswith("api/") or full_path.startswith("media/") or full_path.startswith("fonts/") or full_path.startswith("artifacts/"):
            return PlainTextResponse("Not found", status_code=404)
        if not static_dir.exists():
            return PlainTextResponse("Not found", status_code=404)
        target = (static_dir / full_path).resolve()
        try:
            target.relative_to(static_dir.resolve())
        except ValueError:
            return PlainTextResponse("Forbidden", status_code=403)
        if target.is_file():
            return FileResponse(target)
        index = static_dir / "index.html"
        if index.is_file():
            return HTMLResponse(index.read_bytes(), status_code=200)
        return PlainTextResponse("Not found", status_code=404)


def _resolve_static_dir() -> Path:
    env_dir = os.environ.get("YAMIBO_STATIC_DIR")
    if env_dir:
        return Path(env_dir).expanduser().resolve()
    return Path(__file__).resolve().parent.parent / "web" / "static"


class EmbeddedWebServer:
    def __init__(self, settings):
        self._app = create_app(settings)
        self._config = uvicorn.Config(
            self._app,
            host=settings.web_host,
            port=settings.web_port,
            log_level="warning",
        )
        self._server = uvicorn.Server(self._config)
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None


def main() -> None:
    settings = load_settings()
    uvicorn.run(
        create_app(settings),
        host=settings.web_host,
        port=settings.web_port,
        log_level="warning",
    )
