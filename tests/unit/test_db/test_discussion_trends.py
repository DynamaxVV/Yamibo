from __future__ import annotations

import pytest

from yamibo_mcp.db.repositories.discussion_trends import DiscussionTrendRepository, ensure_postgres


class _Result:
    def __init__(self, row=None):
        self._row = row

    def one_or_none(self):
        return self._row

    def fetchall(self):
        return [] if self._row is None else [self._row]


class _Conn:
    backend = "postgres"

    def __init__(self):
        self.calls = []
        self.rows = {}

    def execute(self, statement, parameters=None):
        self.calls.append((statement, parameters))
        sql = str(statement)
        if "SELECT * FROM discussion_index_runs WHERE run_id = :run_id" in sql:
            return _Result(self.rows.get(parameters["run_id"]))
        if "FROM discussion_current_indexes c" in sql:
            return _Result(self.rows.get((parameters["forum_id"], parameters["start_date"], parameters["end_date"], parameters["version"])))
        return _Result()

    def commit(self):
        self.calls.append(("commit", None))


class _FakeRow(dict):
    def __getitem__(self, key):
        return super().__getitem__(key)


def test_ensure_postgres_rejects_non_postgres_backend():
    class Conn:
        backend = "sqlite"

    with pytest.raises(ValueError, match="discussion trends require postgres backend"):
        ensure_postgres(Conn())


def test_repository_create_run_uses_expected_insert_shape(monkeypatch):
    conn = _Conn()
    repo = DiscussionTrendRepository(conn)
    repo.get_run = lambda run_id: None  # type: ignore[method-assign]

    result = repo.create_run(
        run_id="run-1",
        forum_id=5,
        start_date="2024-01-01",
        end_date="2024-01-31",
        version="v1",
        status="running",
    )

    statement, params = conn.calls[0]
    assert "INSERT INTO discussion_index_runs" in str(statement)
    assert params["forum_id"] == 5
    assert params["status"] == "running"
    assert result is None


def test_topic_floor_evidence_supports_thread_level_assignments():
    conn = _Conn()
    repo = DiscussionTrendRepository(conn)

    repo.get_topic_floor_evidence(run_id="run-1", topic_id=10, forum_id=5)

    statement, params = conn.calls[0]
    sql = str(statement)
    assert "a.pid IS NULL AND f.tid = a.tid" in sql
    assert "f.pid, f.floor_no" in sql
    assert params["run_id"] == "run-1"
