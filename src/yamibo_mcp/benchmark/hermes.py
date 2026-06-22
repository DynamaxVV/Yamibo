from __future__ import annotations

import argparse
import json
import re
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


SESSION_ID_RE = re.compile(r"\b\d{8}_\d{6}_[0-9a-f]+\b")
TOOL_RESULT_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


@dataclass(frozen=True)
class TaskCard:
    task_id: str
    title: str
    prompt: str
    tags: tuple[str, ...]
    timeout_seconds: int = 120
    max_score: int = 5


@dataclass(frozen=True)
class ToolCallRecord:
    tool_name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ToolResultRecord:
    tool_name: str
    structured_content: dict[str, Any]


@dataclass(frozen=True)
class SessionTranscript:
    session_id: str
    final_response: str
    tool_calls: list[ToolCallRecord]
    tool_results: list[ToolResultRecord]


@dataclass(frozen=True)
class TaskRunScore:
    task_id: str
    title: str
    passed: bool
    score: int
    max_score: int
    summary: dict[str, Any]
    findings: list[str]


@dataclass(frozen=True)
class TaskRunArtifact:
    task_id: str
    title: str
    prompt: str
    session_id: str | None
    stdout: str
    stderr: str
    transcript_path: str | None
    transcript: SessionTranscript | None
    score: TaskRunScore
    elapsed_seconds: float
    timed_out: bool


@dataclass(frozen=True)
class BenchmarkReport:
    generated_at: float
    hermes_command: str
    output_dir: str
    task_runs: list[TaskRunArtifact]


STANDARD_TASKS: tuple[TaskCard, ...] = (
    TaskCard(
        task_id="forum_profiles_discovery",
        title="Forum profiles discovery",
        prompt=(
            "Use the yamibo-local MCP tools only. Read the available forum profiles and "
            "return one compact JSON object with total_forums and a forum_ids array."
        ),
        tags=("readonly", "discovery"),
    ),
    TaskCard(
        task_id="multi_forum_readonly_browse",
        title="Multi-forum readonly browse",
        prompt=(
            "Use the yamibo-local MCP tools only. First read forum profiles, then browse "
            "forum 30 page 1 and forum 55 page 1. Return compact JSON with the two forum ids "
            "and the thread count seen on each page. Do not create any jobs."
        ),
        tags=("readonly", "multi_forum"),
    ),
    TaskCard(
        task_id="local_missing_recovery_plan",
        title="Local missing recovery plan",
        prompt=(
            "Use the yamibo-local MCP tools only. Try to read archived thread summary for tid "
            "999999. Do not create a job. Return compact JSON with ok, error_code, and "
            "recommended_next_tool if one is suggested."
        ),
        tags=("recovery", "readonly"),
    ),
    TaskCard(
        task_id="archive_job_launch_and_poll",
        title="Archive job launch and poll",
        prompt=(
            "Use the yamibo-local MCP tools only. Create an archive job for tid 572313, then "
            "read the job status once and return compact JSON with job_id, created, status, "
            "execution_state, needs_attention, is_terminal, result_ready, and "
            "recommended_poll_after_seconds."
        ),
        tags=("async_job", "polling"),
    ),
    TaskCard(
        task_id="duplicate_archive_job_reuse",
        title="Duplicate archive job reuse",
        prompt=(
            "Use the yamibo-local MCP tools only. Create an archive job for tid 572313 twice in "
            "the same session. Return compact JSON with first_job_id, second_job_id, "
            "first_created, second_created, and reused."
        ),
        tags=("idempotency", "async_job"),
    ),
    TaskCard(
        task_id="export_strategy_split",
        title="Export strategy split",
        prompt=(
            "Use the yamibo-local MCP tools only. Create an export job for tid 572313, then "
            "create another export job for tid 572313 with strategy force_resync. Return compact "
            "JSON with first_job_id, second_job_id, first_created, second_created, and "
            "same_job_id."
        ),
        tags=("payload_compatibility", "async_job"),
    ),
    TaskCard(
        task_id="multi_thread_fanout_pressure",
        title="Multi-thread fanout pressure",
        prompt=(
            "Use the yamibo-local MCP tools only. For tids 572313, 572448, and 572530 create "
            "archive jobs and read each job status once. Return compact JSON with an array of "
            "three entries containing tid, job_id, created, status, execution_state, and "
            "needs_attention. Also include unique_job_count."
        ),
        tags=("fanout", "pressure"),
        timeout_seconds=180,
    ),
)


