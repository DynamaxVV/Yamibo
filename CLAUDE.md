# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Setup
uv sync --extra dev

# Daemon (background job consumer + embedded web console on :8765)
uv run yamibo-daemon
uv run yamibo-daemon --once          # single-pass, no loop
uv run yamibo-daemon --no-web        # daemon only, skip web console

# Standalone web console (without daemon)
uv run yamibo-web

# MCP server (stdio transport for LLM clients)
uv run yamibo-archiver stdio

# CLI — direct tool calls (bypass MCP serialization, print JSON)
uv run yamibo-archiver browse-forum-page --page 1 --forum-id 55 --order dateline
uv run yamibo-archiver search-threads --query "星灵感应"
uv run yamibo-archiver create-thread-archive-job --tid 572313
uv run yamibo-archiver create-sync-thread-batch-jobs --tid 572313 --tid 572314
uv run yamibo-archiver job-status <job_id>
uv run yamibo-archiver search-archived-content --query "..." --mode hybrid --top-k 5
uv run yamibo-archiver discussion-partition-trends --forum-id 5 --start-date 2014-11-01 --end-date 2014-11-30

# Database (DANGER: Destructive Operations
uv run yamibo-init-db
uv run yamibo-backup-db
uv run yamibo-maintenance-cleanup
uv run yamibo-reset-data

# Alembic migrations (PostgreSQL)
uv run alembic upgrade head
uv run alembic downgrade -1
uv run alembic revision --autogenerate -m "description"

# Frontend
cd frontend && npm run dev           # Vite HMR dev server
cd frontend && npm run build         # tsc + vite → src/yamibo_mcp/web/static/

# Tests
uv run pytest                                  # all
uv run pytest tests/unit/                      # unit only
uv run pytest tests/integration/               # integration only
uv run pytest tests/unit/test_parsers/test_title_parser.py  # single file
uv run pytest -k "test_name"                   # by name

