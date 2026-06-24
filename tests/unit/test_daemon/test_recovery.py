from __future__ import annotations

from yamibo_mcp.daemon.recovery import recover_expired_jobs
from yamibo_mcp.db.repositories.jobs import JobsRepository
from yamibo_mcp.domain.enums import JobStatus


def test_recover_expired_jobs_handles_running_and_paused_jobs(db):
    # Arrange
    repo = JobsRepository(db)
    running = repo.create("noop")
    paused = repo.create("noop")
    repo.acquire(running.job_id, "worker-1", 300)
    repo.acquire(paused.job_id, "worker-2", 300)
    repo.pause(paused.job_id)
    db.execute(
        "UPDATE jobs SET lease_until = ? WHERE job_id = ?",
        ("2000-01-01T00:00:00+00:00", running.job_id),
    )
    db.execute(
        "UPDATE jobs SET lease_until = ?, worker_id = ? WHERE job_id = ?",
        ("2000-01-01T00:00:00+00:00", "worker-2", paused.job_id),
    )
    db.commit()

    # Act
    count = recover_expired_jobs(repo)

    # Assert
    assert count == 2
    recovered_running = repo.get(running.job_id)
    recovered_paused = repo.get(paused.job_id)
    assert recovered_running.status == JobStatus.INTERRUPTED
    assert recovered_paused.status == JobStatus.PAUSED
    assert recovered_paused.worker_id is None
    assert recovered_paused.lease_until is None
