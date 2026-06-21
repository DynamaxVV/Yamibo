from __future__ import annotations

from yamibo_mcp.domain.enums import JobType
from yamibo_mcp.domain.models import Job
from yamibo_mcp.daemon.handlers.cleanup_job import handle_cleanup_job
from yamibo_mcp.daemon.handlers.export_thread import handle_export_thread
from yamibo_mcp.daemon.handlers.noop import handle_noop
from yamibo_mcp.daemon.handlers.sync_thread import handle_sync_thread
from yamibo_mcp.daemon.handlers.update_thread import handle_update_thread
from yamibo_mcp.daemon.handlers.title_refine import handle_title_refine


def get_handler(job: Job):
    if job.job_type == JobType.NOOP.value:
        return handle_noop
    if job.job_type == JobType.SYNC_THREAD.value:
        return handle_sync_thread
    if job.job_type == JobType.UPDATE_THREAD.value:
        return handle_update_thread
    if job.job_type == JobType.EXPORT_THREAD.value:
        return handle_export_thread
    if job.job_type == JobType.CLEANUP_JOB.value:
        return handle_cleanup_job
    if job.job_type == JobType.TITLE_REFINE.value:
        return handle_title_refine
    return None
