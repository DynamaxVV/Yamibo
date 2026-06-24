# Yamibo Agent Evaluation

Use this guide when validating whether an agent client can operate Yamibo end to end.

## Passing goals
- The agent should distinguish remote read-only tools from local archive reads and job-creation tools.
- The agent should avoid creating duplicate live jobs for the same thread and payload.
- The agent should poll `read_job` as the primary status surface and only read `read_job_events` for diagnostics.
- The agent should use `read_archived_thread(view="summary")` or paged `view="content"` before reading full materialized files.

## Required scenarios
1. Search or inspect a remote thread without causing local writes.
2. Create an archive job, poll it through `read_job`, then inspect `read_job_events`.
3. Read a missing local archive, recover through job creation, then read `summary` and paged `content`.
4. Observe `failed`, `partial`, and `interrupted` job states and follow the returned hints instead of blindly retrying.
5. Create an export or update job and verify that payload-compatible live jobs are reused, while different payloads create new jobs.

## Recommended scoring
- `discoverability`: can the agent find the workflow from guides and tool descriptions.
- `state_discipline`: does the agent keep remote, local, and async job states separate.
- `recovery`: does the agent react correctly to `JOB_NOT_FOUND`, `LOCAL_ARCHIVE_NOT_FOUND`, `REMOTE_LOGIN_REQUIRED`, `REMOTE_MAINTENANCE`, `partial`, and `interrupted`.
- `token_efficiency`: does the agent prefer compact views and cursor pagination over full-file reads.

## Evidence to capture
- tool call order
- final job status and event timeline
- whether duplicate jobs were created
- whether content pagination followed `next_cursor`
- whether the run completed without human correction
