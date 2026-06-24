# Yamibo Agent Workflows

Start here when you do not know which tool to call.

## Remote discovery
- Use `read_forum_profiles` to understand available forums.
- Use `browse_forum_page` for page-by-page browsing.
- Use `search_forum_threads` for remote-first search.
- Use `inspect_remote_thread` before archiving when you need a small remote preview.

## Local archive workflow
- Use `ensure_thread_archived` when you need a local copy and can tolerate queued work.
- Use `create_thread_archive_job` for explicit job creation.
- Use `wait_for_job` instead of client-side `sleep` when you need to block on job completion.
- Use `probe_archived_threads` before large batch archives to check whether a tid already has local archive state and what the last local floor timestamp is.
- Poll `read_job`, then `read_job_events`, only when you need finer-grained status.
- Read local content with `read_archived_thread`.
- For large content, call `read_archived_thread` with `view="content"` and follow `next_cursor`.

## Update/export workflow
- Use `check_thread_updates` for read-only update inspection.
- Use `create_thread_update_job` only after update inspection or when the user requests it.
- Use `create_thread_export_job` after a local archive exists.

Useful resources:
- `yamibo://schema/tools`
- `yamibo://guide/error-codes`
- `yamibo://guide/archive-model`
