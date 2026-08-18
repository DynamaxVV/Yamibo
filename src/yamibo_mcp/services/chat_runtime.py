"""In-process Hermes Run lifecycle, buffering, fan-out, and reconciliation."""
from __future__ import annotations

import json
import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from queue import Empty, Queue
from typing import Any, Callable

from .hermes_api import HermesApiClient

log = logging.getLogger(__name__)
CHAT_SESSION_BUSY = "CHAT_SESSION_BUSY"
_TERMINAL = {"completed", "failed", "cancelled", "unknown"}
_FINAL = {"completed", "failed", "cancelled"}
_ALLOWED_CHOICES = {"once", "session", "always", "deny"}
_TRANSITIONS = {
    "preparing": {"submitting", "failed"}, "submitting": {"queued", "running", "failed"},
    "queued": {"running", "waiting_for_approval", "stopping", "reconciling", *(_TERMINAL - {"unknown"})},
    "running": {"waiting_for_approval", "stopping", "reconciling", *(_TERMINAL - {"unknown"})},
    "waiting_for_approval": {"running", "stopping", "reconciling", *(_TERMINAL - {"unknown"})},
    "stopping": {"reconciling", *(_TERMINAL - {"unknown"})},
    "reconciling": set(_TERMINAL),
    "completed": set(), "failed": set(), "cancelled": set(), "unknown": set(),
}


class ChatRunStatus(str, Enum):
    PREPARING = "preparing"; SUBMITTING = "submitting"; QUEUED = "queued"; RUNNING = "running"
    WAITING_FOR_APPROVAL = "waiting_for_approval"; STOPPING = "stopping"; RECONCILING = "reconciling"
    COMPLETED = "completed"; FAILED = "failed"; CANCELLED = "cancelled"; UNKNOWN = "unknown"


@dataclass(frozen=True)
class NormalizedChatEvent:
    type: str
    run_id: str
    seq: int
    timestamp: float
    payload: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"type": self.type, "run_id": self.run_id, "seq": self.seq, "timestamp": self.timestamp, **self.payload}


@dataclass
class ChatRun:
    run_id: str
    session_id: str
    status: ChatRunStatus
    created_at: float
    updated_at: float
    last_seq: int = 0
    events: deque[NormalizedChatEvent] = field(default_factory=deque)
    subscribers: set[Queue] = field(default_factory=set)
    upstream_thread: threading.Thread | None = None
    stop_requested: bool = False
    terminal_payload: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    messages: dict[str, Any] | None = None
    buffer_bytes: int = 0
    event_sizes: deque[int] = field(default_factory=deque)
    calibration_inflight: bool = False


class ChatSubscription:
    def __init__(self, registry: "ChatRunRegistry", run_id: str, queue: Queue):
        self._registry, self.run_id, self.queue, self.closed = registry, run_id, queue, False
    def get(self, timeout: float | None = None) -> NormalizedChatEvent:
        return self.queue.get(timeout=timeout)
    def close(self) -> None:
        if not self.closed:
            self.closed = True; self._registry._unsubscribe(self.run_id, self.queue)