def _normalize_tool_name(raw_name: str) -> str:
    if raw_name.startswith("mcp_"):
        parts = raw_name.split("_")
        if len(parts) >= 4:
            return "_".join(parts[3:])
    return raw_name


def _parse_tool_result_payload(content: str) -> dict[str, Any]:
    match = TOOL_RESULT_JSON_RE.search(content)
    if match is None:
        return {}
    try:
        payload = json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}
    structured = payload.get("structuredContent")
    return structured if isinstance(structured, dict) else payload


def load_exported_session(path: Path) -> dict[str, Any]:
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            return json.loads(line)
    raise ValueError(f"No session payload found in {path}")


def parse_session_transcript(session: dict[str, Any]) -> SessionTranscript:
    tool_calls: list[ToolCallRecord] = []
    tool_results: list[ToolResultRecord] = []
    final_response = ""
    for message in session.get("messages", []):
        if message.get("role") == "assistant":
            if message.get("content"):
                final_response = str(message["content"])
            for tool_call in message.get("tool_calls") or []:
                function = tool_call.get("function") or {}
                arguments_raw = function.get("arguments") or "{}"
                try:
                    arguments = json.loads(arguments_raw)
                except json.JSONDecodeError:
                    arguments = {}
                tool_calls.append(
                    ToolCallRecord(
                        tool_name=_normalize_tool_name(str(function.get("name") or "")),
                        arguments=arguments,
                    )
                )
        elif message.get("role") == "tool":
            tool_results.append(
                ToolResultRecord(
                    tool_name=_normalize_tool_name(str(message.get("tool_name") or "")),
                    structured_content=_parse_tool_result_payload(str(message.get("content") or "")),
                )
            )
    return SessionTranscript(
        session_id=str(session.get("id") or ""),
        final_response=final_response,
        tool_calls=tool_calls,
        tool_results=tool_results,
    )


def _find_latest_session_ids(hermes_command: str, *, limit: int = 30) -> list[str]:
    proc = subprocess.run(
        [hermes_command, "sessions", "list", "--limit", str(limit)],
        capture_output=True,
        text=True,
        check=True,
    )
    return SESSION_ID_RE.findall(proc.stdout)


def _run_oneshot(hermes_command: str, prompt: str, *, timeout_seconds: int) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            [hermes_command, "-z", prompt, "--yolo"],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return subprocess.CompletedProcess(
            args=exc.cmd,
            returncode=124,
            stdout=exc.stdout or "",
            stderr=(exc.stderr or "") + f"\nTask timed out after {timeout_seconds} seconds.",
        )


def _export_session(hermes_command: str, session_id: str, output_path: Path) -> None:
    subprocess.run(
        [hermes_command, "sessions", "export", str(output_path), "--session-id", session_id],
        capture_output=True,
        text=True,
        check=True,
    )


def _tool_calls_by_name(transcript: SessionTranscript, name: str) -> list[ToolCallRecord]:
    return [item for item in transcript.tool_calls if item.tool_name == name]


def _tool_results_by_name(transcript: SessionTranscript, name: str) -> list[ToolResultRecord]:
    return [item for item in transcript.tool_results if item.tool_name == name]


def _score_forum_profiles_discovery(task: TaskCard, transcript: SessionTranscript) -> TaskRunScore:
    findings: list[str] = []
    score = 0
    tool_names = [item.tool_name for item in transcript.tool_calls]
    if "read_forum_profiles" in tool_names:
        score += 3
    else:
        findings.append("missing read_forum_profiles call")
    if not any(name.startswith("create_thread_") for name in tool_names):
        score += 2
    else:
        findings.append("unexpected write tool call in readonly task")
    return TaskRunScore(task.task_id, task.title, score >= 4, score, task.max_score, {}, findings)


