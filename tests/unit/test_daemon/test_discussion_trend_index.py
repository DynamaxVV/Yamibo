"""Unit tests for the discussion_trend_index handler (V1, Step 03).

Coverage:
- payload validation (forum_id, dates, version, retention, thresholds)
- failed builder does NOT update the current pointer
- second successful run supersedes the previous current
- retention preserves the current run, drops older succeeded + failed runs
- retention: keep_succeeded_runs=N keeps at most N most recent succeeded
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

import pytest

from yamibo_mcp.application.discussion_trend_builder import BuildResult
from yamibo_mcp.daemon.handlers.discussion_trend_index import handle_discussion_trend_index
from yamibo_mcp.domain.models import Job


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _Result:
    def __init__(self, row: Any = None, rows: list[Any] | None = None) -> None:
        self._row = row
        self._rows = rows or []
        self.rowcount = 0

    def one_or_none(self) -> Any:
        return self._row

    def fetchall(self) -> list[Any]:
        return list(self._rows)

    def first(self) -> Any:
        return self._rows[0] if self._rows else None


class _FakeRow(dict):
    """Dict that supports .get / [] like a SQL row."""


class _FakeConn:
    """Fake DatabaseConnection backend with explicit scenario control.

    Each scenario pre-stages:
    - existing_runs: rows in discussion_index_runs for the (forum_id, dates, version) window
    - existing_current: (run_id,) to return from get_current_run BEFORE the build
    - window_rows: rows returned by the builder's window SELECT
    - throw_on_insert: if set, executemany raises to simulate builder failure
    """

    def __init__(
        self,
        *,
        backend: str = "postgres",
        existing_runs: list[dict[str, Any]] | None = None,
        existing_current_run_id: str | None = None,
        window_rows: list[dict[str, Any]] | None = None,
        throw_on_insert: BaseException | None = None,
    ) -> None:
        self.backend = backend
        self.existing_runs = list(existing_runs or [])
        self.existing_current_run_id = existing_current_run_id
        self.window_rows = list(window_rows or [])
        self.throw_on_insert = throw_on_insert
        self.calls: list[tuple[str, str, Any]] = []
        self.executemany_calls: list[tuple[str, list[dict[str, Any]]]] = []
        self._inserts_by_table: dict[str, list[dict[str, Any]]] = {}
        self._next_topic_id = 0
        self._current_pointer: str | None = existing_current_run_id
        self.commit_count = 0

    # Connection API --------------------------------------------------

    def execute(self, statement: str, parameters: Any = None):
        sql = " ".join(str(statement).split())
        self.calls.append(("execute", sql, parameters))
        if sql.lstrip().startswith("SELECT pg_advisory_xact_lock"):
            return _Result()
        if "INSERT INTO discussion_index_runs" in sql:
            self.existing_runs.append(
                {
                    "run_id": parameters["run_id"],
                    "forum_id": parameters["forum_id"],
                    "start_date": parameters["start_date"],
                    "end_date": parameters["end_date"],
                    "version": parameters["version"],
                    "status": parameters["status"],
                    "started_at": parameters.get("started_at", "t0"),
                    "completed_at": None,
                    "superseded_at": None,
                    "metrics_json": parameters.get("metrics_json", {}) or {},
                    "warnings_json": parameters.get("warnings_json", []) or [],
                    "error_json": parameters.get("error_json", {}) or {},
                    "created_at": parameters.get("created_at", "t0"),
                    "updated_at": parameters.get("updated_at", "t0"),
                }
            )
            return _Result()
        if sql.lstrip().startswith("UPDATE discussion_index_runs"):
            run_id = parameters.get("run_id")
            for run in self.existing_runs:
                if run["run_id"] == run_id:
                    if "status = 'succeeded'" in sql:
                        run["status"] = "succeeded"
                        run["completed_at"] = parameters.get("completed_at", "t1")
                        run["metrics_json"] = parameters.get("metrics_json", run["metrics_json"])
                        run["warnings_json"] = parameters.get("warnings_json", run["warnings_json"])
                    elif "status = 'failed'" in sql:
                        run["status"] = "failed"
                        run["completed_at"] = parameters.get("completed_at", "t1")
                        run["error_json"] = parameters.get("error_json", run["error_json"])
                        run["warnings_json"] = parameters.get("warnings_json", run["warnings_json"])
                    elif "superseded_at" in sql:
                        if run["superseded_at"] is None:
                            run["superseded_at"] = parameters.get("superseded_at", "t1")
                    run["updated_at"] = parameters.get("updated_at", "t1")
            return _Result()
        if "INSERT INTO discussion_current_indexes" in sql:
            self._current_pointer = parameters["current_run_id"]
            return _Result()
        if "FROM discussion_current_indexes c" in sql:
            # get_current_run
            cid = self._current_pointer
            row = None
            if cid is not None:
                for run in self.existing_runs:
                    if run["run_id"] == cid:
                        row = _FakeRow(run)
                        break
            return _Result(row=row)
        if "SELECT * FROM discussion_index_runs" in sql and "forum_id" in (parameters or {}):
            # list_window_runs: return all existing runs that match the window,
            # sorted by completed_at DESC, run_id DESC (mirrors production ORDER BY).
            target_runs = []
            for r in self.existing_runs:
                if (
                    r.get("forum_id") == parameters["forum_id"]
                    and r.get("start_date") == parameters["start_date"]
                    and r.get("end_date") == parameters["end_date"]
                    and r.get("version") == parameters["version"]
                ):
                    target_runs.append(_FakeRow(r))
            target_runs.sort(
                key=lambda r: (r.get("completed_at") or r.get("started_at") or "", r.get("run_id") or ""),
                reverse=True,
            )
            return _Result(rows=target_runs)
        if "SELECT * FROM discussion_index_runs" in sql and "run_id" in (parameters or {}):
            for run in self.existing_runs:
                if run["run_id"] == parameters["run_id"]:
                    return _Result(row=_FakeRow(run))
            return _Result()
        if "SELECT topic_id, topic_key FROM discussion_topics" in sql:
            return _Result(rows=self._inserts_by_table.get("discussion_topics", []))
        if "FROM threads t" in sql:
            return _Result(rows=self.window_rows)
        if sql.lstrip().startswith("DELETE FROM"):
            # return rowcount for retention DELETEs
            table = sql.split("DELETE FROM ", 1)[1].split(" ", 1)[0]
            rows = self._inserts_by_table.get(table, [])
            matching = [r for r in rows if r.get("run_id") == parameters.get("run_id")]
            for r in matching:
                rows.remove(r)
            res = _Result()
            res.rowcount = len(matching)
            return res
        return _Result()

    def executemany(self, statement: str, seq_of_parameters: Any) -> Any:
        sql = str(statement)
        params_list = list(seq_of_parameters)
        self.executemany_calls.append((sql, params_list))
        if self.throw_on_insert is not None:
            raise self.throw_on_insert
        for table in (
            "discussion_topics",
            "discussion_topic_assignments",
            "discussion_partition_daily",
            "discussion_topic_daily",
            "discussion_user_daily",
        ):
            if f"INSERT INTO {table}" in sql:
                rows = self._inserts_by_table.setdefault(table, [])
                for params in params_list:
                    if table == "discussion_topics":
                        self._next_topic_id += 1
                        rows.append(
                            {
                                "topic_id": self._next_topic_id,
                                "topic_key": params["topic_key"],
                                "run_id": params.get("run_id"),
                            }
                        )
                    else:
                        rows.append(dict(params))
                return _Result()
        return _Result()

    def commit(self) -> None:
        self.commit_count += 1
        self.calls.append(("commit", "", None))

    def in_transaction(self) -> bool:  # type: ignore[no-redef]
        return False


class _JobsRepo:
    """Captures handler job lifecycle calls."""

    def __init__(self, conn: _FakeConn) -> None:
        self.conn = conn
        self.fail_calls: list[dict[str, Any]] = []
        self.partial_calls: list[dict[str, Any]] = []
        self.succeed_calls: list[dict[str, Any]] = []
        self.stage_calls: list[dict[str, Any]] = []
        self.heartbeat_calls: list[dict[str, Any]] = []
        self.fail_completed = False
        self.partial_completed = False
        self.succeed_completed = False

    def update_stage(self, job_id, stage, *, progress_current=None, progress_total=None):
        self.stage_calls.append(
            {
                "job_id": job_id,
                "stage": stage,
                "progress_current": progress_current,
                "progress_total": progress_total,
            }
        )

    def heartbeat(self, job_id, worker_id, lease_seconds):
        self.heartbeat_calls.append({"job_id": job_id, "worker_id": worker_id})

    def fail(self, job_id, error_code, error_message, artifacts=None):
        self.fail_calls.append(
            {
                "job_id": job_id,
                "error_code": error_code,
                "error_message": error_message,
                "artifacts": artifacts or {},
            }
        )
        self.fail_completed = True

    def partial(self, job_id, artifacts=None):
        self.partial_calls.append({"job_id": job_id, "artifacts": artifacts or {}})
        self.partial_completed = True

    def succeed(self, job_id, artifacts=None):
        self.succeed_calls.append({"job_id": job_id, "artifacts": artifacts or {}})
        self.succeed_completed = True


def _make_job(job_id: str, payload: dict[str, Any] | None) -> Job:
    return Job(
        job_id=job_id,
        job_type="discussion_trend_index",
        status="running",
        stage="validate",
        tid=None,
        payload=payload or {},
        progress_current=0,
        progress_total=None,
        worker_id="worker-1",
        heartbeat_at="t0",
        lease_until="t1",
        retry_count=0,
        max_retries=0,
        resumable=True,
        error_code=None,
        error_message=None,
        artifacts={},
        paused_at=None,
        created_at="t0",
        updated_at="t0",
        finished_at=None,
    )


def _settings_stub():
    class _S:
        pass

    return _S()


def _row(
    tid: int,
    pid: int,
    floor_no: int,
    pub: datetime,
    *,
    publisher: str = "u",
    publisher_uid: str = "uid1",
    category: str = "anime",
    phrase: str = "测试轻小说",
) -> dict[str, Any]:
    return {
        "tid": tid,
        "forum_id": 5,
        "category": category,
        "display_title": phrase,
        "raw_title": phrase,
        "thread_publisher": publisher,
        "thread_publisher_uid": publisher_uid,
        "thread_pub_time": pub,
        "pid": pid,
        "floor_no": floor_no,
        "floor_publisher": publisher,
        "floor_publisher_uid": publisher_uid,
        "floor_pub_time": pub,
        "quote_text": None,
        "reply_text": None,
        "core_title_guess": phrase,
        "normalized_core_title": phrase,
    }


def _big_window(base: datetime) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for t in range(3):
        for f in range(8):
            rows.append(
                _row(
                    1000 + t,
                    (t + 1) * 100 + f,
                    f + 1,
                    base.replace(hour=10 + (f % 8)),
                    publisher=f"u{t * 8 + f}",
                    publisher_uid=f"uid{t * 8 + f}",
                )
            )
    return rows


# ---------------------------------------------------------------------------
# payload validation
# ---------------------------------------------------------------------------


def test_handler_payload_validation_missing_forum_id_fails_job():
    conn = _FakeConn()
    repo = _JobsRepo(conn)
    job = _make_job("job-1", {"start_date": "2024-01-01", "end_date": "2024-01-31"})
    handle_discussion_trend_index(repo, job, "w1", 60, _settings_stub())
    assert repo.fail_completed
    assert repo.fail_calls[0]["error_code"] == "DISCUSSION_TREND_INVALID_PAYLOAD"
    assert "forum_id" in repo.fail_calls[0]["error_message"]
    assert conn._current_pointer is None  # never touched


def test_handler_payload_validation_inverted_window_fails_job():
    conn = _FakeConn()
    repo = _JobsRepo(conn)
    job = _make_job("job-1", {"forum_id": 5, "start_date": "2024-02-01", "end_date": "2024-01-01"})
    handle_discussion_trend_index(repo, job, "w1", 60, _settings_stub())
    assert repo.fail_completed
    assert repo.fail_calls[0]["error_code"] == "DISCUSSION_TREND_INVALID_PAYLOAD"
    assert conn._current_pointer is None


def test_handler_payload_validation_bad_retention_fails_job():
    conn = _FakeConn()
    repo = _JobsRepo(conn)
    job = _make_job(
        "job-1",
        {"forum_id": 5, "start_date": "2024-01-01", "end_date": "2024-01-31", "retention_success_runs": -1},
    )
    handle_discussion_trend_index(repo, job, "w1", 60, _settings_stub())
    assert repo.fail_completed
    assert repo.fail_calls[0]["error_code"] == "DISCUSSION_TREND_INVALID_PAYLOAD"
    assert "retention_success_runs" in repo.fail_calls[0]["error_message"]


def test_handler_non_postgres_backend_fails_job():
    conn = _FakeConn(backend="sqlite")
    repo = _JobsRepo(conn)
    job = _make_job("job-1", {"forum_id": 5, "start_date": "2024-01-01", "end_date": "2024-01-31"})
    handle_discussion_trend_index(repo, job, "w1", 60, _settings_stub())
    assert repo.fail_completed
    assert repo.fail_calls[0]["error_code"] == "DISCUSSION_TREND_POSTGRES_REQUIRED"


# ---------------------------------------------------------------------------
# happy path
# ---------------------------------------------------------------------------


def test_handler_succeeds_and_publishes_current_pointer():
    conn = _FakeConn(window_rows=_big_window(datetime(2024, 1, 10, 12, tzinfo=timezone.utc)))
    repo = _JobsRepo(conn)
    job = _make_job(
        "trend-run-1",
        {"forum_id": 5, "start_date": "2024-01-01", "end_date": "2024-01-31"},
    )
    handle_discussion_trend_index(repo, job, "w1", 60, _settings_stub())
    assert repo.succeed_completed, repo.fail_calls
    assert not repo.fail_completed
    assert conn._current_pointer == "trend-run-1"
    stages = [s["stage"] for s in repo.stage_calls]
    assert stages == ["validate", "lock", "build", "verify", "publish", "cleanup"]
    assert repo.succeed_calls[0]["artifacts"]["run_id"] == "trend-run-1"
    assert repo.succeed_calls[0]["artifacts"]["previous_current_run_id"] is None


# ---------------------------------------------------------------------------
# failed builder must not update current
# ---------------------------------------------------------------------------


def test_handler_failed_builder_does_not_update_current_pointer(monkeypatch):
    # Existing run is the current; new build fails → current must remain.
    existing = {
        "run_id": "old-run",
        "forum_id": 5,
        "start_date": "2024-01-01",
        "end_date": "2024-01-31",
        "version": "trend-v1",
        "status": "succeeded",
        "started_at": "t0",
        "completed_at": "t0",
        "superseded_at": None,
        "metrics_json": {},
        "warnings_json": [],
        "error_json": {},
        "created_at": "t0",
        "updated_at": "t0",
    }
    conn = _FakeConn(
        existing_runs=[existing],
        existing_current_run_id="old-run",
        window_rows=_big_window(datetime(2024, 1, 10, 12, tzinfo=timezone.utc)),
        throw_on_insert=RuntimeError("simulated DB failure during insert"),
    )
    repo = _JobsRepo(conn)
    job = _make_job("new-run", {"forum_id": 5, "start_date": "2024-01-01", "end_date": "2024-01-31"})
    handle_discussion_trend_index(repo, job, "w1", 60, _settings_stub())

    assert repo.fail_completed
    assert repo.fail_calls[0]["error_code"] == "DISCUSSION_TREND_BUILD_FAILED"
    # current pointer must remain on old-run
    assert conn._current_pointer == "old-run"
    # and the new run must be marked failed
    new = next(r for r in conn.existing_runs if r["run_id"] == "new-run")
    assert new["status"] == "failed"
    # the previous current must NOT have been marked superseded
    assert existing["superseded_at"] is None


# ---------------------------------------------------------------------------
# second successful run supersedes the first
# ---------------------------------------------------------------------------


def test_second_successful_run_marks_previous_current_as_superseded():
    first = {
        "run_id": "first-run",
        "forum_id": 5,
        "start_date": "2024-01-01",
        "end_date": "2024-01-31",
        "version": "trend-v1",
        "status": "succeeded",
        "started_at": "t0",
        "completed_at": "t0",
        "superseded_at": None,
        "metrics_json": {},
        "warnings_json": [],
        "error_json": {},
        "created_at": "t0",
        "updated_at": "t0",
    }
    conn = _FakeConn(
        existing_runs=[first],
        existing_current_run_id="first-run",
        window_rows=_big_window(datetime(2024, 1, 10, 12, tzinfo=timezone.utc)),
    )
    repo = _JobsRepo(conn)
    job = _make_job(
        "second-run",
        {"forum_id": 5, "start_date": "2024-01-01", "end_date": "2024-01-31"},
    )
    handle_discussion_trend_index(repo, job, "w1", 60, _settings_stub())

    assert repo.succeed_completed, repo.fail_calls
    # second-run is now current
    assert conn._current_pointer == "second-run"
    # and the first run has been marked superseded
    first_after = next(r for r in conn.existing_runs if r["run_id"] == "first-run")
    assert first_after["superseded_at"] is not None
    assert repo.succeed_calls[0]["artifacts"]["previous_current_run_id"] == "first-run"


# ---------------------------------------------------------------------------
# retention
# ---------------------------------------------------------------------------


def _preload_retention_scenario(
    *,
    keep: int,
) -> tuple[_FakeConn, dict[str, str]]:
    """Build a scenario with 4 succeeded + 1 failed + 1 new = 6 total runs."""
    runs = [
        _FakeRow(
            {
                "run_id": "run-1",
                "forum_id": 5,
                "start_date": "2024-01-01",
                "end_date": "2024-01-31",
                "version": "trend-v1",
                "status": "succeeded",
                "started_at": "t0",
                "completed_at": "t0",
                "superseded_at": "t0",
                "metrics_json": {},
                "warnings_json": [],
                "error_json": {},
                "created_at": "t0",
                "updated_at": "t0",
            }
        ),
        _FakeRow(
            {
                "run_id": "run-2",
                "forum_id": 5,
                "start_date": "2024-01-01",
                "end_date": "2024-01-31",
                "version": "trend-v1",
                "status": "succeeded",
                "started_at": "t0",
                "completed_at": "t1",
                "superseded_at": "t1",
                "metrics_json": {},
                "warnings_json": [],
                "error_json": {},
                "created_at": "t0",
                "updated_at": "t1",
            }
        ),
        _FakeRow(
            {
                "run_id": "run-3",
                "forum_id": 5,
                "start_date": "2024-01-01",
                "end_date": "2024-01-31",
                "version": "trend-v1",
                "status": "succeeded",
                "started_at": "t0",
                "completed_at": "t2",
                "superseded_at": "t2",
                "metrics_json": {},
                "warnings_json": [],
                "error_json": {},
                "created_at": "t0",
                "updated_at": "t2",
            }
        ),
        _FakeRow(
            {
                "run_id": "run-4-failed",
                "forum_id": 5,
                "start_date": "2024-01-01",
                "end_date": "2024-01-31",
                "version": "trend-v1",
                "status": "failed",
                "started_at": "t0",
                "completed_at": "t3",
                "superseded_at": None,
                "metrics_json": {},
                "warnings_json": [],
                "error_json": {"code": "boom"},
                "created_at": "t0",
                "updated_at": "t3",
            }
        ),
    ]
    return _FakeConn(
        existing_runs=list(runs),
        existing_current_run_id="run-3",
        window_rows=_big_window(datetime(2024, 1, 10, 12, tzinfo=timezone.utc)),
    ), {}


def test_retention_preserves_current_and_recent_runs():
    conn, _ = _preload_retention_scenario(keep=3)
    # Stage detail-table rows so retention has something to delete.
    conn._inserts_by_table["discussion_topic_assignments"] = [
        {"run_id": "run-1", "topic_id": 1},
        {"run_id": "run-2", "topic_id": 2},
        {"run_id": "run-4-failed", "topic_id": 3},
    ]
    conn._inserts_by_table["discussion_rag_chunk_topics"] = [
        {"run_id": "run-1", "topic_id": 1, "chunk_id": "c1"},
        {"run_id": "run-4-failed", "topic_id": 3, "chunk_id": "c4"},
    ]

    repo = _JobsRepo(conn)
    job = _make_job(
        "new-run",
        {
            "forum_id": 5,
            "start_date": "2024-01-01",
            "end_date": "2024-01-31",
            "retention_success_runs": 2,
        },
    )
    handle_discussion_trend_index(repo, job, "w1", 60, _settings_stub())

    assert repo.succeed_completed, repo.fail_calls
    retention = repo.succeed_calls[0]["artifacts"]["retention"]
    # With keep=2: keep {new-run, run-3} → 2 succeeded preserved.
    # Pruned: run-1, run-2 (succeeded, older), run-4-failed (failed).
    assert retention["runs_pruned"] == 3
    assert retention["deleted_assignments"] == 3
    assert retention["deleted_rag_chunk_topics"] == 2
    # current must still point to new-run
    assert conn._current_pointer == "new-run"
    # Retention must NOT issue DELETE for daily mart tables (those are owned by
    # the builder, which only deletes for the current run).
    pruned_ids = {"run-1", "run-2", "run-4-failed"}
    for entry in conn.calls:
        if entry[0] != "execute":
            continue
        sql, params = entry[1], entry[2]
        if "DELETE FROM discussion_partition_daily" in sql or "DELETE FROM discussion_topic_daily" in sql or "DELETE FROM discussion_user_daily" in sql:
            assert params.get("run_id") not in pruned_ids, f"retention must not delete daily mart for pruned run: {sql} {params}"
    # run-1 and run-2 succeeded runs MUST have been marked superseded (by publish step)
    for run_id in ("run-1", "run-2"):
        run = next(r for r in conn.existing_runs if r["run_id"] == run_id)
        assert run["superseded_at"] is not None


def test_retention_with_keep_one_keeps_only_current():
    conn, _ = _preload_retention_scenario(keep=1)
    conn._inserts_by_table["discussion_topic_assignments"] = [
        {"run_id": "run-1", "topic_id": 1},
        {"run_id": "run-2", "topic_id": 2},
    ]
    conn._inserts_by_table["discussion_rag_chunk_topics"] = [
        {"run_id": "run-2", "topic_id": 2, "chunk_id": "c2"},
    ]

    repo = _JobsRepo(conn)
    job = _make_job(
        "new-run",
        {
            "forum_id": 5,
            "start_date": "2024-01-01",
            "end_date": "2024-01-31",
            "retention_success_runs": 1,
        },
    )
    handle_discussion_trend_index(repo, job, "w1", 60, _settings_stub())

    assert repo.succeed_completed
    retention = repo.succeed_calls[0]["artifacts"]["retention"]
    # keep=1 → only new-run preserved; run-1, run-2, run-3, run-4-failed all pruned.
    assert retention["runs_pruned"] == 4
    assert retention["deleted_assignments"] == 2
    assert retention["deleted_rag_chunk_topics"] == 1


def test_retention_does_not_delete_current_run_data():
    conn, _ = _preload_retention_scenario(keep=3)
    # Insert data for the about-to-be-current run.
    conn._inserts_by_table["discussion_topic_assignments"] = [
        {"run_id": "new-run", "topic_id": 99},
        {"run_id": "run-1", "topic_id": 1},
    ]
    conn._inserts_by_table["discussion_rag_chunk_topics"] = [
        {"run_id": "new-run", "topic_id": 99, "chunk_id": "c-new"},
    ]

    repo = _JobsRepo(conn)
    job = _make_job(
        "new-run",
        {
            "forum_id": 5,
            "start_date": "2024-01-01",
            "end_date": "2024-01-31",
            "retention_success_runs": 2,
        },
    )
    handle_discussion_trend_index(repo, job, "w1", 60, _settings_stub())

    assert repo.succeed_completed
    # current's data must still be present (any row with run_id=new-run).
    current_assignments = [r for r in conn._inserts_by_table["discussion_topic_assignments"] if r.get("run_id") == "new-run"]
    current_rag = [r for r in conn._inserts_by_table["discussion_rag_chunk_topics"] if r.get("run_id") == "new-run"]
    assert current_assignments, f"new-run assignments were deleted: {conn._inserts_by_table['discussion_topic_assignments']}"
    assert current_rag, f"new-run rag_chunk_topics were deleted: {conn._inserts_by_table['discussion_rag_chunk_topics']}"
