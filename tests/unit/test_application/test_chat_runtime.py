from __future__ import annotations

import threading
import time
from queue import Empty

import pytest

import yamibo_mcp.services.chat_runtime as chat_runtime_module
from yamibo_mcp.services.chat_runtime import (
    CHAT_SESSION_BUSY,
    ChatRunStatus,
    ChatRunRegistry,
)


class FakeHermes:
    def __init__(self, events=(), run_status="completed"):
        self.events = list(events)
        self.run_status = run_status
        self.iter_calls = 0
        self.get_run_calls = 0
        self.get_messages_calls = 0
        self.stop_calls = 0
        self.approvals = []
        self.release = threading.Event()

    def start_run(self, *, session_id, input_text, history):
        return {"run_id": f"run-{session_id}"}

    def iter_run_events(self, run_id):
        self.iter_calls += 1
        for event in self.events:
            yield event

    def get_run(self, run_id):
        self.get_run_calls += 1
        return {"id": run_id, "status": self.run_status}

    def get_messages(self, session_id):
        self.get_messages_calls += 1
        return {"messages": [{"role": "assistant", "content": "done"}]}

    def stop_run(self, run_id):
        self.stop_calls += 1
        return {}

    def approve_run(self, run_id, *, choice, resolve_all):
        self.approvals.append((run_id, choice, resolve_all))
        return {}


def wait_status(registry, run_id, status):
    registry.join(run_id, timeout=2)
    assert registry.get(run_id).status is status


def test_single_upstream_consumer_and_two_subscribers_receive_same_order():
    client = FakeHermes([
        {"event": "message.delta", "json": {"delta": "a"}},
        {"event": "run.completed", "json": {"output": "a"}},
    ])
    registry = ChatRunRegistry(client, sleeper=lambda _: None)
    run = registry.submit(session_id="s1", input_text="hi", history=[])
    left = registry.subscribe(run.run_id)
    right = registry.subscribe(run.run_id)
    wait_status(registry, run.run_id, ChatRunStatus.COMPLETED)
    left_events = [left.get(timeout=1), left.get(timeout=1), left.get(timeout=1)]
    right_events = [right.get(timeout=1), right.get(timeout=1), right.get(timeout=1)]
    assert [e.seq for e in left_events] == [e.seq for e in right_events]
    assert [e.type for e in left_events] == ["message.delta", "run.completed", "session.reconciled"]
    assert client.iter_calls == 1


def test_subscriber_disconnect_does_not_stop_background_run():
    class BackgroundRun(FakeHermes):
        def __init__(self):
            super().__init__()
            self.started = threading.Event()

        def iter_run_events(self, run_id):
            self.iter_calls += 1
            self.started.set()
            self.release.wait(2)
            yield {"event": "message.delta", "json": {"delta": "done"}}
            yield {"event": "run.completed", "json": {"output": "done"}}

    client = BackgroundRun()
    registry = ChatRunRegistry(client, sleeper=lambda _: None)
    run = registry.submit(session_id="background", input_text="hi", history=[])
    subscription = registry.subscribe(run.run_id)
    assert client.started.wait(1)

    # A browser tab can disappear at any time.  Removing its local SSE
    # subscriber must not send Hermes a stop request or terminate the worker.
    subscription.close()
    assert client.stop_calls == 0
    client.release.set()
    wait_status(registry, run.run_id, ChatRunStatus.COMPLETED)
    assert [event.type for event in run.events] == ["message.delta", "run.completed", "session.reconciled"]


def test_real_hermes_message_envelope_uses_json_event_and_preserves_payload():
    client = FakeHermes([
        {"event": "message", "json": {"event": "message.delta", "delta": "a"}},
        {"event": "message", "json": {"event": "reasoning", "text": "thinking"}},
        {"event": "message", "json": {"event": "run.completed", "output": "a"}},
    ])
    registry = ChatRunRegistry(client, sleeper=lambda _: None)
    run = registry.submit(session_id="real-envelope", input_text="hi", history=[])
    registry.join(run.run_id, 2)
    assert [(event.type, event.payload) for event in list(run.events)[:3]] == [
        ("message.delta", {"event": "message.delta", "delta": "a"}),
        ("reasoning.available", {"event": "reasoning", "text": "thinking"}),
        ("run.completed", {"event": "run.completed", "output": "a"}),
    ]


