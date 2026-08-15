from __future__ import annotations

from enum import StrEnum


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"
    SUPERSEDED = "superseded"
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
    RAG_INDEX = "rag_index"
    DISCUSSION_TREND_INDEX = "discussion_trend_index"
    DISCUSSION_TREND_REPORT = "discussion_trend_report"
    IMAGE_BACKFILL = "image_backfill"
    DAILY_SIGN_IN = "daily_sign_in"
