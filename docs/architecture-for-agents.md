# Architecture For Agents

> Version: 0.7.0 | Updated: 2026-06-22

This document answers the question "where should an AI coding agent change code?" It is intentionally more operational than the product docs.

## Main Data Flows

### Agent reads remote forum state

```text
MCP client
  -> server.mcp_registry / server.agent_tools
  -> application.search_use_cases or application.remote_queries
  -> yamibo.client + yamibo.parsers
  -> compact AgentResult
```

Use this path for forum discovery, search, and remote thread preview. Remote queries may open SQLite only to annotate whether a thread is already archived; they must not create jobs, write thread rows, download assets, or materialize files.

### Agent creates long-running work

```text
MCP client or CLI
  -> server.agent_tools or server.cli
  -> application.archive_commands / application.update_commands
  -> db.repositories.jobs
  -> daemon.runner
  -> daemon.handlers/*
  -> yamibo.client, yamibo.parsers, storage, db.repositories
```

Archive, update, export, cleanup, and title-refine work is job-based. A command creates a queued SQLite job; the daemon owns execution and recovery.

### Agent reads local archive state

```text
MCP client
  -> server.agent_tools or server.resources
  -> application.archive_queries / application.job_queries
  -> db.repositories + storage paths
  -> compact AgentResult or MCP resource body
```

Local archive reads must not fetch remote pages. Use `read_archived_thread` for compact views and MCP resources for larger materialized content.

### Web console

```text
browser
  -> web.app
  -> web.api
  -> application queries/commands
  -> db.repositories / storage
```

The React source is in `frontend/`. The package-served static artifact is `src/yamibo_mcp/web/static/`.

## Directory Responsibilities

| Path | Responsibility | Agent edit guidance |
|------|----------------|---------------------|
| `src/yamibo_mcp/server/app.py` | Entrypoint only: exports `build_mcp_server` and CLI `main`. | Do not add tool logic here. |
| `src/yamibo_mcp/server/mcp_registry.py` | FastMCP tool/resource registration and descriptions. | Add or rename public Agent-facing tools/resources here after application seams exist. |
| `src/yamibo_mcp/server/agent_tools.py` | Thin MCP wrappers that return wire dicts through `agent_adapter`. | Keep wrappers shallow; delegate behavior to `application/*`. |
| `src/yamibo_mcp/server/agent_adapter.py` | `AgentResult` to wire payload conversion and exception mapping. | Add stable error mapping here, not in every tool. |
| `src/yamibo_mcp/server/resources.py` | MCP resource handlers and static guide/schema resources. | Resource output should be local or static guidance, not hidden business logic. |
| `src/yamibo_mcp/server/legacy_protocol.py` | Legacy JSON-RPC tool list and dispatcher. | Keep compatibility only; new primary interface belongs in `mcp_registry.py`. |
| `src/yamibo_mcp/server/legacy_tools.py` | Legacy tool-name compatibility wrappers. | Avoid expanding this module unless preserving an old CLI/tool behavior. |
| `src/yamibo_mcp/application/contracts.py` | Agent-facing result, error, and next-action contract. | Keep this interface small and stable. |
| `src/yamibo_mcp/application/archive_commands.py` | Job creation for archive/export and archive ensuring. | Job-creating behavior goes here, not in server wrappers. |
| `src/yamibo_mcp/application/archive_queries.py` | Local-only archive reads: summary/content/assets/diagnostics/export/metadata. | Do not import or call `YamiboClient` here. |
| `src/yamibo_mcp/application/remote_queries.py` | Remote forum browsing/search and remote/local annotation. | May read local SQLite for archive hints; must not persist remote data. |
| `src/yamibo_mcp/application/remote_inspection.py` | Remote thread preview. | Fetch and parse only; no SQLite writes or storage materialization. |
| `src/yamibo_mcp/application/update_queries.py` | Remote update check using local archive baseline. | May fetch remote pages for comparison; should not create jobs. |
| `src/yamibo_mcp/application/update_commands.py` | Incremental update job creation. | Side effect is queued job creation only. |
| `src/yamibo_mcp/application/job_queries.py` | Local job status and event reads. | Read SQLite queue/event state only. |
| `src/yamibo_mcp/application/forum_queries.py` | Forum profile reads. | Local metadata only. |
| `src/yamibo_mcp/application/legacy_use_cases.py` | Aggregated old behaviors such as `get_thread`. | Treat as transitional; prefer command/query modules for new code. |
| `src/yamibo_mcp/daemon/runner.py` | Job polling, acquisition, execution loop. | Change when job lifecycle behavior changes. |
| `src/yamibo_mcp/daemon/handlers/` | Concrete job implementations. | Long-running archive/update/export logic belongs here. |
| `src/yamibo_mcp/db/repositories/` | SQL access modules. | Keep SQL here; callers should not hand-roll repeated queries. |
| `src/yamibo_mcp/domain/` | Domain models, enums, validation, fingerprints. | Put pure domain rules here when they are reused. |
| `src/yamibo_mcp/yamibo/` | Forum HTTP, URLs, page classification, parsers, cleaners, title parsing. | Forum HTML knowledge belongs here. |
| `src/yamibo_mcp/storage/` | Filesystem layout, staging, archive materialization, images, exports. | File path and materialized artifact behavior belongs here. |
| `src/yamibo_mcp/services/` | LLM client and title refinement helpers. | LLM title logic is internal archive-flow support, not public Agent tooling. |
| `src/yamibo_mcp/web/` | Embedded HTTP API/server and packaged static UI. | Python web routes in `web/api.py`; static artifacts are generated. |
| `frontend/` | React/Vite source for the web console. | Edit UI source here, then build into `src/yamibo_mcp/web/static/`. |
| `src/yamibo_mcp/maintenance/` | Backup, cleanup, reset commands. | Operational scripts only. |

