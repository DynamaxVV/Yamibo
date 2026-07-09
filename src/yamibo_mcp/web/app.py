from __future__ import annotations

import argparse
import mimetypes
import threading
from dataclasses import replace
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from yamibo_mcp.config import Settings, load_settings
from yamibo_mcp.logging import configure_logging
from yamibo_mcp.storage.paths import StoragePaths
from yamibo_mcp.web.api import handle_api


class WebHandler(BaseHTTPRequestHandler):
    settings: Settings

    def handle(self) -> None:
        try:
            super().handle()
        except (BrokenPipeError, ConnectionResetError):
            return

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)

        if parsed.path.startswith("/api/"):
            handle_api(self, parsed.path, parsed.query, self.settings)
            return

        if parsed.path.startswith("/media/"):
            self._serve_media(parsed.path)
            return

        if parsed.path.startswith("/fonts/"):
            self._serve_fonts(parsed.path)
            return

        if parsed.path.startswith("/artifacts/jobs/"):
            self._serve_job_artifact(parsed.path)
            return

        if self._serve_static(parsed.path):
            return

        self._send_text("Not found", HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/"):
            handle_api(self, parsed.path, parsed.query, self.settings)
            return
        self._send_text("Not found", HTTPStatus.NOT_FOUND)

    def do_DELETE(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/"):
            handle_api(self, parsed.path, parsed.query, self.settings)
            return
        self._send_text("Not found", HTTPStatus.NOT_FOUND)

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        return

    def _serve_static(self, path: str) -> bool:
        static_dir = Path(__file__).parent / "static"
        if not static_dir.exists():
            return False

        rel = path.lstrip("/") or "index.html"
        target = (static_dir / rel).resolve()
        try:
            target.relative_to(static_dir.resolve())
        except ValueError:
            self._send_text("Forbidden", HTTPStatus.FORBIDDEN)
            return True

        if target.is_file():
            self._send_bytes(target.read_bytes(), self._guess_content_type(target))
            return True

        index = static_dir / "index.html"
        if index.is_file():
            self._send_bytes(index.read_bytes(), "text/html; charset=utf-8")
            return True
        return False

    def _serve_media(self, path: str) -> None:
        raw_rel = path.removeprefix("/media/").strip("/")
        if not raw_rel:
            self._send_text("Not found", HTTPStatus.NOT_FOUND)
            return

        target = self._safe_resolve(self.settings.data_dir, raw_rel)
        if target is None:
            self._send_text("Forbidden", HTTPStatus.FORBIDDEN)
            return
        if not target.is_file():
            self._send_text("Not found", HTTPStatus.NOT_FOUND)
            return
        self._send_bytes(target.read_bytes(), self._guess_content_type(target))

    def _serve_fonts(self, path: str) -> None:
        raw_rel = path.removeprefix("/fonts/").strip("/")
        if not raw_rel:
            self._send_text("Not found", HTTPStatus.NOT_FOUND)
            return

        font_root = self.settings.data_dir / "fonts"
        target = self._safe_resolve(font_root, raw_rel)
        if target is None:
            self._send_text("Forbidden", HTTPStatus.FORBIDDEN)
            return
        if not target.is_file():
            self._send_text("Not found", HTTPStatus.NOT_FOUND)
            return
        self._send_bytes(target.read_bytes(), self._guess_content_type(target))

    def _serve_job_artifact(self, path: str) -> None:
        parts = [part for part in path.split("/") if part]
        if len(parts) != 4:
            self._send_text("Not found", HTTPStatus.NOT_FOUND)
            return

        _, _, job_id, filename = parts
        artifact_root = StoragePaths(self.settings.data_dir).staging_job_dir(job_id)
        target = self._safe_resolve(artifact_root, filename)
        if target is None:
            self._send_text("Forbidden", HTTPStatus.FORBIDDEN)
            return
        if not target.is_file():
            self._send_text("Not found", HTTPStatus.NOT_FOUND)
            return
        self._send_bytes(target.read_bytes(), self._guess_content_type(target))

    def _safe_resolve(self, root: Path, raw_rel: str) -> Path | None:
        target = (root / Path(unquote(raw_rel))).resolve()
        try:
            target.relative_to(root.resolve())
        except ValueError:
            return None
        return target

    def _guess_content_type(self, path: Path) -> str:
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

    def _send_text(self, body: str, status: HTTPStatus, content_type: str = "text/plain; charset=utf-8") -> None:
        self._send_bytes(body.encode("utf-8"), content_type, status)

    def _send_bytes(self, body: bytes, content_type: str, status: HTTPStatus = HTTPStatus.OK) -> None:
        self.send_response(status.value)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class EmbeddedWebServer:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._server = build_server(settings)
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None


def build_server(settings: Settings) -> ThreadingHTTPServer:
    WebHandler.settings = settings
    ThreadingHTTPServer.daemon_threads = True
    return ThreadingHTTPServer((settings.web_host, settings.web_port), WebHandler)


def run(settings: Settings) -> None:
    server = build_server(settings)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run YamiboArchiver local web console.")
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    args = parser.parse_args()

    configure_logging()
    settings = load_settings()
    if args.host or args.port:
        settings = replace(
            settings,
            web_host=args.host or settings.web_host,
            web_port=args.port or settings.web_port,
        )
    run(settings)


if __name__ == "__main__":
    main()