def _score_multi_forum_readonly_browse(task: TaskCard, transcript: SessionTranscript) -> TaskRunScore:
    findings: list[str] = []
    score = 0
    browse_calls = _tool_calls_by_name(transcript, "browse_forum_page")
    touched_forums = {call.arguments.get("forum_id") for call in browse_calls}
    if "read_forum_profiles" in [item.tool_name for item in transcript.tool_calls]:
        score += 1
    else:
        findings.append("missing read_forum_profiles discovery step")
    if {30, 55}.issubset(touched_forums):
        score += 3
    else:
        findings.append("did not browse both forum 30 and forum 55")
    if not any(item.tool_name.startswith("create_thread_") for item in transcript.tool_calls):
        score += 1
    else:
        findings.append("unexpected write tool call in readonly browse task")
    return TaskRunScore(
        task.task_id,
        task.title,
        score >= 4,
        score,
        task.max_score,
        {"forums_touched": sorted(forum_id for forum_id in touched_forums if forum_id is not None)},
        findings,
    )


def _score_local_missing_recovery_plan(task: TaskCard, transcript: SessionTranscript) -> TaskRunScore:
    findings: list[str] = []
    score = 0
    reads = _tool_results_by_name(transcript, "read_archived_thread")
    if reads:
        score += 2
    else:
        findings.append("missing read_archived_thread call")
    error_codes = [
        (result.structured_content.get("error") or {}).get("code")
        for result in reads
    ]
    if "LOCAL_ARCHIVE_NOT_FOUND" in error_codes:
        score += 2
    else:
        findings.append("did not surface LOCAL_ARCHIVE_NOT_FOUND")
    if not _tool_calls_by_name(transcript, "create_thread_archive_job"):
        score += 1
    else:
        findings.append("agent should not create a job in recovery-plan task")
    return TaskRunScore(
        task.task_id,
        task.title,
        score >= 4,
        score,
        task.max_score,
        {"error_codes": [code for code in error_codes if code]},
        findings,
    )


def _score_archive_job_launch_and_poll(task: TaskCard, transcript: SessionTranscript) -> TaskRunScore:
    findings: list[str] = []
    score = 0
    creates = _tool_results_by_name(transcript, "create_thread_archive_job")
    reads = _tool_results_by_name(transcript, "read_job")
    if creates:
        score += 2
    else:
        findings.append("missing create_thread_archive_job call")
    if reads:
        score += 2
    else:
        findings.append("missing read_job poll")
    attention_states = []
    if reads:
        payload = reads[-1].structured_content.get("data") or {}
        attention_states.append(payload.get("execution_state"))
        if "execution_state" in payload and "needs_attention" in payload:
            score += 1
        else:
            findings.append("read_job did not return execution diagnostics")
    return TaskRunScore(
        task.task_id,
        task.title,
        score >= 4,
        score,
        task.max_score,
        {"execution_states": [state for state in attention_states if state]},
        findings,
    )


def _score_duplicate_archive_job_reuse(task: TaskCard, transcript: SessionTranscript) -> TaskRunScore:
    findings: list[str] = []
    score = 0
    results = _tool_results_by_name(transcript, "create_thread_archive_job")
    job_ids = [((item.structured_content.get("data") or {}).get("job_id")) for item in results]
    created_flags = [((item.structured_content.get("data") or {}).get("created")) for item in results]
    if len(results) >= 2:
        score += 2
    else:
        findings.append("expected two archive job create attempts")
    reused = False
    reuse_mode = "missing"
    if len(job_ids) >= 2 and job_ids[0] == job_ids[1]:
        first_created, second_created = created_flags[:2]
        if second_created is False and first_created in {True, False}:
            reused = True
            reuse_mode = "existing_live_job" if first_created is False else "fresh_then_reused"
    if reused:
        score += 3
    else:
        findings.append("duplicate archive job was not clearly reused")
    return TaskRunScore(
        task.task_id,
        task.title,
        score >= 4,
        score,
        task.max_score,
        {
            "job_reuse_confirmed": reused,
            "job_ids": job_ids[:2],
            "created_flags": created_flags[:2],
            "reuse_mode": reuse_mode,
        },
        findings,
    )


