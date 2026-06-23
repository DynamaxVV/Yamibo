from __future__ import annotations

import time
from dataclasses import dataclass

from yamibo_mcp.db.repositories.jobs import JobsRepository


@dataclass
class HeartbeatPacer:
    repo: JobsRepository
    job_id: str
    worker_id: str
    lease_seconds: int
    min_interval_seconds: float
    _last_heartbeat: float = 0.0

    def beat(self, *, force: bool = False) -> None:
        now = time.monotonic()
        if not force and self._last_heartbeat > 0 and (now - self._last_heartbeat) < self.min_interval_seconds:
            return
        self.repo.heartbeat(self.job_id, self.worker_id, self.lease_seconds)
        self._last_heartbeat = now