def test_session_exclusive_and_different_sessions_can_run():
    class Blocking(FakeHermes):
        def iter_run_events(self, run_id):
            self.iter_calls += 1
            self.release.wait(1)
            yield {"event": "run.cancelled", "json": {}}

    client = Blocking([])
    registry = ChatRunRegistry(client, sleeper=lambda _: None)
    first = registry.submit(session_id="s", input_text="1", history=[])
    with pytest.raises(RuntimeError, match=CHAT_SESSION_BUSY):
        registry.submit(session_id="s", input_text="2", history=[])
    second = registry.submit(session_id="other", input_text="2", history=[])
    client.release.set()
    registry.join(first.run_id, 2)
    registry.join(second.run_id, 2)


def test_replay_gap_and_ring_limits():
    client = FakeHermes([{"event": "message.delta", "json": {"delta": "x" * 20}} for _ in range(8)])
    registry = ChatRunRegistry(client, max_events=3, max_bytes=600, sleeper=lambda _: None)
    run = registry.submit(session_id="s", input_text="x", history=[])
    registry.join(run.run_id, 2)
    sub = registry.subscribe(run.run_id, last_event_id=0)
    assert sub.get(timeout=1).type == "stream.gap"
    replay = []
    while True:
        try:
            replay.append(sub.get(timeout=.05))
        except Empty:
            break
    assert replay and all(event.seq > 0 for event in replay)
    assert len(registry.get(run.run_id).events) <= 3


def test_last_event_id_gap_triggers_run_and_message_calibration():
    client = FakeHermes([{"event": "message.delta", "json": {"delta": str(index)}} for index in range(8)])
    registry = ChatRunRegistry(client, max_events=3, sleeper=lambda _: None)
    run = registry.submit(session_id="s", input_text="x", history=[])
    registry.join(run.run_id, 2)
    get_run_calls_before_gap = client.get_run_calls

    registry.subscribe(run.run_id, last_event_id=0)

    assert client.get_run_calls > get_run_calls_before_gap
    assert client.get_messages_calls > 0


@pytest.mark.parametrize(
    ("event_type", "expected_status"),
    [
        ("run.completed", ChatRunStatus.COMPLETED),
        ("run.failed", ChatRunStatus.FAILED),
        ("run.cancelled", ChatRunStatus.CANCELLED),
    ],
)
def test_each_terminal_event_performs_double_calibration_before_final_status(event_type, expected_status):
    client = FakeHermes([{"event": event_type, "json": {}}])
    registry = ChatRunRegistry(client, sleeper=lambda _: None)
    run = registry.submit(session_id=f"s-{event_type}", input_text="x", history=[])

    wait_status(registry, run.run_id, expected_status)

    assert client.get_run_calls == 1
    assert client.get_messages_calls == 1
    assert any(event.type == "session.reconciled" for event in run.events)


def test_ring_buffer_enforces_500_event_limit_and_keeps_byte_accounting_consistent():
    client = FakeHermes([{"event": "message.delta", "json": {"delta": str(index)}} for index in range(600)])
    registry = ChatRunRegistry(client, max_events=500, max_bytes=10 * 1024 * 1024, sleeper=lambda _: None)
    run = registry.submit(session_id="ring-count", input_text="x", history=[])
    registry.join(run.run_id, 2)

    assert len(run.events) == 500
    assert run.buffer_bytes == sum(run.event_sizes)
    assert run.buffer_bytes <= registry.max_bytes


def test_ring_buffer_enforces_one_megabyte_serialized_limit_independently_of_event_count():
    client = FakeHermes([{"event": "message.delta", "json": {"delta": "x" * 2000}} for _ in range(1000)])
    registry = ChatRunRegistry(client, max_events=5000, max_bytes=1024 * 1024, sleeper=lambda _: None)
    run = registry.submit(session_id="ring-bytes", input_text="x", history=[])
    registry.join(run.run_id, 2)

    assert len(run.events) < 500
    assert run.buffer_bytes <= 1024 * 1024
    assert run.buffer_bytes == sum(run.event_sizes)