def _score_export_strategy_split(task: TaskCard, transcript: SessionTranscript) -> TaskRunScore:
    findings: list[str] = []
    score = 0
    results = _tool_results_by_name(transcript, "create_thread_export_job")
    job_ids = [((item.structured_content.get("data") or {}).get("job_id")) for item in results]
    created_flags = [((item.structured_content.get("data") or {}).get("created")) for item in results]
    split_ok = len(job_ids) >= 2 and job_ids[0] != job_ids[1] and created_flags[:2] == [True, True]
    if len(results) >= 2:
        score += 2
    else:
        findings.append("expected two export job create attempts")
    if split_ok:
        score += 3
    else:
        findings.append("different export strategy did not produce a new job")
    return TaskRunScore(
        task.task_id,
        task.title,
        score >= 4,
        score,
        task.max_score,
        {"strategy_split_confirmed": split_ok, "job_ids": job_ids[:2], "created_flags": created_flags[:2]},
        findings,
    )


def _score_multi_thread_fanout_pressure(task: TaskCard, transcript: SessionTranscript) -> TaskRunScore:
    findings: list[str] = []
    score = 0
    create_calls = _tool_calls_by_name(transcript, "create_thread_archive_job")
    read_calls = _tool_calls_by_name(transcript, "read_job")
    tids = [call.arguments.get("tid") for call in create_calls if call.arguments.get("tid") is not None]
    unique_tids = {int(tid) for tid in tids}
    job_results = [
        (result.structured_content.get("data") or {})
        for result in _tool_results_by_name(transcript, "read_job")
    ]
    if unique_tids == {572313, 572448, 572530}:
        score += 2
    else:
        findings.append("did not fan out to all three target tids")
    if len(read_calls) >= 3:
        score += 2
    else:
        findings.append("did not read all job statuses after fanout")
    if len({payload.get("job_id") for payload in job_results if payload.get("job_id")}) >= 3:
        score += 1
    else:
        findings.append("job status payloads were not distinct for all fanout jobs")
    return TaskRunScore(
        task.task_id,
        task.title,
        score >= 4,
        score,
        task.max_score,
        {"fanout_tids": sorted(unique_tids), "job_status_count": len(job_results)},
        findings,
    )


_SCORERS = {
    "forum_profiles_discovery": _score_forum_profiles_discovery,
    "multi_forum_readonly_browse": _score_multi_forum_readonly_browse,
    "local_missing_recovery_plan": _score_local_missing_recovery_plan,
    "archive_job_launch_and_poll": _score_archive_job_launch_and_poll,
    "duplicate_archive_job_reuse": _score_duplicate_archive_job_reuse,
    "export_strategy_split": _score_export_strategy_split,
    "multi_thread_fanout_pressure": _score_multi_thread_fanout_pressure,
}


def score_task_run(task: TaskCard, transcript: SessionTranscript) -> TaskRunScore:
    return _SCORERS[task.task_id](task, transcript)


def _select_tasks(task_ids: list[str] | None) -> list[TaskCard]:
    if not task_ids:
        return list(STANDARD_TASKS)
    selected = []
    available = {task.task_id: task for task in STANDARD_TASKS}
    for task_id in task_ids:
        if task_id not in available:
            raise ValueError(f"Unknown task id: {task_id}")
        selected.append(available[task_id])
    return selected


def _wait_for_new_session_id(hermes_command: str, before_ids: set[str], *, retries: int = 10, delay_seconds: float = 1.0) -> str | None:
    for _ in range(retries):
        current = _find_latest_session_ids(hermes_command, limit=max(30, len(before_ids) + 10))
        new_ids = [session_id for session_id in current if session_id not in before_ids]
        if new_ids:
            return new_ids[0]
        time.sleep(delay_seconds)
    return None


