from contextlib import contextmanager
from unittest.mock import Mock

import pytest

from yamibo_mcp.application import scheduled_task_commands as commands
from yamibo_mcp.server import agent_tools
from yamibo_mcp.services.embedded_chat.mcp import public_input_model

TASK_ID = "f025b51c-6a6b-40e2-8c05-94963a1aee8e"


@pytest.fixture
def transaction(monkeypatch):
    conn = Mock()
    outcomes = []

    @contextmanager
    def connect():
        try:
            yield conn
        except Exception:
            outcomes.append("rollback")
            raise
        else:
            outcomes.append("commit")

    monkeypatch.setattr(commands, "connect", connect)
    repo = Mock()
    monkeypatch.setattr(commands, "ScheduledTasksRepository", lambda c: repo)
    return conn, repo, outcomes


def test_create_only_fixed_action_and_commit(transaction):
    _, repo, outcomes = transaction
    commands.create_archive_schedule(name="归档", tid=572313, schedule_kind="cron", cron="0 8 * * *", forum_id=30)
    args = repo.create.call_args.kwargs
    assert args["action"] == "archive_thread"
    assert args["owner_id"] == "local"
    assert args["arguments"] == {"tid": 572313, "mode": "text_only", "forum_id": 30}
    assert outcomes == ["commit"]


def test_invalid_naive_timestamp_rolls_back(transaction):
    _, repo, outcomes = transaction
    with pytest.raises(ValueError, match="timezone"):
        commands.create_archive_schedule(name="归档", tid=572313, schedule_kind="at", at="2030-01-01T08:00:00")
    repo.create.assert_not_called()
    assert outcomes == ["rollback"]


def test_trigger_revision_is_locked_before_dispatch(transaction, monkeypatch):
    conn, _, outcomes = transaction
    conn.execute.return_value.fetchone.return_value = {"revision": 2}
    dispatch = Mock()
    monkeypatch.setattr(commands, "trigger_task", dispatch)
    with pytest.raises(ValueError, match="REVISION_CONFLICT"):
        commands.trigger_scheduled_task(task_id=TASK_ID, expected_revision=1, requested_at="2030-01-01T08:00:00+08:00")
    dispatch.assert_not_called()
    assert "FOR UPDATE" in conn.execute.call_args.args[0]
    assert outcomes == ["rollback"]
    commands.trigger_scheduled_task(task_id=TASK_ID, expected_revision=2, requested_at="2030-01-01T08:00:00+08:00")
    assert dispatch.call_args.kwargs["now"].hour == 0
    assert outcomes[-1] == "commit"


def test_pause_retains_exact_action_and_optimistic_revision(transaction):
    _, repo, outcomes = transaction
    repo.get.return_value = dict(name="归档", action="archive_thread", arguments={"tid": 572313},
        schedule_kind="cron", timezone="Asia/Shanghai", cron="0 8 * * *", at=None)
    commands.set_scheduled_task_enabled(task_id=TASK_ID, expected_revision=3, enabled=False)
    args = repo.update.call_args.kwargs
    assert args["expected_revision"] == 3 and args["enabled"] is False
    assert args["arguments"] == {"tid": 572313}
    assert outcomes == ["commit"]


def test_schedule_tools_are_discoverable_and_writes_require_approval():
    handlers = {name: handler for name, _, handler in agent_tools.PUBLIC_AGENT_TOOLS}
    for name in ("create_archive_schedule", "set_scheduled_task_enabled", "trigger_scheduled_task"):
        metadata = handlers[name].__capability_metadata__
        assert metadata["effect"] == "mutating" and metadata["risk"] == "write"
    for name in ("list_scheduled_tasks", "read_scheduled_task"):
        assert handlers[name].__capability_metadata__["effect"] == "read_only"
    model = public_input_model("create_archive_schedule")
    with pytest.raises(ValueError):
        model.model_validate({"name": "bad", "tid": 1, "schedule_kind": "cron", "cron": "0 * * * *", "action": "shell"})