## Common Task Modification Paths

| Task | Primary files | Tests to start with |
|------|---------------|---------------------|
| Add a new Agent-facing MCP tool | `application/*`, `server/agent_tools.py`, `server/mcp_registry.py`, `server/legacy_protocol.py` if legacy JSON-RPC needs it, docs | `uv run pytest tests/unit/test_server/test_agent_interface.py tests/unit/test_application/` |
| Change Agent error shape or exception mapping | `application/contracts.py`, `server/agent_adapter.py` | `uv run pytest tests/unit/test_application/test_contracts.py tests/unit/test_server/test_agent_interface.py tests/unit/test_server/test_protocol_legacy.py` |
| Add a local archive read view | `application/archive_queries.py`, repositories if needed, `server/resources.py` if a resource is also needed | `uv run pytest tests/unit/test_application/test_archive_queries.py tests/unit/test_server/test_resources.py` |
| Change remote search/browse behavior | `application/search_use_cases.py`, `application/remote_queries.py`, `yamibo/client.py`, `yamibo/parsers/*` | `uv run pytest tests/unit/test_server/test_forum_id_tools.py tests/unit/test_parsers/` |
| Change remote thread preview | `application/remote_inspection.py`, `yamibo/parsers/thread_detail.py` | `uv run pytest tests/unit/test_server/test_agent_interface.py tests/unit/test_parsers/test_thread_detail.py` |
| Change archive job behavior | `application/archive_commands.py`, `daemon/handlers/sync_thread.py`, `storage/*`, repositories | `uv run pytest tests/unit/test_application/test_thread_use_cases.py tests/unit/test_daemon/` |
| Change update detection | `application/update_queries.py`, `application/update_commands.py`, `daemon/handlers/update_thread.py` | `uv run pytest tests/unit/test_application/test_thread_update_use_cases.py tests/unit/test_daemon/test_update_thread_handler.py` |
| Change job status/events | `application/job_queries.py`, `db/repositories/jobs.py`, `db/repositories/job_events.py` | `uv run pytest tests/unit/test_application/test_job_use_cases.py tests/unit/test_db/` |
| Change web API | `web/api.py`, relevant `application/*` module | `uv run pytest tests/unit/test_web/` |
| Change web UI | `frontend/src/*`, then `npm --prefix frontend run build` | `uv run pytest tests/unit/test_web/` plus manual UI smoke test if visual behavior changes |
| Change static asset serving | `web/app.py`, `src/yamibo_mcp/web/static/README.md` | `uv run pytest tests/unit/test_web/test_app.py` |
| Change schema/migrations | `db/migrations.py`, repositories, fixtures | `uv run pytest tests/unit/test_db/ tests/unit/test_application/` |

## Legacy Zones

These files are compatibility zones, not the place to add new primary behavior:

- `src/yamibo_mcp/server/tools.py`
- `src/yamibo_mcp/server/protocol.py`
- `src/yamibo_mcp/server/resource_handlers.py`
- `src/yamibo_mcp/server/legacy_tools.py`
- `src/yamibo_mcp/server/legacy_protocol.py`
- `src/yamibo_mcp/application/thread_use_cases.py`
- `src/yamibo_mcp/application/thread_update_use_cases.py`
- `src/yamibo_mcp/application/job_use_cases.py`
- `src/yamibo_mcp/application/legacy_use_cases.py`

Rules:

- Do not expose `llm_transform_text` or `parse_thread_title` as public Agent-facing tools.
- Do not add public Agent-facing `limit` parameters. Use forum pagination, floor ranges, or cursor/chunk patterns.
- Do not make remote preview functions write SQLite, download images, or materialize local archives.
- Do not make local archive query functions fetch remote pages.
- Do not hide business logic in MCP prompts or static resource text.

## Static Artifact Policy

`frontend/` is the editable React/Vite source. `src/yamibo_mcp/web/static/` is the generated artifact served by the embedded Python web server and included in the Python package tree.

When changing UI:

1. Edit files under `frontend/src/` or `frontend/public/`.
2. Run `npm --prefix frontend run build`.
3. Review the generated diff under `src/yamibo_mcp/web/static/`.
4. Run `uv run pytest tests/unit/test_web/`.

Agents should not inspect minified JS/CSS under `src/yamibo_mcp/web/static/assets/` unless the task is specifically about built artifact routing, packaging, or cache busting.

## Test Command Matrix

| Scope | Command |
|-------|---------|
| Agent interface contract | `uv run pytest tests/unit/test_server/test_agent_interface.py tests/unit/test_application/test_contracts.py` |
| MCP resources | `uv run pytest tests/unit/test_server/test_resources.py` |
| Application layer | `uv run pytest tests/unit/test_application/` |
| Server layer | `uv run pytest tests/unit/test_server/` |
| Web API/static routes | `uv run pytest tests/unit/test_web/` |
| Database/repositories | `uv run pytest tests/unit/test_db/` |
| Daemon handlers | `uv run pytest tests/unit/test_daemon/` |
| Parsers | `uv run pytest tests/unit/test_parsers/` |
| Frontend build | `npm --prefix frontend run build` |
| Full Python suite | `uv run pytest` |

No lint or typecheck is configured for the Python package. The frontend build runs TypeScript before Vite.