def _render_markdown_report(report: BenchmarkReport) -> str:
    total_score = sum(item.score.score for item in report.task_runs)
    total_max = sum(item.score.max_score for item in report.task_runs)
    passed = sum(1 for item in report.task_runs if item.score.passed)
    lines = [
        "# Hermes Benchmark Report",
        "",
        f"- output_dir: `{report.output_dir}`",
        f"- hermes_command: `{report.hermes_command}`",
        f"- passed: `{passed}/{len(report.task_runs)}`",
        f"- total_score: `{total_score}/{total_max}`",
        "",
        "## Task Results",
    ]
    for item in report.task_runs:
        transcript_path = item.transcript_path or "-"
        findings = "; ".join(item.score.findings) if item.score.findings else "none"
        lines.extend(
            [
                f"### {item.title}",
                f"- task_id: `{item.task_id}`",
                f"- session_id: `{item.session_id or '-'}`",
                f"- score: `{item.score.score}/{item.score.max_score}`",
                f"- passed: `{item.score.passed}`",
                f"- timed_out: `{item.timed_out}`",
                f"- transcript: `{transcript_path}`",
                f"- elapsed_seconds: `{item.elapsed_seconds:.1f}`",
                f"- summary: `{json.dumps(item.score.summary, ensure_ascii=False, sort_keys=True)}`",
                f"- findings: `{findings}`",
                "",
            ]
        )
    return "\n".join(lines)


def _report_to_jsonable(report: BenchmarkReport) -> dict[str, Any]:
    return {
        "generated_at": report.generated_at,
        "hermes_command": report.hermes_command,
        "output_dir": report.output_dir,
        "task_runs": [
            {
                "task_id": item.task_id,
                "title": item.title,
                "prompt": item.prompt,
                "session_id": item.session_id,
                "stdout": item.stdout,
                "stderr": item.stderr,
                "transcript_path": item.transcript_path,
                "transcript": None if item.transcript is None else asdict(item.transcript),
                "score": asdict(item.score),
                "elapsed_seconds": item.elapsed_seconds,
                "timed_out": item.timed_out,
            }
            for item in report.task_runs
        ],
    }


def run_benchmark(
    *,
    output_dir: Path,
    hermes_command: str = "hermes",
    task_ids: list[str] | None = None,
) -> BenchmarkReport:
    output_dir.mkdir(parents=True, exist_ok=True)
    transcripts_dir = output_dir / "transcripts"
    transcripts_dir.mkdir(parents=True, exist_ok=True)
    task_runs: list[TaskRunArtifact] = []
    for task in _select_tasks(task_ids):
        before_ids = set(_find_latest_session_ids(hermes_command))
        started_at = time.time()
        proc = _run_oneshot(hermes_command, task.prompt, timeout_seconds=task.timeout_seconds)
        elapsed = time.time() - started_at
        timed_out = proc.returncode == 124
        session_id = _wait_for_new_session_id(hermes_command, before_ids)
        transcript = None
        transcript_path: Path | None = None
        if session_id is not None:
            transcript_path = transcripts_dir / f"{task.task_id}-{session_id}.jsonl"
            _export_session(hermes_command, session_id, transcript_path)
            transcript = parse_session_transcript(load_exported_session(transcript_path))
            score = score_task_run(task, transcript)
        else:
            score = TaskRunScore(
                task_id=task.task_id,
                title=task.title,
                passed=False,
                score=0,
                max_score=task.max_score,
                summary={},
                findings=["session id could not be located after hermes run"],
            )
        task_runs.append(
            TaskRunArtifact(
                task_id=task.task_id,
                title=task.title,
                prompt=task.prompt,
                session_id=session_id,
                stdout=proc.stdout,
                stderr=proc.stderr,
                transcript_path=None if transcript_path is None else str(transcript_path),
                transcript=transcript,
                score=score,
                elapsed_seconds=elapsed,
                timed_out=timed_out,
            )
        )
    report = BenchmarkReport(
        generated_at=time.time(),
        hermes_command=hermes_command,
        output_dir=str(output_dir),
        task_runs=task_runs,
    )
    (output_dir / "benchmark-report.json").write_text(
        json.dumps(_report_to_jsonable(report), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output_dir / "benchmark-report.md").write_text(_render_markdown_report(report), encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run repeatable Hermes MCP benchmark tasks.")
    parser.add_argument("--output-dir", default="artifacts/hermes-benchmark")
    parser.add_argument("--hermes-command", default="hermes")
    parser.add_argument("--tasks", nargs="*", help="Optional subset of task ids to run.")
    args = parser.parse_args(argv)

    run_benchmark(
        output_dir=Path(args.output_dir),
        hermes_command=args.hermes_command,
        task_ids=args.tasks,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
