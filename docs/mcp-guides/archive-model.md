# Yamibo Archive Model

The MCP interface separates remote reads, local archives, and background jobs.

## State model
- `browse_forum_page`, `search_forum_threads`, `inspect_remote_thread`, and `check_thread_updates` are remote read-only tools.
- `probe_archived_threads` is a local read-only probe for archive presence and last local floor timestamp; it does not fetch remote data.
- `create_thread_archive_job`, `create_thread_update_job`, and `create_thread_export_job` create SQLite jobs.
- The daemon consumes queued jobs and materializes local files/resources.

## Local content model
- `read_archived_thread(view="summary")` returns compact metadata and resource URIs.
- `read_archived_thread(view="content")` returns a bounded chunk of floors and content blocks.
- Follow `next_cursor` while `has_more` is true.
- Full materialized text is exposed through `yamibo://threads/{tid}/context`.
- Structured posts are exposed through `yamibo://threads/{tid}/posts`.

## Job model
- Job status is read through `read_job`.
- Blocking waits should prefer `wait_for_job`.
- Job event history is read through `read_job_events` or `yamibo://jobs/{job_id}/events`.
- Status resources are exposed through `yamibo://jobs/{job_id}/status`.
