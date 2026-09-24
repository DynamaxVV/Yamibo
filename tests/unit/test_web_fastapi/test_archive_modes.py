from unittest.mock import patch

import pytest

from yamibo_mcp.application.contracts import AgentResult
from yamibo_mcp.db.repositories.jobs import JobsRepository
from yamibo_mcp.web_fastapi.routers.threads import export_thread, resync_thread, resync_threads_batch
from fastapi import HTTPException


@pytest.mark.parametrize("mode", ["text_only", "full"])
def test_web_archive_batch_passes_mode(client, mode):
    with patch("yamibo_mcp.web_fastapi.routers.threads.create_thread_archive_batch_jobs", return_value=AgentResult(ok=True, data={"requested_mode": mode, "created_count": 1})) as command:
        response = client.post("/api/threads/archive-batch", json={"tids": [42], "mode": mode})
    assert response.status_code == 200
    assert response.json()["requested_mode"] == mode
    assert command.call_args.kwargs["mode"] == mode


def test_web_archive_batch_defaults_to_full(client):
    with patch("yamibo_mcp.web_fastapi.routers.threads.create_thread_archive_batch_jobs", return_value=AgentResult(ok=True, data={})) as command:
        response = client.post("/api/threads/archive-batch", json={"tids": [42]})
    assert response.status_code == 200
    assert command.call_args.kwargs["mode"] == "full"


@pytest.mark.parametrize("mode", [None, "", "FULL", [], 7])
def test_web_archive_batch_rejects_invalid_mode(client, mode):
    response = client.post("/api/threads/archive-batch", json={"tids": [42], "mode": mode})
    assert response.status_code == 400
    assert "mode must" in response.json()["detail"]


def test_resync_keeps_existing_text_only_mode(db):
    db.execute("INSERT INTO threads (tid, raw_title, archive_status, capture_mode) VALUES (42, 'Comic', 'complete', 'text_only')")
    result = resync_thread({"tid": 42}, conn=db)
    job = JobsRepository(db).get(result["job_id"])
    assert job.payload["mode"] == "text_only"


def test_batch_resync_keeps_each_threads_mode(db):
    db.execute("INSERT INTO threads (tid, raw_title, archive_status, capture_mode) VALUES (42, 'Text', 'complete', 'text_only')")
    db.execute("INSERT INTO threads (tid, raw_title, archive_status, capture_mode) VALUES (43, 'Full', 'complete', 'full')")
    with patch("yamibo_mcp.web_fastapi.routers.threads.ensure_remote_access_allowed"):
        result = resync_threads_batch({"tids": [42, 43]}, conn=db)
    modes = {JobsRepository(db).get(job_id).tid: JobsRepository(db).get(job_id).payload["mode"] for job_id in result["created_job_ids"]}
    assert modes == {42: "text_only", 43: "full"}


def test_web_export_rejects_text_only_before_queuing(db):
    db.execute("INSERT INTO threads (tid, raw_title, archive_status, capture_mode) VALUES (42, 'Comic', 'complete', 'text_only')")
    with pytest.raises(HTTPException) as exc:
        export_thread({"tid": 42}, conn=db, settings=None)
    assert exc.value.status_code == 400
    assert JobsRepository(db).list() == []
