from __future__ import annotations

from enum import StrEnum


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"
    INTERRUPTED = "interrupted"
    RETRYING = "retrying"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED = "cancelled"


class JobType(StrEnum):
    NOOP = "noop"
    SYNC_THREAD = "sync_thread"
    UPDATE_THREAD = "update_thread"
    EXPORT_THREAD = "export_thread"
    CLEANUP_JOB = "cleanup_job"
    TITLE_REFINE = "title_refine"
