from __future__ import annotations

import json
from pathlib import Path

from yamibo_mcp.benchmark.hermes import (
    STANDARD_TASKS,
    load_exported_session,
    parse_session_transcript,
    score_task_run,
)


def _write_session(tmp_path: Path, payload: dict) -> Path:
    path = tmp_path / "session.jsonl"
    path.write_text(f"{json.dumps(payload, ensure_ascii=False)}\n", encoding="utf-8")
    return path


def test_parse_session_transcript_extracts_tool_calls_and_results(tmp_path):
    path = _write_session(
        tmp_path,
        {
            "id": "session-1",
            "messages": [
                {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "function": {
                                "name": "mcp_yamibo_local_read_forum_profiles",
                                "arguments": "{\"forum_id\": 30}",
                            }
                        }
                    ],
                },
                {
                    "role": "tool",
                    "tool_name": "mcp_yamibo_local_read_forum_profiles",
                    "content": (
                        "<untrusted_tool_result>"
                        "{\"structuredContent\":{\"ok\":true,\"data\":{\"forums\":[{\"forum_id\":30}]}}}"
                        "</untrusted_tool_result>"
                    ),
                },
                {"role": "assistant", "content": "{\"forum_ids\": [30]}"},
            ],
        },
    )

    session = load_exported_session(path)
    transcript = parse_session_transcript(session)

    assert transcript.session_id == "session-1"
    assert transcript.tool_calls[0].tool_name == "read_forum_profiles"
    assert transcript.tool_calls[0].arguments == {"forum_id": 30}
    assert transcript.tool_results[0].tool_name == "read_forum_profiles"
    assert transcript.tool_results[0].structured_content["data"]["forums"][0]["forum_id"] == 30


def test_scores_duplicate_archive_job_task_from_transcript(tmp_path):
    task = next(task for task in STANDARD_TASKS if task.task_id == "duplicate_archive_job_reuse")
    path = _write_session(
        tmp_path,
        {
            "id": "session-2",
            "messages": [
                {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "function": {
                                "name": "mcp_yamibo_local_create_thread_archive_job",
                                "arguments": "{\"tid\": 572313}",
                            }
                        }
                    ],
                },
                {
                    "role": "tool",
                    "tool_name": "mcp_yamibo_local_create_thread_archive_job",
                    "content": (
                        "<untrusted_tool_result>"
                        "{\"structuredContent\":{\"ok\":true,\"data\":{\"job_id\":\"job-1\",\"created\":true}}}"
                        "</untrusted_tool_result>"
                    ),
                },
                {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "function": {
                                "name": "mcp_yamibo_local_create_thread_archive_job",
                                "arguments": "{\"tid\": 572313}",
                            }
                        }
                    ],
                },
                {
                    "role": "tool",
                    "tool_name": "mcp_yamibo_local_create_thread_archive_job",
                    "content": (
                        "<untrusted_tool_result>"
                        "{\"structuredContent\":{\"ok\":true,\"data\":{\"job_id\":\"job-1\",\"created\":false}}}"
                        "</untrusted_tool_result>"
                    ),
                },
                {"role": "assistant", "content": "{\"reused\": true}"},
            ],
        },
    )

    session = load_exported_session(path)
    transcript = parse_session_transcript(session)
    scored = score_task_run(task, transcript)

    assert scored.passed is True
    assert scored.summary["job_reuse_confirmed"] is True
    assert scored.score == scored.max_score


def test_scores_duplicate_archive_job_task_when_live_job_already_exists(tmp_path):
    task = next(task for task in STANDARD_TASKS if task.task_id == "duplicate_archive_job_reuse")
    path = _write_session(
        tmp_path,
        {
            "id": "session-3",
            "messages": [
                {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "function": {
                                "name": "mcp_yamibo_local_create_thread_archive_job",
                                "arguments": "{\"tid\": 572313}",
                            }
                        }
                    ],
                },
                {
                    "role": "tool",
                    "tool_name": "mcp_yamibo_local_create_thread_archive_job",
                    "content": (
                        "<untrusted_tool_result>"
                        "{\"structuredContent\":{\"ok\":true,\"data\":{\"job_id\":\"job-2\",\"created\":false}}}"
                        "</untrusted_tool_result>"
                    ),
                },
                {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "function": {
                                "name": "mcp_yamibo_local_create_thread_archive_job",
                                "arguments": "{\"tid\": 572313}",
                            }
                        }
                    ],
                },
                {
                    "role": "tool",
                    "tool_name": "mcp_yamibo_local_create_thread_archive_job",
                    "content": (
                        "<untrusted_tool_result>"
                        "{\"structuredContent\":{\"ok\":true,\"data\":{\"job_id\":\"job-2\",\"created\":false}}}"
                        "</untrusted_tool_result>"
                    ),
                },
            ],
        },
    )

    session = load_exported_session(path)
    transcript = parse_session_transcript(session)
    scored = score_task_run(task, transcript)

    assert scored.passed is True
    assert scored.summary["job_reuse_confirmed"] is True
    assert scored.summary["reuse_mode"] == "existing_live_job"
