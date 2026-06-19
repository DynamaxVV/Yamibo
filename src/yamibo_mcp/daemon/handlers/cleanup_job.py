from __future__ import annotations

from datetime import datetime, timedelta, timezone
import shutil
from pathlib import Path

from yamibo_mcp.config import Settings
from yamibo_mcp.db.repositories.jobs import JobsRepository
from yamibo_mcp.domain.models import Job
from yamibo_mcp.storage.paths import StoragePaths


def _older_than(path: Path, *, hours: int) -> bool:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc) < cutoff


def handle_cleanup_job(repo: JobsRepository, job: Job, worker_id: str, lease_seconds: int, settings: Settings) -> None:
    paths = StoragePaths(settings.data_dir)
    mode = str(job.payload.get("mode") or "job_staging").strip()
    repo.update_stage(job.job_id, "cleanup", progress_current=0, progress_total=1)
    repo.heartbeat(job.job_id, worker_id, lease_seconds)

    if mode == "job_staging":
        target_job_id = str(job.payload.get("job_id") or "").strip()
        if not target_job_id:
            raise ValueError("cleanup_job requires payload.job_id for job_staging mode")
        staging_dir = paths.staging_job_dir(target_job_id)
        removed = False
        if staging_dir.exists():
            shutil.rmtree(staging_dir)
            removed = True
        repo.succeed(
            job.job_id,
            {
                "mode": mode,
                "target_job_id": target_job_id,
                "removed_staging_dir": removed,
                "staging_dir": str(staging_dir),
            },
        )
        return

    if mode == "stale_staging":
        older_than_hours = int(job.payload.get("older_than_hours") or settings.cleanup_staging_older_than_hours)
        staging_root = settings.data_dir / "staging" / "jobs"
        removed_dirs: list[str] = []
        if staging_root.exists():
            for child in staging_root.iterdir():
                if child.is_dir() and _older_than(child, hours=older_than_hours):
                    shutil.rmtree(child)
                    removed_dirs.append(str(child))
        repo.succeed(
            job.job_id,
            {
                "mode": mode,
                "older_than_hours": older_than_hours,
                "removed_count": len(removed_dirs),
                "removed_dirs": removed_dirs[:100],
            },
        )
        return

    raise ValueError(f"unsupported cleanup mode: {mode}")