def test_cleanup_removes_terminal_runs_at_one_hour_but_retains_non_terminal_runs():
    now = [100.0]

    class Blocking(FakeHermes):
        def iter_run_events(self, run_id):
            self.iter_calls += 1
            self.release.wait(2)
            yield {"event": "run.cancelled", "json": {}}

    terminal_client = FakeHermes([{"event": "run.completed", "json": {}}])
    terminal_registry = ChatRunRegistry(terminal_client, clock=lambda: now[0], sleeper=lambda _: None)
    terminal = terminal_registry.submit(session_id="cleanup-terminal", input_text="x", history=[])
    wait_status(terminal_registry, terminal.run_id, ChatRunStatus.COMPLETED)
    assert terminal_registry.cleanup(now=now[0] + 3599) == 0
    assert terminal_registry.cleanup(now=now[0] + 3600) == 1

    running_client = Blocking()
    running_registry = ChatRunRegistry(running_client, clock=lambda: now[0], sleeper=lambda _: None)
    running = running_registry.submit(session_id="cleanup-running", input_text="x", history=[])
    assert running_registry.cleanup(now=now[0] + 3600) == 0
    assert running_registry.get(running.run_id).status is not ChatRunStatus.COMPLETED
    running_client.release.set()
    running_registry.join(running.run_id, 2)


def test_waiting_for_approval_accepts_each_choice_including_deny_without_stopping():
    class Waiting(FakeHermes):
        def __init__(self):
            super().__init__([{"event": "run.completed", "json": {}}])
            self.started = threading.Event()

        def iter_run_events(self, run_id):
            self.iter_calls += 1
            self.started.set()
            self.release.wait(2)
            yield from super().iter_run_events(run_id)

    for choice in ("once", "session", "always", "deny"):
        client = Waiting()
        registry = ChatRunRegistry(client, sleeper=lambda _: None)
        run = registry.submit(session_id=f"approval-{choice}", input_text="x", history=[])
        assert client.started.wait(1)
        registry._publish(run, "approval.request", {"choices": ["once", "session", "always", "deny"]})

        registry.approve(run.run_id, choice=choice)
        assert client.approvals == [(run.run_id, choice, False)]
        assert client.stop_calls == 0
        client.release.set()
        registry.join(run.run_id, 2)


def test_failed_submission_releases_session_placeholder_for_retry():
    class FailsOnce(FakeHermes):
        def __init__(self):
            super().__init__()
            self.fail = True

        def start_run(self, *, session_id, input_text, history):
            if self.fail:
                self.fail = False
                raise RuntimeError("submit failed")
            return super().start_run(session_id=session_id, input_text=input_text, history=history)

    client = FailsOnce()
    registry = ChatRunRegistry(client, sleeper=lambda _: None)
    with pytest.raises(RuntimeError, match="submit failed"):
        registry.submit(session_id="retry", input_text="x", history=[])

    retried = registry.submit(session_id="retry", input_text="x", history=[])
    assert retried.status in {ChatRunStatus.QUEUED, ChatRunStatus.RUNNING, ChatRunStatus.COMPLETED}


@pytest.mark.parametrize(
    ("failed_operation", "expected_code"),
    [
        ("run", "CHAT_RUN_CALIBRATION_RUN_FAILED"),
        ("messages", "CHAT_RUN_CALIBRATION_MESSAGES_FAILED"),
    ],
)
def test_single_calibration_failure_is_observable_and_other_calibration_continues(failed_operation, expected_code):
    class PartialFailure(FakeHermes):
        def get_run(self, run_id):
            if failed_operation == "run":
                self.get_run_calls += 1
                raise RuntimeError("run secret should not escape")
            return super().get_run(run_id)

        def get_messages(self, session_id):
            if failed_operation == "messages":
                self.get_messages_calls += 1
                raise RuntimeError("message secret should not escape")
            return super().get_messages(session_id)

    client = PartialFailure([{"event": "run.completed", "json": {}}])
    registry = ChatRunRegistry(client, sleeper=lambda _: None)
    run = registry.submit(session_id=f"calibration-{failed_operation}", input_text="x", history=[])
    wait_status(registry, run.run_id, ChatRunStatus.COMPLETED)

    assert client.get_run_calls == 1
    assert client.get_messages_calls == 1
    assert run.error == {"code": expected_code}
    assert any(event.type == "stream.error" and event.payload["error"]["code"] == expected_code for event in run.events)


def test_disconnect_recovers_when_run_query_reaches_terminal_state():
    client = FakeHermes([], run_status="completed")
    registry = ChatRunRegistry(client, sleeper=lambda _: None)
    run = registry.submit(session_id="disconnect-recover", input_text="x", history=[])

    wait_status(registry, run.run_id, ChatRunStatus.COMPLETED)

    assert client.get_run_calls == 2
    assert client.get_messages_calls == 1
    assert run.error is None


