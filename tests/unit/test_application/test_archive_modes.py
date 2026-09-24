from types import SimpleNamespace
from unittest.mock import patch

import pytest

from yamibo_mcp.application.archive_commands import create_thread_archive_batch_jobs, create_thread_archive_job, create_thread_export_job
from yamibo_mcp.db.repositories.jobs import JobsRepository
from yamibo_mcp.server.agent_tools import create_thread_archive_job as agent_archive


@pytest.fixture(autouse=True)
def archive_settings(tmp_path):
    with patch("yamibo_mcp.application.archive_commands.load_settings", return_value=SimpleNamespace(db_path=tmp_path / "unused.db")):
        yield


def _thread(db, *, mode="text_only", status="complete"):
    db.execute("INSERT INTO threads (tid, raw_title, archive_status, capture_mode) VALUES (?, ?, ?, ?)", (42, "Comic", status, mode))
    db.commit()


@pytest.mark.parametrize("mode", ["text_only", "full"])
def test_archive_mode_is_stored_and_reused(db, mode):
    first = create_thread_archive_job(tid=42, mode=mode, connection=db)
    second = create_thread_archive_job(tid=42, mode=mode, connection=db)
    assert first.data["created"] is True
    assert first.data["status"] == "queued"
    assert second.data["created"] is False
    assert second.data["job_id"] == first.data["job_id"]
    assert second.data["requested_mode"] == mode
    assert JobsRepository(db).get(first.data["job_id"]).payload["mode"] == mode


def test_default_full_reuses_legacy_payload(db):
    existing = JobsRepository(db).create("sync_thread", tid=42, payload={"tid": 42})
    result = create_thread_archive_job(tid=42, connection=db)
    assert result.data["job_id"] == existing.job_id
    assert result.data["requested_mode"] == "full"
    assert result.data["created"] is False


@pytest.mark.parametrize("mode", [None, "", "FULL", "invalid", [], 1])
def test_invalid_mode_rejected_before_side_effects(db, mode):
    with pytest.raises(ValueError, match="mode must"):
        create_thread_archive_job(tid=42, mode=mode, connection=db)
    with pytest.raises(ValueError, match="mode must"):
        create_thread_archive_batch_jobs(tids=[42], mode=mode, connection=db)
    assert JobsRepository(db).list() == []


def test_upgrade_queues_and_reuses_backfill(db):
    _thread(db)
    first = create_thread_archive_job(tid=42, mode="full", connection=db)
    second = create_thread_archive_job(tid=42, mode="full", connection=db)
    job = JobsRepository(db).get(first.data["job_id"])
    assert job.job_type == "image_backfill"
    assert job.payload == {"tid": 42, "dry_run": False, "scope": "selected", "include_first_floor": True, "upgrade_to_full": True}
    assert second.data["job_id"] == job.job_id
    assert second.data["created"] is False
    assert first.data["status"] == "queued"
    assert db.execute("SELECT capture_mode FROM threads WHERE tid = 42").fetchone()["capture_mode"] == "text_only"


def test_full_partial_with_missing_images_retries_backfill(db):
    _thread(db, mode="full", status="partial")
    db.execute("INSERT INTO assets (asset_id, tid, pid, asset_type, remote_url, status) VALUES ('image-1', 42, 420, 'image', 'https://example.com/1.jpg', 'missing')")
    result = create_thread_archive_job(tid=42, mode="full", connection=db)
    assert JobsRepository(db).get(result.data["job_id"]).job_type == "image_backfill"


@pytest.mark.parametrize("stored_mode", ["text_only", "full"])
def test_completed_archive_satisfies_text_request_without_jobs(db, stored_mode):
    _thread(db, mode=stored_mode)
    result = create_thread_archive_job(tid=42, mode="text_only", connection=db)
    assert result.data["status"] == "satisfied"
    assert result.data["job_id"] is None
    assert result.next_actions == []
    assert result.side_effects == []
    assert JobsRepository(db).list() == []
    assert db.execute("SELECT capture_mode FROM threads WHERE tid = 42").fetchone()["capture_mode"] == stored_mode


def test_partial_full_archive_satisfies_text_request_without_downgrading(db):
    _thread(db, mode="full", status="partial")
    result = create_thread_archive_job(tid=42, mode="text_only", connection=db)
    assert result.data["status"] == "satisfied"
    assert JobsRepository(db).list() == []
    assert db.execute("SELECT capture_mode FROM threads WHERE tid = 42").fetchone()["capture_mode"] == "full"


def test_text_only_export_requires_explicit_upgrade(db):
    _thread(db)
    with pytest.raises(ValueError, match="upgrade to full"):
        create_thread_export_job(tid=42, connection=db)
    assert JobsRepository(db).list() == []


def test_conflicting_mode_rejected_before_batch_creates_jobs(db):
    existing = JobsRepository(db).create("sync_thread", tid=42, payload={"tid": 42, "mode": "text_only"})
    with pytest.raises(ValueError, match="conflicting active archive job"):
        create_thread_archive_batch_jobs(tids=[43, 42], mode="full", connection=db)
    assert [job.job_id for job in JobsRepository(db).list()] == [existing.job_id]


def test_mixed_batch_reports_individual_jobs_and_mode(db):
    _thread(db)
    result = create_thread_archive_batch_jobs(tids=[42, 43, 43], mode="full", connection=db)
    assert result.data["target_count"] == 2
    assert result.data["created_count"] == 2
    assert result.data["requested_mode"] == "full"
    assert {job["job_type"] for job in result.data["jobs"]} == {"sync_thread", "image_backfill"}
    assert all(job["status"] == "queued" for job in result.data["jobs"])


def test_agent_mode_validation_is_structured():
    result = agent_archive(tid=42, mode="other")
    assert result["ok"] is False
    assert result["error"]["code"] == "INVALID_ARGUMENT"


def test_incomplete_text_archive_needs_full_sync_before_image_completion(db):
    _thread(db, status="partial")
    result = create_thread_archive_job(tid=42, mode="full", connection=db)
    assert JobsRepository(db).get(result.data["job_id"]).job_type == "sync_thread"


def test_archive_reuses_matching_job_even_when_different_full_payload_is_newer(db):
    repo = JobsRepository(db)
    first = repo.create("sync_thread", tid=42, payload={"tid": 42})
    later = repo.create("sync_thread", tid=42, payload={"tid": 42, "mode": "full", "forum_id": 30})
    db.execute("UPDATE jobs SET created_at = '2026-09-24T10:00:00Z' WHERE job_id = ?", (first.job_id,))
    db.execute("UPDATE jobs SET created_at = '2026-09-24T11:00:00Z' WHERE job_id = ?", (later.job_id,))
    result = create_thread_archive_job(tid=42, connection=db)
    assert result.data["job_id"] == first.job_id
    assert result.data["created"] is False
    assert len(repo.list()) == 2
