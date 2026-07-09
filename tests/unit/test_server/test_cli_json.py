from __future__ import annotations

from datetime import datetime, timezone

from yamibo_mcp.server.cli import dump_json
from yamibo_mcp.server.cli import build_parser


def test_dump_json_serializes_datetime_values():
    rendered = dump_json(
        {
            "job_id": "job-1",
            "created_at": datetime(2026, 7, 6, 0, 53, 0, tzinfo=timezone.utc),
        }
    )

    assert '"created_at": "2026-07-06T00:53:00+00:00"' in rendered


def test_cli_registers_mcp_alignment_and_job_shortcuts():
    parser = build_parser()
    subparsers = next(
        action for action in parser._actions if getattr(action, "dest", None) == "command"
    )

    registered = set(subparsers.choices)

    assert {
        "create-thread-archive-job",
        "inspect-remote-thread",
        "probe-archived-threads",
        "ensure-thread-archived",
        "read-job-events",
        "wait-for-job",
    }.issubset(registered)


def test_cli_keeps_legacy_sync_aliases_registered():
    parser = build_parser()
    subparsers = next(
        action for action in parser._actions if getattr(action, "dest", None) == "command"
    )

    registered = set(subparsers.choices)

    assert {
        "create-sync-thread-job",
        "create-sync-thread-batch-jobs",
    }.issubset(registered)
