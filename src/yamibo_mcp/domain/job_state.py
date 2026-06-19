from __future__ import annotations

from uuid import uuid4


def new_job_id(job_type: str) -> str:
    return f"{job_type}_{uuid4().hex[:16]}"
