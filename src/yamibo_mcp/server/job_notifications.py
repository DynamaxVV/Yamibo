from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict
from typing import Any

from yamibo_mcp.config import load_settings
from yamibo_mcp.db.connection import connect
from yamibo_mcp.db.repositories.job_events import JobEventsRepository
from yamibo_mcp.db.repositories.jobs import JobsRepository
from yamibo_mcp.server.resource_uris import job_events_uri, job_status_uri, parse_resource_uri
from yamibo_mcp.server.schemas import job_status_payload

LOG = logging.getLogger(__name__)


class JobResourceNotifier:
    def __init__(self, *, poll_interval_seconds: float = 1.0) -> None:
        self._poll_interval_seconds = poll_interval_seconds
        self._subscriptions_by_uri: dict[str, set[Any]] = defaultdict(set)
        self._uris_by_session: dict[Any, set[str]] = defaultdict(set)
        self._last_snapshot_by_uri: dict[str, str] = {}
        self._task: asyncio.Task[None] | None = None

    async def subscribe(self, uri: str, session: Any) -> None:
        self._validate_uri(uri)
        self._subscriptions_by_uri[uri].add(session)
        self._uris_by_session[session].add(uri)
        if uri not in self._last_snapshot_by_uri:
            self._last_snapshot_by_uri[uri] = self._snapshot_uri(uri)
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self.run_forever())

    async def unsubscribe(self, uri: str, session: Any) -> None:
        sessions = self._subscriptions_by_uri.get(uri)
        if sessions is not None:
            sessions.discard(session)
            if not sessions:
                self._subscriptions_by_uri.pop(uri, None)
                self._last_snapshot_by_uri.pop(uri, None)
        uris = self._uris_by_session.get(session)
        if uris is not None:
            uris.discard(uri)
            if not uris:
                self._uris_by_session.pop(session, None)

    async def poll_once(self) -> None:
        for uri in list(self._subscriptions_by_uri):
            current = self._snapshot_uri(uri)
            previous = self._last_snapshot_by_uri.get(uri)
            if previous is None:
                self._last_snapshot_by_uri[uri] = current
                continue
            if current == previous:
                continue
            self._last_snapshot_by_uri[uri] = current
            await self._notify_uri_updated(uri)

    async def run_forever(self) -> None:
        while True:
            try:
                if self._subscriptions_by_uri:
                    await self.poll_once()
                await asyncio.sleep(self._poll_interval_seconds)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - background notifier must stay alive
                LOG.exception("job resource notifier loop failed")
                await asyncio.sleep(self._poll_interval_seconds)

    def _snapshot_uri(self, uri: str) -> str:
        settings = load_settings()
        conn = connect(settings.db_path)
        try:
            kind_root, _, kind = parse_resource_uri(uri)
            if kind_root != "jobs":
                raise ValueError(f"unsupported job resource uri: {uri}")
            job_id = kind.split("/")[0]
            if kind.endswith("/status"):
                job = JobsRepository(conn).get(job_id)
                return json.dumps(job_status_payload(job), ensure_ascii=False, sort_keys=True)
            if kind.endswith("/events"):
                rows = JobEventsRepository(conn).list(job_id=job_id)
                return json.dumps(
                    [
                        {
                            "event_id": row.event_id,
                            "event_type": row.event_type,
                            "status": row.status,
                            "stage": row.stage,
                            "payload_json": json.dumps(row.payload, ensure_ascii=False),
                            "created_at": row.created_at,
                        }
                        for row in rows
                    ],
                    ensure_ascii=False,
                    sort_keys=True,
                )
            raise ValueError(f"unsupported job resource uri: {uri}")
        finally:
            conn.close()

    async def _notify_uri_updated(self, uri: str) -> None:
        for session in list(self._subscriptions_by_uri.get(uri, ())):
            try:
                await session.send_resource_updated(uri)
            except Exception:  # noqa: BLE001 - stale session cleanup
                LOG.exception("failed to notify session about updated resource %s", uri)
                await self._unsubscribe_session(session)

    async def _unsubscribe_session(self, session: Any) -> None:
        for uri in list(self._uris_by_session.get(session, ())):
            await self.unsubscribe(uri, session)

    def _validate_uri(self, uri: str) -> None:
        kind_root, _, kind = parse_resource_uri(uri)
        if kind_root != "jobs" or (not kind.endswith("/status") and not kind.endswith("/events")):
            raise ValueError(f"unsupported subscribable resource uri: {uri}")


def register_job_resource_subscriptions(server, notifier: JobResourceNotifier) -> None:
    @server._mcp_server.subscribe_resource()  # type: ignore[attr-defined]
    async def _subscribe_resource(uri) -> None:
        await notifier.subscribe(str(uri), server._mcp_server.request_context.session)  # type: ignore[attr-defined]

    @server._mcp_server.unsubscribe_resource()  # type: ignore[attr-defined]
    async def _unsubscribe_resource(uri) -> None:
        await notifier.unsubscribe(str(uri), server._mcp_server.request_context.session)  # type: ignore[attr-defined]


def enable_resource_subscription_capability(server) -> None:
    original_get_capabilities = server._mcp_server.get_capabilities  # type: ignore[attr-defined]

    def _get_capabilities(notification_options, experimental_capabilities):
        capabilities = original_get_capabilities(notification_options, experimental_capabilities)
        if capabilities.resources is not None:
            capabilities.resources.subscribe = True
        return capabilities

    server._mcp_server.get_capabilities = _get_capabilities  # type: ignore[attr-defined]


def build_default_job_resource_uris(job_id: str) -> dict[str, str]:
    return {"status": job_status_uri(job_id), "job_events": job_events_uri(job_id)}
