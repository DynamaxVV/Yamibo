# Yamibo Agent Error Codes

- `INVALID_ARGUMENT`: Tool arguments are invalid. Fix the request before retrying.
- `LOCAL_ARCHIVE_NOT_FOUND`: The thread is not archived locally. Use `create_thread_archive_job` or `ensure_thread_archived`.
- `JOB_NOT_FOUND`: The job id is unknown. Check the id or create a new job.
- `REMOTE_LOGIN_REQUIRED`: Remote access needs a valid cookie/login.
- `REMOTE_MAINTENANCE`: The forum appears to be in maintenance mode. Retry later.
- `UNEXPECTED_REMOTE_PAGE`: The remote page is not the expected forum/thread page.
- `REMOTE_FETCH_FAILED`: Remote fetch failed for network or HTTP reasons.
- `EXPORT_PRECHECK_FAILED`: Export cannot start until archive preconditions are fixed.
- `INTERNAL_ERROR`: Unexpected server error. Prefer a narrower retry or inspect job events.