# PostgreSQL (local dev via docker)
docker compose up -d                  # pgvector/pgvector:pg15, db=yamibo, user/pass=yamibo
YAMIBO_TEST_PG_URL=postgresql://yamibo:yamibo@localhost:5432/yamibo uv run pytest tests/integration/
```

## Architecture

**Job-based async pattern**: `yamibo-archiver` (MCP tools / CLI) creates jobs → queue (SQLite or PostgreSQL) → `yamibo-daemon` polls + acquires leases + dispatches to handlers. CLI tools call the application layer directly, bypassing MCP serialization.

**Layers** (top-down, dependencies flow inward):

| Layer | Role | Key files |
|---|---|---|
| `server/` | FastMCP tools, CLI subcommands, resource URIs, agent adapter | `agent_tools.py` (tool registry), `agent_adapter.py` (`@agent_tool` decorator), `cli.py` (argparse CLI) |
| `application/` | Business logic — `*_commands.py` (write: creates jobs, mutates state) and `*_queries.py` (read: reads DB/remote). `contracts.py` defines `AgentResult`/`AgentError`/`AgentAction` | `archive_commands.py`, `archive_queries.py`, `discussion_trend_commands.py`, `discussion_trend_queries.py`, `rag_commands.py`, `rag_queries.py` |
| `daemon/` | Job consumer loop + per-type handlers | `main.py` (DaemonRunner + EmbeddedWebServer), `handlers/` (one module per job type) |
| `yamibo/` | Forum HTTP client, HTML parsers, title engine, proxy/account pools, anti-bot | `client.py`, `parsers/`, `title/`, `proxy_pool.py`, `account_pool.py` |
| `storage/` | File I/O: staging, exports, images, markdown, atomic writes | `atomic.py`, `images.py`, `exports.py` |
| `db/` | DB abstraction (SQLite + PostgreSQL), Alembic migrations, repositories per table | `connection.py` (DatabaseConnection), `repositories/` (jobs, threads, series, assets, rag_chunks, discussion_trends, etc.) |
| `domain/` | Frozen dataclasses, enums, validation — pure data, no I/O | `models.py`, `enums.py` |
| `rag/` | Chunking, embedding, hybrid search (FTS5/pgvector + vector similarity) | |
| `services/` | LLM client, title hints | |
| `maintenance/` | backup, cleanup, reset CLI | |
| `benchmark/` | Hermes performance benchmarking | |

**Shared package-root modules**: `config.py` (settings loading), `errors.py` (exception hierarchy), `time_utils.py`, `logging.py`.

**Web layer**: `web/api.py` is the single dispatch entry point — it owns the DB lifecycle (`connect()` → `migrate()` → route → `conn.close()`). Per-resource route handlers in `web/routes/` (`jobs.py`, `threads.py`, `series.py`, `forums.py`, `rag.py`, `review.py`, `settings.py`, `dashboard.py`, `debug.py`, `remote_forum.py`, `daemon.py`). Shared helpers in `web/routes/_helpers.py` and `web/routes/_converters.py`. The embedded web server starts inside the daemon process on `:8765` (configurable via `YAMIBO_WEB_HOST`/`YAMIBO_WEB_PORT`).

**Frontend**: React 18 + Vite + React Router v6. Layout route pattern — `<Layout>` wraps all pages. Context providers (`I18nProvider` zh/en, `ThemeProvider` 4 themes + dark mode) wrap `<BrowserRouter>`. Pages in `pages/`, shared components in `components/` (ThreadReader, LazyImage, PaginationControls, DataTable, Badge, ThemePicker). CSS: `styles.css` (global) + `styles/` (per-page). API client in `api/client.ts` — `fetchJson<T>(path)` for GET, `postJson<T>(path, body)` for POST, both prepend `/api` base. Build output → `src/yamibo_mcp/web/static/`.

## Adding Features

### Adding a new job type (daemon handler)

1. Add the `JobType` enum value in `domain/enums.py`.
2. Create `daemon/handlers/<name>.py` with a `handle_<name>(job: Job, conn, settings: Settings) -> None` function.
3. In `daemon/handlers/__init__.py`: import the handler, add an `if job.job_type == JobType.<NAME>.value: return handle_<name>` branch in `get_handler()`.
4. If the job needs a creation path, add a command in `application/<name>_commands.py` that inserts into the job queue via `JobsRepository`.

Handler dispatch is a simple `if/elif` chain — no decorator registry. Returning `None` from `get_handler()` means "no handler for this type" and the daemon will mark the job as failed.

### Adding a new MCP tool

1. Implement the logic in `application/` — a command function (returns `dict[str, Any]`) or query function (returns `AgentResult`).
2. In `server/agent_tools.py`: wrap with `@agent_tool`, use **keyword-only arguments** with type hints, delegate to the application function.
3. Register in `PUBLIC_AGENT_TOOLS` as a `(name, description, function_ref)` tuple. Description must mention side effects (e.g. "writes a queued job to SQLite; daemon execution is required").

Pattern:
```python
@agent_tool
def tool_name(*, param_a: int, param_b: str = "default") -> AgentResult:
    return _underlying_app_function(param_a=param_a, param_b=param_b)