@pytest.mark.parametrize("status", ["completed", "failed", "cancelled"])
def test_disconnect_polling_publishes_terminal_before_reconciliation(status):
    client = FakeHermes([], run_status=status)
    registry = ChatRunRegistry(client, sleeper=lambda _: None)
    run = registry.submit(session_id=f"disconnect-{status}", input_text="x", history=[])
    registry.join(run.run_id, 2)
    assert [event.type for event in run.events] == [f"run.{status}", "session.reconciled"]
    assert run.status is ChatRunStatus(status)


def test_disconnect_with_run_still_running_becomes_unknown_after_message_calibration():
    client = FakeHermes([], run_status="running")
    registry = ChatRunRegistry(client, sleeper=lambda _: None)
    run = registry.submit(session_id="disconnect-lost", input_text="x", history=[])

    wait_status(registry, run.run_id, ChatRunStatus.UNKNOWN)

    assert run.error == {"code": "CHAT_RUN_STREAM_LOST"}
    assert client.get_run_calls == 7
    assert client.get_messages_calls == 1
    assert [event.type for event in run.events] == ["stream.error", "session.reconciled"]


def test_unknown_run_blocks_a_second_submit_until_hermes_is_final():
    client = FakeHermes([], run_status="running")
    registry = ChatRunRegistry(client, sleeper=lambda _: None)
    first = registry.submit(session_id="unknown-busy", input_text="first", history=[])
    wait_status(registry, first.run_id, ChatRunStatus.UNKNOWN)

    with pytest.raises(RuntimeError, match=CHAT_SESSION_BUSY):
        registry.submit(session_id="unknown-busy", input_text="second", history=[])

    client.run_status = "completed"
    second = registry.submit(session_id="unknown-busy", input_text="second", history=[])
    wait_status(registry, second.run_id, ChatRunStatus.COMPLETED)


def test_gap_is_subscriber_local_and_concurrent_gap_subscriptions_share_one_calibration():
    class BlockingCalibration(FakeHermes):
        def __init__(self):
            super().__init__([{"event": "message.delta", "json": {"delta": str(index)}} for index in range(8)])
            self.calibration_started = threading.Event()
            self.release_calibration = threading.Event()

        def get_messages(self, session_id):
            self.get_messages_calls += 1
            if self.get_messages_calls > 1:
                self.calibration_started.set()
                self.release_calibration.wait(2)
            return {"messages": [{"role": "assistant", "content": "done"}]}

    client = BlockingCalibration()
    registry = ChatRunRegistry(client, max_events=3, sleeper=lambda _: None)
    run = registry.submit(session_id="gap-local", input_text="x", history=[])
    registry.join(run.run_id, 2)
    before_events = list(run.events)
    before_run_calls = client.get_run_calls
    before_message_calls = client.get_messages_calls
    existing = registry.subscribe(run.run_id, last_event_id=run.last_seq)

    first = registry.subscribe(run.run_id, last_event_id=0)
    assert client.calibration_started.wait(1)
    second = registry.subscribe(run.run_id, last_event_id=0)
    assert first.get(timeout=1).type == "stream.gap"
    assert second.get(timeout=1).type == "stream.gap"
    assert list(run.events) == before_events
    with pytest.raises(Empty):
        existing.get(timeout=0.05)

    client.release_calibration.set()
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline and client.get_run_calls < before_run_calls + 1:
        time.sleep(0.001)
    assert client.get_run_calls == before_run_calls + 1
    assert client.get_messages_calls == before_message_calls + 1


def test_shutdown_closes_local_subscription_without_stop_or_approval_and_stops_at_event_boundary():
    class ShutdownFake(FakeHermes):
        def __init__(self):
            super().__init__()
            self.started = threading.Event()

        def iter_run_events(self, run_id):
            self.iter_calls += 1
            self.started.set()
            self.release.wait(2)
            yield {"event": "message.delta", "json": {"delta": "late"}}

    client = ShutdownFake()
    registry = ChatRunRegistry(client, sleeper=lambda _: None)
    run = registry.submit(session_id="shutdown", input_text="x", history=[])
    assert client.started.wait(1)
    sub = registry.subscribe(run.run_id)

    registry.shutdown()
    assert sub.closed
    assert sub.get(timeout=1).type == "stream.closed"
    assert client.stop_calls == 0
    assert client.approvals == []

    client.release.set()
    registry.join(run.run_id, 2)
    assert [event.type for event in run.events] == []


