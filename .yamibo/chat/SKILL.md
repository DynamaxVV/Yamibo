# Yamibo Project Skill

Use this project skill implicitly inside the WebUI chat mode.

## Operating Rules

- Search/browse remote forum data with remote-first read tools.
- Probe local archive state before creating bulk archive work when applicable.
- For long-running work, create jobs and use `read_job` or `wait_for_job` for status; inspect events only when diagnosing failures.
- For local knowledge answers, prefer archived search, trend queries, and resources.
- Keep action count small and avoid duplicate reads.
- Treat `resources` and `next_actions` in a tool result as the preferred continuation contract.
- Use `yamibo://guide/agent-workflows`, `yamibo://guide/archive-model`, and `yamibo://guide/error-codes` before guessing unfamiliar tool behavior.
- Do not claim WebUI Chat has a built-in tool loop, approvals, citations, or Agent run history; those are future runtime plans, not current capabilities.

## Preferred Sequences

1. Discover target tids with browse/search if the user has not given ids.
2. Inspect local archive state before enqueueing archive/update/index jobs.
3. After a write action, read the returned job id rather than guessing completion.
4. After success or partial success, read the smallest local resource needed to answer the user.
