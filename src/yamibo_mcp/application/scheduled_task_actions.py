"""Explicit scheduled action allowlist; no prompt, shell, path or URL execution."""
from __future__ import annotations

from typing import Any


def validate_action(action: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if action != "archive_thread":
        raise ValueError("unsupported scheduled action; allowed: archive_thread")
    if not isinstance(arguments, dict) or set(arguments) - {"tid", "mode", "forum_id"}:
        raise ValueError("archive_thread accepts only tid, mode and forum_id")
    result = dict(arguments)
    if type(result.get("tid")) is not int or result["tid"] <= 0:
        raise ValueError("tid must be a positive integer")
    if "forum_id" in result and (type(result["forum_id"]) is not int or result["forum_id"] <= 0):
        raise ValueError("forum_id must be a positive integer")
    result.setdefault("mode", "text_only")
    if result["mode"] not in {"text_only", "full"}:
        raise ValueError("mode must be text_only or full")
    return result


def execute_action(connection, action: str, arguments: dict[str, Any]) -> dict[str, Any]:
    from yamibo_mcp.application.archive_commands import archive_thread_job
    return archive_thread_job(connection=connection, **validate_action(action, arguments))
