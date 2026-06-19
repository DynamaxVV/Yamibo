# Yamibo MCP

Local archiving system for the yamibo.com (百合会) forum. Python package with MCP server, daemon, web console, and CLI.

## Architecture

- `src/yamibo_mcp/server/` — MCP server (FastMCP stdio) + legacy JSON-RPC + CLI subcommands
- `src/yamibo_mcp/daemon/` — background job consumer (polls SQLite, executes handlers) + embedded web console
- `src/yamibo_mcp/web/` — embedded web UI for job monitoring (started by daemon)
- `src/yamibo_mcp/yamibo/` — forum HTTP client, HTML parsers, title parsing, series matching
- `src/yamibo_mcp/storage/` — local file I/O (staging, exports, images, markdown)
- `src/yamibo_mcp/db/` — SQLite schema, migrations, repositories (jobs, threads, series, audit)
- `src/yamibo_mcp/services/` — LLM client, title hints
- `src/yamibo_mcp/domain/` — models, enums, validation
- `src/yamibo_mcp/maintenance/` — backup, cleanup, reset commands
- `tests/unit/` — unit tests (pytest)

Job-based architecture: `yamibo-mcp-server` creates jobs in SQLite, `yamibo-daemon` consumes them.

## Commands

```bash
# Setup
uv sync --extra dev

# Run daemon (background job consumer + web console)
uv run yamibo-daemon
uv run yamibo-daemon --once        # single-pass for testing

# Run MCP server (stdio transport for LLM clients)
uv run yamibo-mcp-server stdio

# CLI shortcuts (direct tool calls, returns JSON)
uv run yamibo-mcp-server browse-forum-page --page 1
uv run yamibo-mcp-server search-threads --query "星灵感应"
uv run yamibo-mcp-server get-thread --tid 572313
uv run yamibo-mcp-server job-status <job_id>

# Database
uv run yamibo-init-db
```

## Tests

```bash
uv run pytest                      # all tests
uv run pytest tests/unit/          # unit tests only
uv run pytest tests/unit/test_parsers/test_title_parser.py  # single file
uv run pytest -k "test_name"       # by name
```

No lint/typecheck configured — only pytest.

**测试数据**：`tests/fixtures/` 目录包含真实论坛数据（2026-06-19 抓取）
- `forum_pages/page_*.json`：论坛列表页数据（第 1/2/3/5/10/20 页）
- `threads/thread_*.json`：帖子详情数据（不同特征的帖子）
- `title_parses/*.json`：标题解析数据（简单/带汉化组/章节名/特殊前缀）
- `edge_cases/`：边界条件数据（空楼层、缺失字段、畸形数据、单层楼）

**测试原则**：高内聚低耦合、独立性与可重复性、数据与逻辑分离

## Config

Primary config: `yamibo.local.json` (not committed with real credentials).

All settings also overridable via `YAMIBO_*` env vars. Key ones:
- `YAMIBO_CONFIG_PATH`, `YAMIBO_DATA_DIR`, `YAMIBO_DB_PATH`
- `YAMIBO_COOKIE_FILE` — forum auth cookie
- `YAMIBO_EXPORT_DIR` — where exported ZIPs go
- `YAMIBO_LLM_BASE_URL`, `YAMIBO_LLM_API_KEY`, `YAMIBO_LLM_MODEL`

Config resolution: env vars → `yamibo.local.json` → hardcoded defaults (`config.py:67`).

## Gotchas

- The daemon must be running for `archive-thread` / `export-thread` jobs to execute. Creating a job just writes to SQLite.
- `get-thread` auto-archives if local copy missing; `archive-thread` only queues.
- `search-threads` tries remote first, falls back to local — check `source` and `remote_error` in response.
- Forum requires valid `.cookie` file for remote access.
- SQLite DB lives at `data/forum.db` by default. Schema managed by `db/migrations.py`.
- HTML test fixtures in `html_sample/` are real forum pages used by parser tests.
- **论坛维护时间**：每天 5:30-6:30（UTC+8）论坛可能维护，此时只能获取维护界面。
- **搜索限流**：论坛搜索接口限制 10 秒/次，测试中需添加延时。
