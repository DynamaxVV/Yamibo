# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Setup
uv sync --extra dev

# Daemon (background job consumer + embedded web console on :8765)
uv run yamibo-daemon
uv run yamibo-daemon --once          # single-pass, no loop

# MCP server (stdio transport for LLM clients)
uv run yamibo-mcp-server stdio

# CLI — direct tool calls
uv run yamibo-mcp-server browse-forum-page --page 1
uv run yamibo-mcp-server search-threads --query "星灵感应"
uv run yamibo-mcp-server create-thread-archive-job --tid 572313
uv run yamibo-mcp-server job-status <job_id>
uv run yamibo-mcp-server search-archived-content --query "..." --mode hybrid --top-k 5

# Database
uv run yamibo-init-db
uv run yamibo-backup-db

# Frontend dev server
cd frontend && npm run dev

# Tests
uv run pytest                          # all
uv run pytest tests/unit/              # unit only
uv run pytest -k "test_name"           # by name
```

## Architecture

**Job-based async pattern**: `yamibo-mcp-server` (MCP tools) creates jobs → SQLite queue → `yamibo-daemon` polls + acquires leases + executes handlers. CLI tools call application layer directly, bypassing MCP serialization.

**Layers** (top-down, dependencies flow inward):
- `server/` — FastMCP tool registration, CLI subcommands, JSON schemas, resource URIs, agent adapter
- `application/` — Business logic split into `*_commands.py` (side-effecting: creates jobs) and `*_queries.py` (read-only: reads DB/remote)
- `daemon/` — `DaemonRunner` acquires jobs via lease, dispatches to handlers in `daemon/handlers/`
- `yamibo/` — Forum HTTP client, HTML parsers, title parsing engine
- `storage/` — File I/O: staging, exports, images, atomic writes
- `db/` — `DatabaseConnection` abstraction over SQLite/PostgreSQL; repositories per table in `db/repositories/`
- `domain/` — Frozen dataclasses (Job, ThreadSnapshot, ContentBlock, etc.), enums, validation
- `services/` — LLM client, title hints provider
- `maintenance/` — backup, cleanup, reset CLI commands

**Frontend**: React 18 + Vite + React Router. Build output → `src/yamibo_mcp/web/static/`, served by daemon at `http://127.0.0.1:8765`.

## Agent Tool Contract

All MCP tools are wrapped with `@agent_tool` (defined in `server/agent_adapter.py`). Every tool returns `AgentResult`:
```python
AgentResult(ok=True, data={...}, side_effects=["remote_fetch_only"])
# or
AgentResult(ok=False, error=AgentError(code="...", message="...", agent_hint="...", retryable=...))
```

Exceptions thrown inside tools are caught by the decorator and mapped to structured `AgentError` via `map_exception()`. Error codes: `JOB_NOT_FOUND`, `REMOTE_FETCH_FAILED`, `REMOTE_LOGIN_REQUIRED`, `REMOTE_MAINTENANCE`, `REMOTE_ACCESS_PAUSED`, `REMOTE_THREAD_PERMISSION_REQUIRED`, `UNEXPECTED_REMOTE_PAGE`, `EXPORT_PRECHECK_FAILED`, `LOCAL_ARCHIVE_NOT_FOUND`, `INVALID_ARGUMENT`, `INTERNAL_ERROR`.

Public tools are listed in `server/agent_tools.py` → `PUBLIC_AGENT_TOOLS` (name, description, handler). Adding a tool means: implement in `application/`, wrap in `agent_tools.py` with `@agent_tool`, register in `PUBLIC_AGENT_TOOLS`.

## Configuration

Primary: `yamibo.local.json` (not committed). Config sections: `yamibo`, `export`, `llm`, `rag`, `database`, `title`, `web`, `worker`, `maintenance`, `login`. All overridable via `YAMIBO_*` env vars (e.g. `YAMIBO_COOKIE_FILE`, `YAMIBO_LLM_MODEL`). See `config.py` → `load_settings()` for full schema.

`rag.api_key` / `rag.base_url` fall back to `llm.api_key` / `llm.base_url` if not set separately.

## Database

`DatabaseConnection` (`db/connection.py`) wraps SQLAlchemy `Connection` with its own `ResultProxy`/`RowProxy` for dict-style access. Supports SQLite (default, WAL mode, FK enforcement) and PostgreSQL (with `pgvector`). Use `connect()` to get a connection; supports `with`-statement context manager (auto-commits on success, rollback on exception).

RAG search: `sqlite-vec` extension for SQLite, `pgvector` for PostgreSQL. Hybrid search combines FTS5/tsvector + vector similarity.

## Error Handling

Error hierarchy in `errors.py`:
- `YamiboError` (base)
  - `JobNotFound`
  - `LeaseNotAcquired`
  - `RemoteFetchError` (base for all HTTP/forum errors)
    - `LoginRequiredError`
    - `RemoteMaintenanceError`
    - `RemoteAccessPausedError`
    - `UnexpectedPageError`
      - `ThreadPermissionRequiredError`

## Test Fixtures

`tests/fixtures/` contains real forum data snapshots (2026-06-19): `forum_pages/`, `threads/`, `title_parses/`, `edge_cases/`. Tests use these JSON fixtures — no live network calls in test suite.

## Web Console

Frontend SPA with i18n (zh/en), 4 themes + dark mode. Pages: Dashboard, Jobs, JobDetail, Threads, ThreadDetail, Series, Forums, RAG, Exports, Review, Logs, Settings. API client in `frontend/src/api/client.ts`.
