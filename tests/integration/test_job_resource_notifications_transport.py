from __future__ import annotations

import asyncio
import os
import socket
import sqlite3
import time
from contextlib import ExitStack, contextmanager
from pathlib import Path
from threading import Thread
from unittest.mock import patch

import uvicorn
from mcp.client.session import ClientSession
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamable_http_client

from yamibo_mcp.db.migrations import migrate
from yamibo_mcp.db.repositories.jobs import JobsRepository
from yamibo_mcp.server.mcp_registry import build_mcp_server
from yamibo_mcp.server.resource_uris import job_status_uri


def _fake_settings(tmp_path: Path):
    class Settings:
        pass

    settings = Settings()
    settings.db_path = tmp_path / "test.db"
    settings.data_dir = tmp_path / "data"
    settings.export_dir = tmp_path / "exports"
    settings.novel_txt_export_dir = tmp_path / "novel_exports"
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.export_dir.mkdir(parents=True, exist_ok=True)
    settings.novel_txt_export_dir.mkdir(parents=True, exist_ok=True)
    return settings


def _open_db(settings):
    conn = sqlite3.connect(settings.db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    migrate(conn)
    return conn


def _pick_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_port(port: int, *, timeout_seconds: float = 10) -> None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.1)
    raise TimeoutError(f"server port {port} did not open within {timeout_seconds}s")


@contextmanager
def _run_server(settings, *, transport: str):
    port = _pick_free_port()
    with ExitStack() as stack:
        stack.enter_context(
            patch.dict(
                os.environ,
                {
                    "YAMIBO_DATA_DIR": str(settings.data_dir),
                    "YAMIBO_DB_PATH": str(settings.db_path),
                    "YAMIBO_DB_BACKEND": "sqlite",
                    "YAMIBO_DB_URL": "",
                    "YAMIBO_EXPORT_DIR": str(settings.export_dir),
                    "YAMIBO_NOVEL_TXT_EXPORT_DIR": str(settings.novel_txt_export_dir),
                },
                clear=False,
            )
        )
        server = build_mcp_server()
        server.settings.transport_security = None
        app = server.streamable_http_app() if transport == "streamable-http" else server.sse_app()
        config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
        uvicorn_server = uvicorn.Server(config)
        thread = Thread(target=lambda: asyncio.run(uvicorn_server.serve()), daemon=True)
        thread.start()
        try:
            _wait_for_port(port)
            yield port
        finally:
            uvicorn_server.should_exit = True
            thread.join(timeout=5)


def test_streamable_http_subscription_receives_job_status_notification(tmp_path):
    settings = _fake_settings(tmp_path)
    conn = _open_db(settings)
    job = JobsRepository(conn).create("sync_thread", tid=9911, payload={"tid": 9911})
    conn.commit()
    conn.close()

    event = asyncio.Event()
    seen_uris: list[str] = []

    async def on_message(message) -> None:
        root = getattr(message, "root", None)
        if getattr(root, "method", None) == "notifications/resources/updated":
            seen_uris.append(str(root.params.uri))
            event.set()

    async def scenario(port: int) -> None:
        async with streamable_http_client(f"http://127.0.0.1:{port}/mcp", terminate_on_close=False) as streams:
            async with ClientSession(*streams[:2], message_handler=on_message) as session:
                await session.initialize()
                await session.subscribe_resource(job_status_uri(job.job_id))
                conn = _open_db(settings)
                JobsRepository(conn).succeed(job.job_id, {"tid": 9911})
                conn.commit()
                conn.close()
                await asyncio.wait_for(event.wait(), timeout=5)

    with _run_server(settings, transport="streamable-http") as port:
        asyncio.run(scenario(port))

    assert job_status_uri(job.job_id) in seen_uris


def test_sse_subscription_receives_job_status_notification(tmp_path):
    settings = _fake_settings(tmp_path)
    conn = _open_db(settings)
    job = JobsRepository(conn).create("sync_thread", tid=9912, payload={"tid": 9912})
    conn.commit()
    conn.close()

    event = asyncio.Event()
    seen_uris: list[str] = []

    async def on_message(message) -> None:
        root = getattr(message, "root", None)
        if getattr(root, "method", None) == "notifications/resources/updated":
            seen_uris.append(str(root.params.uri))
            event.set()

    async def scenario(port: int) -> None:
        async with sse_client(f"http://127.0.0.1:{port}/sse") as streams:
            async with ClientSession(*streams, message_handler=on_message) as session:
                await session.initialize()
                await session.subscribe_resource(job_status_uri(job.job_id))
                conn = _open_db(settings)
                JobsRepository(conn).succeed(job.job_id, {"tid": 9912})
                conn.commit()
                conn.close()
                await asyncio.wait_for(event.wait(), timeout=5)

    with _run_server(settings, transport="sse") as port:
        asyncio.run(scenario(port))

    assert job_status_uri(job.job_id) in seen_uris