class ChatRunRegistry:
    def __init__(self, client: HermesApiClient, *, max_events: int = 500, max_bytes: int = 1024 * 1024,
                 sleeper: Callable[[float], None] = time.sleep, backoff: tuple[float, ...] = (.25, .5, 1, 2, 4),
                 clock: Callable[[], float] = time.time, retention: float = 3600.0):
        self.client, self.max_events, self.max_bytes = client, max_events, max_bytes
        self.sleeper, self.backoff = sleeper, backoff
        self.clock, self.retention = clock, retention
        self._lock = threading.RLock(); self._runs: dict[str, ChatRun] = {}; self._session_active_run: dict[str, str] = {}
        self._session_submit_locks: dict[str, threading.Lock] = {}
        self._publish_lock = threading.Lock()
        self._subscriptions: dict[Queue, ChatSubscription] = {}
        self._shutdown = False

    def submit(self, *, session_id: str, input_text: str, history: list[dict[str, Any]]) -> ChatRun:
        # Serialize submissions for one Session without holding the registry
        # lock across the remote status probe below.
        with self._submit_lock_for(session_id):
            now = self.clock()
            with self._lock:
                active = self._session_active_run.get(session_id)
                active_run = self._runs.get(active) if active else None
                if active_run and active_run.status.value not in _TERMINAL:
                    raise RuntimeError(CHAT_SESSION_BUSY)
                unknown_run_id = active_run.run_id if active_run and active_run.status is ChatRunStatus.UNKNOWN else None
            # ``unknown`` is a local uncertainty state, not proof that Hermes
            # finished.  A lost SSE connection can leave the upstream run
            # executing; starting another run in the same Session would then
            # interleave two assistant turns and duplicate the visible answer.
            if unknown_run_id and not self._remote_run_is_final(unknown_run_id):
                raise RuntimeError(CHAT_SESSION_BUSY)
            with self._lock:
                # Re-check after the network probe in case cleanup or another
                # lifecycle transition changed the local Session mapping.
                active = self._session_active_run.get(session_id)
                active_run = self._runs.get(active) if active else None
                if active_run and active_run.status.value not in _TERMINAL:
                    raise RuntimeError(CHAT_SESSION_BUSY)
                run = ChatRun(f"local-{time.time_ns()}", session_id, ChatRunStatus.PREPARING, now, now)
                self._runs[run.run_id] = run; self._session_active_run[session_id] = run.run_id
        try:
            self._transition(run, ChatRunStatus.SUBMITTING)
            response = self.client.start_run(session_id=session_id, input_text=input_text, history=history)
            upstream_id = response.get("run_id") or response.get("id")
            if not upstream_id: raise RuntimeError("Hermes did not return run_id")
            with self._lock:
                placeholder_id = next((key for key, value in self._runs.items() if value is run), None)
                run.run_id = str(upstream_id); self._runs[run.run_id] = run
                if placeholder_id and placeholder_id != run.run_id: self._runs.pop(placeholder_id, None)
                self._session_active_run[session_id] = run.run_id
            self._transition(run, ChatRunStatus.QUEUED)
            thread = threading.Thread(target=self._consume, args=(run,), daemon=True, name=f"chat-run-{run.run_id}")
            with self._lock: run.upstream_thread = thread
            thread.start(); return run
        except Exception as exc:
            with self._lock: self._transition(run, ChatRunStatus.FAILED); self._session_active_run.pop(session_id, None)
            raise

    start_run = submit

    def _submit_lock_for(self, session_id: str) -> threading.Lock:
        with self._lock:
            return self._session_submit_locks.setdefault(session_id, threading.Lock())

    def get(self, run_id: str) -> ChatRun:
        with self._lock: return self._runs[run_id]
    def join(self, run_id: str, timeout: float | None = None) -> None:
        thread = self.get(run_id).upstream_thread
        if thread: thread.join(timeout)

    def subscribe(self, run_id: str, last_event_id: int | str | None = None) -> ChatSubscription:
        gap = False
        with self._lock:
            run = self._runs[run_id]; q: Queue = Queue(); after = int(last_event_id or 0)
            if run.events and after < run.events[0].seq - 1:
                gap = True
                q.put_nowait(NormalizedChatEvent("stream.gap", run.run_id, run.events[0].seq - 1, self.clock(), {"after_seq": after, "available_from": run.events[0].seq}))
            for event in run.events:
                if event.seq > after: q.put(event)
            run.subscribers.add(q); subscription = ChatSubscription(self, run_id, q); self._subscriptions[q] = subscription
        if gap: self._schedule_calibration(run)
        return subscription

    def _unsubscribe(self, run_id: str, q: Queue) -> None:
        with self._lock:
            if run_id in self._runs: self._runs[run_id].subscribers.discard(q)
            self._subscriptions.pop(q, None)

    def stop(self, run_id: str) -> dict[str, Any]:
        run = self.get(run_id)
        with self._lock:
            if run.status.value in _TERMINAL: return {}
            run.stop_requested = True; self._transition(run, ChatRunStatus.STOPPING)
        return self.client.stop_run(run_id)

    def approve(self, run_id: str, *, choice: str, resolve_all: bool = False) -> dict[str, Any]:
        run = self.get(run_id)
        if choice not in _ALLOWED_CHOICES: raise ValueError("CHAT_APPROVAL_INVALID_CHOICE")
        with self._lock:
            if run.status is not ChatRunStatus.WAITING_FOR_APPROVAL: raise RuntimeError("CHAT_APPROVAL_CONFLICT")
        return self.client.approve_run(run_id, choice=choice, resolve_all=resolve_all)

    def shutdown(self) -> None:
        with self._lock:
            self._shutdown = True
            for run in self._runs.values():
                for q in list(run.subscribers):
                    q.put_nowait(_event(run.run_id, run.last_seq + 1, "stream.closed", {}, self.clock))
                    subscription = self._subscriptions.get(q)
                    if subscription: subscription.closed = True
                run.subscribers.clear()
            self._subscriptions.clear()

    def cleanup(self, now: float | None = None) -> int:
        """Drop terminal runs retained longer than the configured window."""
        cutoff = self.clock() if now is None else now
        removed = 0
        with self._lock:
            for run_id, run in list(self._runs.items()):
                if run.status.value in _TERMINAL and cutoff - run.updated_at >= self.retention:
                    for q in list(run.subscribers):
                        sub = self._subscriptions.pop(q, None)
                        if sub: sub.closed = True
                    run.subscribers.clear(); self._runs.pop(run_id, None)
                    if self._session_active_run.get(run.session_id) == run_id: self._session_active_run.pop(run.session_id, None)
                    removed += 1
        return removed

    purge_expired = cleanup

    def _transition(self, run: ChatRun, target: ChatRunStatus) -> None:
        with self._lock:
            if target.value not in _TRANSITIONS[run.status.value]:
                log.warning("chat invalid transition run_id=%s session_id=%s status=%s event_type=status", run.run_id, run.session_id, run.status.value)
                return
            run.status = target; run.updated_at = self.clock()

    def _remote_run_is_final(self, run_id: str) -> bool:
        try:
            status = str(self.client.get_run(run_id).get("status") or "").lower()
        except Exception:
            # A failed status probe must not permit a second upstream run.
            return False
        return status in _FINAL

    def _consume(self, run: ChatRun) -> None:
        terminal = False
        iterator = None
        try:
            self._transition(run, ChatRunStatus.RUNNING)
            try:
                iterator = self.client.iter_run_events(run.run_id)
                for raw in iterator:
                    with self._lock:
                        if self._shutdown: return
                    typ, payload = self._normalize(raw)
                    self._publish(run, typ, payload)
                    if typ.startswith("run.") and typ in {"run.completed", "run.failed", "run.cancelled"}:
                        terminal = True; run.terminal_payload = payload; break
            except Exception as exc:
                log.warning("chat stream disconnected run_id=%s session_id=%s error_code=CHAT_RUN_STREAM_LOST", run.run_id, run.session_id)
            if self._shutdown: return
            if not terminal:
                self._transition(run, ChatRunStatus.RECONCILING)
                final = None
                for delay in (0, *self.backoff):
                    if delay: self.sleeper(delay)
                    try: final = self.client.get_run(run.run_id)
                    except Exception: final = None
                    if str((final or {}).get("status", "")).lower() in _TERMINAL - {"unknown"}: break
                if str((final or {}).get("status", "")).lower() not in _TERMINAL - {"unknown"}:
                    run.error = {"code": "CHAT_RUN_STREAM_LOST"}
                    self._publish(run, "stream.error", {"error": {"code": "CHAT_RUN_STREAM_LOST"}})
                    self._calibrate(run)
                    self._transition(run, ChatRunStatus.UNKNOWN)
                    return
                terminal_status = str(final.get("status")).lower()
                terminal_type = f"run.{terminal_status}"
                run.terminal_payload = self._terminal_payload(final)
                self._publish(run, terminal_type, run.terminal_payload)
                self._calibrate(run)
                self._transition(run, ChatRunStatus(terminal_status))
                return
            self._transition(run, ChatRunStatus.RECONCILING)
            self._calibrate(run)
            status = {"run.completed": ChatRunStatus.COMPLETED, "run.failed": ChatRunStatus.FAILED, "run.cancelled": ChatRunStatus.CANCELLED}[typ]
            self._transition(run, status)
        finally:
            if iterator is not None and self._shutdown:
                close = getattr(iterator, "close", None)
                if callable(close):
                    try: close()
                    except Exception: pass
            with self._lock:
                # Keep an ``unknown`` run attached to its Session.  Hermes may
                # still be executing after the local SSE consumer is lost;
                # submit() releases this lock only after a fresh terminal
                # status probe, preventing a concurrent second run.
                if run.status.value in _FINAL and self._session_active_run.get(run.session_id) == run.run_id: self._session_active_run.pop(run.session_id, None)

    def _calibrate(self, run: ChatRun) -> None:
        run_result = None
        try: run_result = self.client.get_run(run.run_id)
        except Exception as exc:
            self._calibration_error(run, "CHAT_RUN_CALIBRATION_RUN_FAILED", exc)
        try:
            run.messages = self.client.get_messages(run.session_id); count = len((run.messages or {}).get("messages", []))
        except Exception as exc: self._calibration_error(run, "CHAT_RUN_CALIBRATION_MESSAGES_FAILED", exc)
        finally:
            # Reconciliation is also the finite boundary for clients when a
            # stream is lost or one half of calibration fails.
            self._publish(run, "session.reconciled", {"session_id": run.session_id, "message_count": count if 'count' in locals() else 0})

    def _calibration_error(self, run: ChatRun, code: str, exc: Exception) -> None:
        with self._lock:
            run.error = {"code": code}
        self._publish(run, "stream.error", {"error": {"code": code}})

    def _schedule_calibration(self, run: ChatRun) -> None:
        with self._lock:
            if run.calibration_inflight or self._shutdown: return
            run.calibration_inflight = True
        def work() -> None:
            try:
                if not self._shutdown: self._calibrate(run)
            finally:
                with self._lock: run.calibration_inflight = False
        threading.Thread(target=work, daemon=True, name=f"chat-calibrate-{run.run_id}").start()

    def _normalize(self, raw: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        json_payload = raw.get("json") if isinstance(raw.get("json"), dict) else None
        payload = json_payload if json_payload is not None else raw.get("data") if isinstance(raw.get("data"), dict) else {}
        # HermesApiClient uses the generic SSE event ``message``; the actual
        # protocol event is carried inside the decoded JSON object.
        typ = str((json_payload or {}).get("event") or (json_payload or {}).get("type") or "")
        if not typ or (typ == "message" and raw.get("event") not in (None, "", "message")):
            typ = str(raw.get("event") or raw.get("type") or typ)
        if typ == "reasoning":
            typ = "reasoning.available"
        if typ not in {"message.delta", "tool.started", "tool.completed", "reasoning.available", "approval.request", "approval.responded", "run.completed", "run.failed", "run.cancelled"}:
            return "stream.error", {"error": {"code": "CHAT_UPSTREAM_EVENT_UNKNOWN"}}
        return typ, dict(payload)

    @staticmethod
    def _terminal_payload(final: dict[str, Any]) -> dict[str, Any]:
        """Expose only documented, non-credential terminal result fields."""
        allowed = {"output", "usage", "error"}
        return {key: final[key] for key in allowed if key in final}

    def _publish(self, run: ChatRun, typ: str, payload: dict[str, Any]) -> NormalizedChatEvent:
        # Serialize one event outside the registry lock.  The publish lock
        # preserves seq/order while allowing subscribers and other runs to
        # use the registry during serialization.
        with self._publish_lock:
            with self._lock:
                run.last_seq += 1
                event = NormalizedChatEvent(typ, run.run_id, run.last_seq, self.clock(), payload)
            event_size = len(json.dumps(event.as_dict(), ensure_ascii=False).encode())
            with self._lock:
                self._append_event_locked(run, event, event_size)
            if typ == "approval.request": self._transition(run, ChatRunStatus.WAITING_FOR_APPROVAL)
            elif typ == "approval.responded": self._transition(run, ChatRunStatus.RUNNING)
            return event

    def _append_event_locked(self, run: ChatRun, event: NormalizedChatEvent, event_size: int) -> None:
        run.events.append(event); run.event_sizes.append(event_size); run.buffer_bytes += event_size
        while len(run.events) > self.max_events or run.buffer_bytes > self.max_bytes:
            run.events.popleft(); run.buffer_bytes -= run.event_sizes.popleft()
        targets = list(run.subscribers)
        for q in targets: q.put_nowait(event)


def _event(run_id: str, seq: int, typ: str, payload: dict[str, Any], clock: Callable[[], float] = time.time) -> NormalizedChatEvent:
    return NormalizedChatEvent(typ, run_id, seq, clock(), payload)
