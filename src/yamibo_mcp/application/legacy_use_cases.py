from __future__ import annotations

from yamibo_mcp.application.job_use_cases import get_job_status_payload
from yamibo_mcp.application.thread_use_cases import archive_thread_job, ensure_thread


def get_thread(*, tid: int, url: str | None = None, base_url: str | None = None, forum_id: int | None = None) -> dict[str, object]:
    return ensure_thread(tid=tid, url=url, base_url=base_url, forum_id=forum_id)


__all__ = ["archive_thread_job", "ensure_thread", "get_job_status_payload", "get_thread"]