```

The `@agent_tool` decorator (defined in `agent_adapter.py`) catches all exceptions and maps them to structured `AgentError` via `map_exception()`. Error codes: `JOB_NOT_FOUND`, `REMOTE_FETCH_FAILED`, `REMOTE_LOGIN_REQUIRED`, `REMOTE_MAINTENANCE`, `REMOTE_ACCESS_PAUSED`, `REMOTE_THREAD_PERMISSION_REQUIRED`, `UNEXPECTED_REMOTE_PAGE`, `EXPORT_PRECHECK_FAILED`, `LOCAL_ARCHIVE_NOT_FOUND`, `INVALID_ARGUMENT`, `INTERNAL_ERROR`.

### Adding a new web API route

1. Create `web/routes/<name>.py` with handler functions signature `handle_<endpoint>(handler, conn, params, settings)` — `handler` is the `BaseHTTPRequestHandler`, `conn` is a pre-connected `DatabaseConnection`.
2. In `web/api.py`: add the route dispatch in `_route()` (another `if/elif` chain). Check HTTP method via `handler.command == "GET"` etc.
3. For dynamic path segments, extract via string slicing (e.g. `route[6:-7]` for `/jobs/<id>/events`).

### Adding a new frontend page

1. Create `frontend/src/pages/<Name>.tsx`.
2. In `frontend/src/App.tsx`: add a `<Route>` inside the `<Layout>` layout route.
3. If needed, add API functions to `frontend/src/api/client.ts` using `fetchJson<T>()` or `postJson<T>()`.
4. For i18n strings, import from `I18nContext.tsx`.

## Development Patterns

### Application layer: commands vs queries

- **Commands** (`*_commands.py`): side-effecting — creates jobs, mutates DB state. Keyword-only args. Returns raw `dict[str, Any]`. Raises `ValueError` for invalid input (mapped to `INVALID_ARGUMENT` by the agent adapter).
- **Queries** (`*_queries.py`): read-only — reads DB or remote. Keyword-only args. Returns `AgentResult(ok=True, data=..., side_effects=[...])` directly. Validates allowed values via module-level constants (e.g. `ARCHIVED_THREAD_VIEWS = {"summary", "content", ...}`).

Both open/close their own DB connections with `try/finally`.

### Repository pattern

Repositories in `db/repositories/` are plain classes wrapping a `DatabaseConnection` — no ORM, raw SQL. Conventions:
- `_UPPER_SNAKE_CASE` module-level constants for retry delays, status tuples, etc.
- `_loads(value)` helper to deserialize JSON from DB columns (handles `None`, `str`, pre-parsed `dict`/`list`).
- Lock errors detected for both SQLite (`"database is locked"`) and Postgres (`SQLSTATE 55P03, 40001, 40P01`).
- Retry with backoff: `0.1, 0.2, 0.5, 1.0, 2.0` seconds.

### Daemon lifecycle

1. `configure_logging()` → `load_settings()` → `DaemonRunner(settings, worker_id)`.
2. Optionally starts `EmbeddedWebServer` (unless `--no-web`).
3. `run_forever()` loop: poll for queued jobs → acquire lease → `get_handler(job)` → execute → update status/artifacts. `run_once()` processes at most one job then returns.
4. `KeyboardInterrupt` → graceful shutdown → `embedded_web.stop()`.

### Web API dispatch

`web/api.py:handle_api(handler, path, query, settings)` is the single entry:
1. Strips `/api` prefix, parses query string.
2. Opens DB connection, runs `migrate(conn)` (ensures schema current on every request).
3. Dispatches to per-route handler via `_route()` (if/elif chain on route string).
4. `finally: conn.close()`. `BrokenPipeError`/`ConnectionResetError` swallowed silently.

### Config system

`config.py` — single entry point `load_settings() -> Settings` (frozen dataclass). Resolution order:
1. Environment variables (`YAMIBO_*`)
2. `yamibo.local.json` (or `YAMIBO_CONFIG_PATH` override)
3. Hardcoded defaults

Boolean env vars: `str(val).lower() in {"1", "true", "yes", "on"}`. All paths are `Path` objects. `AccountConfig` is a separate frozen dataclass for per-account credentials.

Key env vars: `YAMIBO_DATA_DIR`, `YAMIBO_DB_PATH`, `YAMIBO_DB_BACKEND` (sqlite|postgres), `YAMIBO_DB_URL`, `YAMIBO_WEB_HOST`, `YAMIBO_WEB_PORT`, `YAMIBO_WORKER_POLL_SECONDS`, `YAMIBO_WORKER_LEASE_SECONDS`, `YAMIBO_COOKIE_FILE`, `YAMIBO_LLM_BASE_URL`, `YAMIBO_LLM_API_KEY`, `YAMIBO_LLM_MODEL`.

## Database & Migrations

`DatabaseConnection` (`db/connection.py`) wraps SQLAlchemy `Connection` with custom `ResultProxy`/`RowProxy` for dict-style access. Supports PostgreSQL (default, with `pgvector` extension) and SQLite (WAL mode, FK enforcement). Context-manager: auto-commit on success, rollback on exception.

Alembic migrations are **hand-written** (`target_metadata = None` in `alembic/env.py`). Schema name passed at runtime via `config.attributes["schema"]` (defaults to `"public"`). Migration files in `alembic/versions/`.

RAG search: `sqlite-vec` for SQLite, `pgvector` for PostgreSQL. Hybrid search combines FTS5/tsvector + vector similarity.

Discussion Trend V1 features (trend mart, topic evidence, forum evidence pack, discussion reports) are **PostgreSQL-only** — SQLite not supported.

Local dev PostgreSQL: `docker compose up -d` starts `pgvector/pgvector:pg15` with db `yamibo`, credentials `yamibo/yamibo`, port 5432.

## Testing

**Fixtures** in `tests/fixtures/` contain real forum data snapshots (2026-06-19) — no live network in tests. Load via `tests/fixtures/loader.py` (`load_forum_page()`, `load_thread()`, `load_title_parse()`, `load_edge_case()`).

**`tests/conftest.py`** provides:
- `db` — function-scoped temporary SQLite database (sets `YAMIBO_DB_BACKEND=sqlite`, clears `YAMIBO_DB_URL`)
- `pg_engine` — session-scoped PostgreSQL, either via `YAMIBO_TEST_PG_URL` env var or testcontainers `pgvector/pgvector:pg15`
- `backend` — parametrized fixture yielding `"sqlite"` (always) and `"postgres"` (when available)

**Test structure**: `tests/unit/` mirrors the source layout — `test_application/`, `test_daemon/`, `test_db/`, `test_domain/`, `test_parsers/`, `test_rag/`, `test_server/`, `test_services/`, `test_storage/`, `test_web/`, `test_yamibo/`. `tests/integration/` covers agent workflows, PostgreSQL regression, SQLite→PG ETL, shadow validation, and transport/notification tests.

## Gotchas

- The daemon must be running for archive/export/RAG jobs to execute. Creating a job only writes to the queue.
- Discussion Trend V1 is PostgreSQL-only. Integration tests against SQLite must set `YAMIBO_DB_BACKEND=sqlite` and clear `YAMIBO_DB_URL`, otherwise inherited PG config leaks into subprocesses.
- Forum maintenance window: daily 5:30–6:30 AM (UTC+8). Forum search rate limit: 1 request per 10 seconds.
- Forum requires valid `.cookie` file for remote access.
- `*.egg-info/` and `.mimocode/` are local artifacts — don't commit.
- Python side has no lint/typecheck configured. Frontend runs `tsc` as part of `npm run build`.
- After modifying the frontend TSX/TS source code, you must run `cd frontend && npm run build` to recompile; otherwise, the web client will still display the old static assets. `npm run dev` is only used for local development and debugging (HMR). In production, static files rely on the build output to `src/yamibo_mcp/web/static/`.
- Agent tool guideline: prefer `probe_archived_threads` before batch archiving; `read_job` is the primary status surface (not `read_job_events`); creating a job ≠ job completion — always verify with `read_job` or `wait_for_job`.
- **CRITICAL / DATABASE DANGER**:
  - `yamibo-reset-data` will **wipe all production data** instantly.
  - `yamibo-init-db` will overwrite existing tables if forced.
  - Never run destructive DB commands (`reset-data`, `init-db`, `maintenance-cleanup`) without a recent backup via `yamibo-backup-db`.

## Commit Conventions (github)

遵循 Conventional Commits: `feat` (新功能), `fix` (bug修复), `docs` (文档更新), `refactor` (重构).

Example: `feat: v0.11.1 PostgreSQL 迁移支撑 + 账号池反爬保护`