def test_gap_replay_and_later_publish_have_strictly_increasing_non_duplicate_sequences(monkeypatch):
    client = FakeHermes([{"event": "message.delta", "json": {"delta": str(index)}} for index in range(8)])
    registry = ChatRunRegistry(client, max_events=3, sleeper=lambda _: None)
    monkeypatch.setattr(registry, "_schedule_calibration", lambda _run: None)
    run = registry.submit(session_id="seq-chain", input_text="x", history=[])
    registry.join(run.run_id, 2)
    subscription = registry.subscribe(run.run_id, last_event_id=0)
    replayed = [subscription.get(timeout=1) for _ in range(4)]
    later = registry._publish(run, "message.delta", {"delta": "later"})
    assert [event.type for event in replayed[:1]] == ["stream.gap"]
    assert replayed[0].seq + 1 == replayed[1].seq
    assert [event.seq for event in replayed[1:]] == sorted({event.seq for event in replayed[1:]})
    assert later.seq > replayed[-1].seq
    assert len({event.seq for event in replayed + [later]}) == len(replayed) + 1


def test_event_serialization_does_not_hold_registry_lock(monkeypatch):
    client = FakeHermes([])
    registry = ChatRunRegistry(client, sleeper=lambda _: None)
    run = registry.submit(session_id="lock-boundary", input_text="x", history=[])
    registry.join(run.run_id, 2)
    original_dumps = chat_runtime_module.json.dumps
    lock_owned_during_serialization = []

    def inspect_lock(*args, **kwargs):
        lock_owned_during_serialization.append(registry._lock._is_owned())
        return original_dumps(*args, **kwargs)

    monkeypatch.setattr(chat_runtime_module.json, "dumps", inspect_lock)
    registry._publish(run, "message.delta", {"delta": "x"})
    assert lock_owned_during_serialization
    assert not any(lock_owned_during_serialization)


def test_concurrent_publish_assigns_unique_contiguous_sequences_in_queue_order():
    class Blocking(FakeHermes):
        def iter_run_events(self, run_id):
            self.iter_calls += 1
            self.release.wait(2)
            yield {"event": "run.completed", "json": {}}

    client = Blocking()
    registry = ChatRunRegistry(client, sleeper=lambda _: None)
    run = registry.submit(session_id="publish-concurrency", input_text="x", history=[])
    threads = [
        threading.Thread(target=registry._publish, args=(run, "message.delta", {"delta": str(index)}))
        for index in range(50)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(2)
    client.release.set()
    registry.join(run.run_id, 2)
    published = [event for event in run.events if event.type == "message.delta"]
    assert len(published) == 50
    assert [event.seq for event in published] == list(range(1, 51))


def test_unknown_event_is_error_and_stop_does_not_fake_cancelled():
    client = FakeHermes([{"event": "mystery", "json": {"secret": "no"}}], run_status="running")
    registry = ChatRunRegistry(client, sleeper=lambda _: None)
    run = registry.submit(session_id="s", input_text="x", history=[])
    registry.join(run.run_id, 2)
    assert registry.get(run.run_id).status is ChatRunStatus.UNKNOWN
    assert registry.get(run.run_id).error["code"] == "CHAT_RUN_STREAM_LOST"
    registry.stop(run.run_id)
    assert registry.get(run.run_id).status is not ChatRunStatus.CANCELLED


def test_approval_only_allowed_while_waiting():
    client = FakeHermes([])
    registry = ChatRunRegistry(client, sleeper=lambda _: None)
    run = registry.submit(session_id="s", input_text="x", history=[])
    registry.join(run.run_id, 2)
    with pytest.raises(RuntimeError, match="CHAT_APPROVAL_CONFLICT"):
        registry.approve(run.run_id, choice="once")


def test_terminal_event_reconciles_run_and_messages_and_shutdown_closes_subscriptions():
    client = FakeHermes([{"event": "run.cancelled", "json": {}}])
    registry = ChatRunRegistry(client, sleeper=lambda _: None)
    run = registry.submit(session_id="s", input_text="x", history=[])
    sub = registry.subscribe(run.run_id)
    wait_status(registry, run.run_id, ChatRunStatus.CANCELLED)
    assert client.get_run_calls == 1 and client.get_messages_calls == 1
    registry.shutdown()
    assert sub.closed
