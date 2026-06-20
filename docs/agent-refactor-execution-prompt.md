# Agent-Oriented Incremental Refactor Execution Prompt

You are a cautious senior Python refactoring engineer. Work in `/Users/vv/Code/Yamibo`.

Your mission is to refactor Yamibo MCP incrementally from a comic-forum-specific archive tool into an agent-friendly, multi-forum, multi-content-shape forum archiving system.

Global requirements:

- Execute one phase at a time.
- Before changing code in a phase, read every file listed under `Context Anchors`.
- Preserve the existing `server + daemon + SQLite jobs` architecture.
- Preserve existing `forum-30` comic forum behavior unless a phase explicitly changes an internal implementation detail behind the same public behavior.
- Treat `get_thread` as an intent-level API: callers ask for a thread detail, while cache-hit, cache-miss, remote fetch, and local archival details stay inside the application layer.
- Do not use destructive git commands such as `git reset --hard` or `git checkout --` unless the user explicitly approves them.
- Prefer small, reviewable patches. Run the listed tests after each phase.

## Overall Background And Refactor Direction

Current system shape:

- `server/tools.py` currently mixes protocol adapter logic, DB access, remote fetch, inline sync, response shaping, resource resolution, and some use-case orchestration.
- The system assumes `forum-30` comic forum in several paths. URL helpers are partly generic, but client, search, jobs, docs, and test fixtures still encode comic-forum assumptions.
- The domain model is centered on `ThreadSnapshot`, `FloorSnapshot`, and image-centric archival. This fits comic posts but does not fit novel posts, discussion posts, or mixed short-text posts as naturally.
- Jobs are durable in SQLite, but completed jobs do not produce a durable event stream or outbox that future server/web/MCP notification features can consume.

Target system shape:

- MCP/CLI/Web entrypoints call a thin `server` adapter.
- The adapter calls `application` use cases.
- Use cases call repositories, storage services, forum client, parser, and daemon job helpers.
- Forum identity and content profile are explicit concepts.
- Posts can be represented as ordered blocks plus assets, while old floors/image fields remain available for compatibility during migration.
- Job state updates append durable events, so future notification paths can be built without making daemon depend on server process availability.
- Agent-facing resources provide compact summaries and diagnostics before requiring agents to read large `context.md` or `metadata.json` resources.

Refactor route:

`server adapter -> application use case -> repository/service/client -> domain contract -> resource/response`

Do not jump straight to the final topology. Build it in phases.

## Global Testing Requirements

Every phase must add or update tests for the behavior it introduces. Passing the existing suite is necessary but not sufficient.

Test discipline:

- Keep tests offline and deterministic. Do not call real `yamibo.com` in unit or integration-style tests.
- Prefer `tests/fixtures/` and fake clients over live HTTP. If new fixtures are needed, store them under explicit directories such as `tests/fixtures/forum_pages/forum_55/` or `tests/fixtures/content_shapes/`.
- Follow the repository's 3A testing style from `docs/testing-strategy.md`: Arrange, Act, Assert separated clearly.
- Test stable public contract shape instead of timestamps, generated ids, absolute paths, worker ids, or other volatile values.
- For public tool and resource behavior, include at least one test through the public boundary, not only repository internals.
- For DB changes, test empty DB migration, old DB migration, and repeated idempotent migration.
- For new fallback behavior, test both success and failure paths: remote success, remote failure, local fallback, missing resources, and invalid input where relevant.
- For every phase that adds a parameter with a compatibility default, test both omitted parameter behavior and explicit default behavior.

Suggested test organization:

```text
tests/unit/test_application/       # Use-case tests with fake clients/repositories where useful
tests/unit/test_server/            # Tool/resource contract tests
tests/unit/test_db/                # Migration and repository tests
tests/unit/test_yamibo/            # URL/client/parser unit tests
tests/fixtures/content_shapes/     # Offline novel/discussion/mixed content fixtures
```

Do not introduce live network requirements. If a behavior currently requires remote HTML, add a fixture or inject a fake fetcher/client.

---

# Phase 0: Baseline Audit And Contract Snapshot

## Reason, Background, And Approach

Reason:

The current code works, but its public behavior is mostly protected by convention and scattered tests. Before moving logic into an application layer, capture the current behavior so later phases can refactor internals without accidentally changing MCP/CLI contracts.

Approach:

Add characterization tests or documented contract snapshots first. Avoid production-code changes in this phase. The goal is to make current behavior observable, especially for `get_thread`, `search_threads`, `browse_forum_page`, resource URI parsing, and job status payloads.

## Context Anchors

Must read:

- `README.md`
- `AGENTS.md`
- `docs/api-reference.md`
- `docs/database-design.md`
- `src/yamibo_mcp/server/app.py`
- `src/yamibo_mcp/server/tools.py`
- `src/yamibo_mcp/server/schemas.py`
- `src/yamibo_mcp/server/resources.py`
- `src/yamibo_mcp/domain/models.py`
- `src/yamibo_mcp/db/migrations.py`
- `src/yamibo_mcp/yamibo/urls.py`

Immutable baseline:

```python
@dataclass(frozen=True)
class ThreadSnapshot:
    tid: int
    url: str | None
    page_type: str
    raw_title: str
    display_title: str
    title: TitleSnapshot
    publisher: str | None
    publisher_uid: str | None
    pub_time: str | None
    permission: int
    floors: list[FloorSnapshot]
    image_count: int = 0
```

Existing MCP tool names are immutable in this phase:

```text
search_threads
get_thread
browse_forum_page
archive_thread
export_thread
get_job_status
cleanup_job
sync_forum_range
parse_thread_title
llm_transform_text
```

## Current vs Target Topology

Bad Way:

`server/tools.py` directly handles protocol-facing functions, migration, DB connection, repository calls, remote client calls, inline sync, and response shaping.

Good Way:

No topology change yet. This phase records baseline behavior so later phases can safely move toward:

`server/tools.py -> application/use_cases -> repositories/storage/yamibo client -> schemas/resources`

Why:

The refactor needs a safety net before logic starts moving. Characterization tests are the fence around the current contract.

## Skeleton And Signatures

This phase should not add production abstractions. If tests need helpers, keep them local to the test file.

Suggested test helper shape:

```python
def assert_thread_summary_shape(payload: dict[str, object]) -> None:
    required = {
        "tid",
        "url",
        "display_title",
        "raw_title",
        "archive_status",
        "validation_status",
        "resources",
    }
    assert required.issubset(payload.keys())
```

## Data And State Lifecycle

Current lifecycle:

`MCP/CLI -> server.tools -> db/client/handler -> schemas/resources -> response`

Target lifecycle in later phases:

`MCP/CLI -> server adapter -> application use case -> repositories/services -> contract response`

## Blast Radius

Potentially affected areas:

- MCP tool output shape.
- CLI command output shape.
- Tests that import `server.tools` directly.
- Legacy JSON-RPC protocol in `server/protocol.py`.
- Web console assumptions about repository rows.

Isolation plan:

- Add tests around public behavior only.
- Do not inspect or assert volatile values such as absolute paths, timestamps, generated job ids, or worker ids unless essential.
- Avoid production code edits.

## Negative Constraints

- Do not modify database schema.
- Do not rename tools.
- Do not change `get_thread`, `search_threads`, or `browse_forum_page` return fields.
- Do not delete or move `tests/unit/_backup_20260619`.
- Do not introduce new dependencies.

## Defense And Fallback

Validation:

```bash
uv run pytest tests/unit/
```

Required test cases:

- `server.tools` contract snapshot for `thread_summary_payload`, `thread_detail_payload`, and `job_status_payload` using local DB/fixture data.
- Resource URI parsing snapshot for all existing `yamibo://threads/*` and `yamibo://series/*` URI forms.
- CLI-compatible output shape test for at least one short command that does not require live network.
- Negative test proving characterization tests do not assert volatile fields such as `job_id`, timestamps, absolute paths, or worker ids.

If tests fail:

- First check whether new characterization tests over-constrained time, generated ids, absolute paths, or remote-dependent behavior.
- Prefer weakening the new test to assert stable shape rather than incidental values.
- To undo this phase, remove only the newly added test/documentation files using a manual patch.

---

# Phase 1: Application Layer And Agent Contract Envelope

## Reason, Background, And Approach

Reason:

Agent-facing tools should express user intent, not internal implementation details. Today, `server/tools.py` contains too much orchestration, making it hard to keep behavior stable while adding cache hiding, multi-forum support, content profiles, and diagnostics.

Approach:

Create an `application` package and move use-case orchestration behind stable functions. Keep `server/tools.py` as a compatibility adapter. Start with thread and job use cases, while reusing existing schema builders so public response shape stays stable.

## Context Anchors

Must read:

- `src/yamibo_mcp/server/tools.py`
- `src/yamibo_mcp/server/schemas.py`
- `src/yamibo_mcp/server/resources.py`
- `src/yamibo_mcp/server/protocol.py`
- `src/yamibo_mcp/db/repositories/jobs.py`
- `src/yamibo_mcp/db/repositories/threads.py`
- `src/yamibo_mcp/daemon/handlers/sync_thread.py`

Immutable baseline:

Existing MCP tool names remain unchanged:

```text
search_threads
get_thread
browse_forum_page
archive_thread
export_thread
get_job_status
cleanup_job
sync_forum_range
parse_thread_title
llm_transform_text
```

Internal target contract:

```python
@dataclass(frozen=True)
class AgentResponse:
    ok: bool
    data: dict[str, object] | None
    error: dict[str, object] | None
    resources: dict[str, str]
    next_actions: list[str]
    warnings: list[str]
```

## Current vs Target Topology

Bad Way:

Each tool function knows too much about DB setup, migrations, repository calls, remote fetches, and job execution.

Good Way:

```text
server/tools.py
  -> application/thread_use_cases.py
  -> application/search_use_cases.py
  -> application/job_use_cases.py
  -> repositories/services/storage/yamibo
```

Why:

This isolates agent-facing semantics from transport details. Later, MCP, CLI, and Web can share the same use-case behavior.

## Skeleton And Signatures

Create:

```python
# src/yamibo_mcp/application/contracts.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class AgentResponse:
    ok: bool
    data: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    resources: dict[str, str] = field(default_factory=dict)
    next_actions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def success(
    data: dict[str, Any],
    *,
    resources: dict[str, str] | None = None,
    next_actions: list[str] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    ...


def failure(
    code: str,
    message: str,
    *,
    retryable: bool = False,
    suggested_action: str | None = None,
) -> dict[str, Any]:
    ...
```

```python
# src/yamibo_mcp/application/thread_use_cases.py
from __future__ import annotations


def ensure_thread(
    *,
    tid: int,
    url: str | None = None,
    base_url: str | None = None,
    forum_id: int | None = None,
) -> dict[str, object]:
    """Return thread detail, fetching and archiving internally when local cache is missing."""
    ...


def archive_thread_job(
    *,
    html_path: str | None = None,
    tid: int | None = None,
    url: str | None = None,
    base_url: str | None = None,
    forum_id: int | None = None,
) -> dict[str, object]:
    ...
```

```python
# src/yamibo_mcp/application/job_use_cases.py
def get_job_status_payload(job_id: str) -> dict[str, object]:
    ...
```

## Data And State Lifecycle

`get_thread(tid)` target lifecycle:

`server adapter -> ensure_thread -> ThreadsRepository.get_thread -> if missing run internal sync -> ThreadsRepository.get_thread -> thread_detail_payload -> return stable payload`

`archive_thread(tid)` target lifecycle:

`server adapter -> archive_thread_job -> JobsRepository.create(sync_thread) -> return job_id payload`

## Blast Radius

Potentially affected areas:

- `server/tools.py` imports and function bodies.
- `server/protocol.py` `TOOLS` mapping.
- `server/app.py` FastMCP registrations.
- CLI outputs from `yamibo-mcp-server`.
- Existing unit tests importing `server.tools`.

Isolation plan:

- Keep function names in `server/tools.py`.
- Move internals behind use cases gradually.
- Reuse existing `thread_detail_payload`, `thread_summary_payload`, and `job_status_payload`.
- Do not introduce response envelope as public shape for existing tools until tests and docs are updated.

## Negative Constraints

- Do not change public tool names.
- Do not require agents to call `archive_thread` before `get_thread`.
- Do not change public response envelope for all tools in one step.
- Do not move daemon handler internals into server.
- Do not introduce network calls in tests.

## Defense And Fallback

Validation:

```bash
uv run pytest tests/unit/test_db tests/unit/test_storage tests/unit/test_domain
uv run pytest tests/unit/test_parsers
```

Assertion standards:

- Cached and uncached `get_thread` paths return the same top-level shape.
- `archive_thread` remains job-oriented.
- Legacy protocol can still call every existing tool name.

Required test cases:

- `ensure_thread` cache-hit path returns the same stable top-level keys as the existing `get_thread` tool.
- `ensure_thread` cache-miss path uses a fake sync/fetch path and returns the same stable top-level keys without requiring caller-side branching.
- `archive_thread_job` creates a queued `sync_thread` job and preserves `tid`, `url`, `base_url`, and optional `forum_id` in payload when provided.
- Legacy JSON-RPC `tools/call` can still dispatch at least `get_thread`, `archive_thread`, and `get_job_status` by their old names.
- Failure response for a sync/fetch exception includes a structured code/message and does not leak secrets or local cookie contents.

If failures occur, inspect first:

- `src/yamibo_mcp/server/tools.py` imports.
- `src/yamibo_mcp/server/protocol.py` `TOOLS`.
- `src/yamibo_mcp/server/app.py` FastMCP tool registration.
- `src/yamibo_mcp/application/thread_use_cases.py` DB connection lifecycle.

Fallback:

- Keep the new application function but make `server/tools.py` call the previous implementation for the failing tool until isolated.
- Revert only the specific moved function body by manual patch.

---

# Phase 2: Multi-Forum Support

## Reason, Background, And Approach

Reason:

The current system mainly targets `https://bbs.yamibo.com/forum-30-1.html`. Future forums such as `forum-55` for light novels, `forum-5` for anime, and `forum-33` for discussion should not require cloned tool/client/repository code.

Approach:

Make forum identity explicit and pass `forum_id` through URL generation, client fetch, search, sync job payload, local fallback search, and storage metadata. Default to `forum_id=30` to preserve compatibility.

## Context Anchors

Must read:

- `src/yamibo_mcp/yamibo/urls.py`
- `src/yamibo_mcp/yamibo/client.py`
- `src/yamibo_mcp/server/tools.py`
- `src/yamibo_mcp/application/thread_use_cases.py` if created
- `src/yamibo_mcp/db/migrations.py`
- `src/yamibo_mcp/db/repositories/threads.py`
- `docs/release-notes.md`

Immutable baseline:

```python
DEFAULT_COMIC_FORUM_ID = 30

def forum_page_url(
    page: int,
    *,
    forum_id: int = DEFAULT_COMIC_FORUM_ID,
    base_url: str = DEFAULT_THREAD_BASE,
) -> str:
    ...
```

Target forum profile:

```python
@dataclass(frozen=True)
class ForumProfile:
    forum_id: int
    name: str
    content_kind: str
    base_url: str = "https://bbs.yamibo.com"
    enabled: bool = True
```

## Current vs Target Topology

Bad Way:

`forum_id=30` is hidden in defaults and docs. Search has `forum_id=30` default inside client. Browse/sync call paths do not consistently expose forum identity.

Good Way:

Every forum-list/search/sync path accepts `forum_id`, defaults to `30`, and stores forum identity with archived data.

Why:

The URL helper is already close to generic. The missing piece is carrying forum identity through the rest of the system.

## Skeleton And Signatures

Create:

```python
# src/yamibo_mcp/domain/forums.py
from __future__ import annotations

from dataclasses import dataclass


DEFAULT_FORUM_ID = 30


@dataclass(frozen=True)
class ForumProfile:
    forum_id: int
    name: str
    content_kind: str
    base_url: str = "https://bbs.yamibo.com"
    enabled: bool = True


def default_forums() -> list[ForumProfile]:
    return [
        ForumProfile(forum_id=30, name="comic", content_kind="comic"),
        ForumProfile(forum_id=55, name="novel", content_kind="novel"),
        ForumProfile(forum_id=5, name="anime", content_kind="discussion"),
        ForumProfile(forum_id=33, name="discussion", content_kind="discussion"),
    ]


def resolve_forum(forum_id: int | None) -> ForumProfile:
    ...
```

Update signatures:

```python
def browse_forum_page(
    *,
    page: int,
    forum_id: int = 30,
    base_url: str = "https://bbs.yamibo.com",
    cookie_file: str | None = None,
    include_sticky: bool = False,
    include_announcements: bool = False,
) -> dict[str, object]:
    ...
```

```python
def search_threads(
    *,
    query: str = "",
    forum_id: int = 30,
    limit: int = 0,
    start_page: int = 1,
    end_page: int | None = None,
    posted_on: str | None = None,
    base_url: str = "https://bbs.yamibo.com",
    cookie_file: str | None = None,
    include_sticky: bool = False,
    include_announcements: bool = False,
) -> dict[str, object]:
    ...
```

## Data And State Lifecycle

`browse_forum_page(page=1, forum_id=55)`:

`tool -> resolve_forum -> forum_page_url(page, forum_id=55) -> client.fetch_forum_threads -> parser -> merge local archive status by tid/forum_id -> response`

`search_threads(query, forum_id=55)`:

`tool -> client.fetch_search_results_all(forum_id=55) -> merge local rows filtered by forum_id -> response`

## Blast Radius

Potentially affected areas:

- URL normalization.
- Search form field `srhfid`.
- Job payload JSON.
- Local fallback search SQL.
- Existing fixtures with `forum-30`.
- Web sync forms.
- API docs.

Isolation plan:

- Default every new parameter to `30`.
- Add forum fields to payloads without removing old fields.
- Make DB columns nullable or defaulted during migration.
- Keep old CLI commands working without new arguments.

## Negative Constraints

- Do not change `threads.tid` from primary key to composite primary key.
- Do not delete `DEFAULT_COMIC_FORUM_ID`; keep it as a compatibility alias if replacing with `DEFAULT_FORUM_ID`.
- Do not require user configuration before default comic forum works.
- Do not put forum-specific branching inside title parser.
- Do not assume every forum has comic-like title syntax.

## Defense And Fallback

Validation:

```bash
uv run pytest tests/unit/test_yamibo tests/unit/test_parsers
```

Required assertions:

```python
assert forum_page_url(1, forum_id=30).endswith("/forum-30-1.html")
assert forum_page_url(1, forum_id=55).endswith("/forum-55-1.html")
```

Required test cases:

- Calling `browse_forum_page(page=1)` omits `forum_id` and still targets `forum-30-1.html`.
- Calling `browse_forum_page(page=1, forum_id=55)` targets `forum-55-1.html` through an injected/fake client.
- `search_threads(query="x", forum_id=55)` passes `srhfid=55` to the search request path or fake client boundary.
- Remote search failure falls back to local search filtered by the same `forum_id`.
- Job payloads created by range sync and archive include `forum_id` when supplied, and remain compatible when omitted.
- Migration/backfill test confirms existing thread rows default to `forum_id=30` and `content_kind='comic'`.

If failures occur, inspect first:

- `src/yamibo_mcp/yamibo/client.py` for remaining hardcoded `forum_id=30`.
- `src/yamibo_mcp/server/tools.py` for missing default `forum_id`.
- `src/yamibo_mcp/yamibo/urls.py` for compatibility aliases.

Fallback:

- Keep new `forum_id` parameter but ignore it temporarily in the failing path, while preserving the signature.

---

# Phase 3: Content-Kind Model, Posts, Blocks, And Assets

## Reason, Background, And Approach

Reason:

Different forums have different content shapes. Comic posts are image-first. Novel posts are long-text-first. Anime and discussion forums are short-text threads with occasional images, links, quotes, or attachments. A model centered on `FloorSnapshot.image_urls` makes every forum look like a comic thread.

Approach:

Add a generic content representation alongside the current compatibility model. Keep `FloorSnapshot` and `ThreadSnapshot.floors` for now, but introduce posts, ordered content blocks, and assets. Use `content_kind` to decide validation and export completeness rules.

## Context Anchors

Must read:

- `src/yamibo_mcp/domain/models.py`
- `src/yamibo_mcp/yamibo/parsers/thread_detail.py`
- `src/yamibo_mcp/storage/markdown.py`
- `src/yamibo_mcp/storage/images.py`
- `src/yamibo_mcp/storage/exports.py`
- `src/yamibo_mcp/domain/validation.py`
- `tests/unit/test_storage/test_markdown.py`

Immutable baseline:

`FloorSnapshot` and `ThreadSnapshot.floors` remain available during this phase.

Target structures:

```python
@dataclass(frozen=True)
class ContentBlock:
    block_id: str
    pid: int
    order_index: int
    block_type: str  # text | image | attachment | quote | link | divider | unknown
    text: str | None = None
    asset_url: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class AssetSnapshot:
    asset_id: str
    tid: int
    pid: int
    asset_type: str  # image | attachment | shared | external_link
    remote_url: str
    local_path: str | None
    exportable: bool
    required: bool
    status: str  # pending | downloaded | skipped | missing
```

## Current vs Target Topology

Bad Way:

Content text and image data are coupled in `FloorSnapshot.content`, `has_images`, and `image_urls`. Missing images heavily influence archive status even when a forum is text-first.

Good Way:

Discuz floors become posts. Each post contains ordered blocks. Media and attachments become assets. Profile-specific logic determines what is required.

Why:

This allows one parser/storage/export pipeline to support comics, novels, discussions, and mixed threads without hardcoding per-forum tool behavior.

## Skeleton And Signatures

Add:

```python
@dataclass(frozen=True)
class PostSnapshot:
    pid: int
    tid: int
    floor_no: int
    publisher: str | None
    pub_time: str | None
    content_text: str
    blocks: list[ContentBlock] = field(default_factory=list)


@dataclass(frozen=True)
class ThreadContentSnapshot:
    tid: int
    forum_id: int
    content_kind: str
    posts: list[PostSnapshot]
    assets: list[AssetSnapshot]
```

Add helpers:

```python
def classify_content_kind(
    forum_id: int | None,
    *,
    image_count: int,
    word_count: int,
) -> str:
    ...


def render_context_by_profile(
    snapshot: ThreadSnapshot,
    *,
    content_kind: str,
    archived_images: dict[int, list[str]] | None = None,
) -> str:
    ...
```

## Data And State Lifecycle

Target lifecycle:

`HTML -> parse_thread_snapshot -> derive posts/content_blocks/assets -> validate_by_profile -> download required assets -> materialize context/metadata -> repository upsert`

Profile rules:

- `comic`: missing required content images means `partial`.
- `novel`: missing main text means invalid; missing non-critical images is warning.
- `discussion`: preserve order, quotes, links, and attachments; missing non-critical images is warning.
- `mixed`: conservative fallback; preserve all extractable content with minimal assumptions.

## Blast Radius

Potentially affected areas:

- Parser tests.
- Markdown rendering.
- Image download and classification.
- Export readiness.
- Web reading preview.
- `metadata.json` shape.

Isolation plan:

- Add new fields to metadata while keeping old `floors`, `image_urls`, `archived_images`, and `missing_image_urls`.
- Derive old image fields from new assets only after tests cover the new representation.
- Keep storage paths stable.

## Negative Constraints

- Do not delete `context.md` or `metadata.json`.
- Do not let novel threads become `partial` simply because they have no images.
- Do not mark all images as export-required.
- Do not rename or remove the `floors` DB table in this phase.
- Do not break existing comic fixture behavior.

## Defense And Fallback

Validation:

```bash
uv run pytest tests/unit/test_storage tests/unit/test_domain tests/unit/test_parsers
```

Assertion standards:

- Comic fixtures retain current export behavior.
- A pure long-text thread produces at least one `text` block.
- An empty reply floor can produce a warning.
- A primary floor with no text and no required asset remains invalid.

Required test cases:

- Comic fixture with required content images still marks missing required images as `partial`.
- Novel fixture with long text and no images archives as complete when required text is present.
- Discussion fixture with short text, quotes, links, and optional images preserves block order.
- Mixed fixture with unknown/unsupported content creates `unknown` blocks instead of dropping content silently.
- `metadata.json` contains new `content_blocks` and `assets` data while preserving old `floors`, `image_urls`, and `archived_images` compatibility fields.
- Export readiness uses profile rules: comic checks required images, novel checks required text, discussion treats non-critical images as warnings.

If failures occur, inspect first:

- `src/yamibo_mcp/storage/markdown.py`
- `src/yamibo_mcp/domain/validation.py`
- `src/yamibo_mcp/storage/exports.py`
- `src/yamibo_mcp/yamibo/parsers/thread_detail.py`

Fallback:

- Keep new block/asset classes but stop writing them into metadata until rendering tests are fixed.

---

# Phase 4: Database Migration For Forums, Blocks, And Assets

## Reason, Background, And Approach

Reason:

The in-memory model and metadata can represent multiple content shapes, but the database must also store forum identity, content kind, blocks, and assets for search, diagnostics, export readiness, and agent resources.

Approach:

Add new tables and columns using idempotent migrations. Preserve `threads.tid` as primary key. Backfill existing rows as comic forum data. Keep old tables as compatibility storage while new repositories are introduced.

## Context Anchors

Must read:

- `src/yamibo_mcp/db/migrations.py`
- `src/yamibo_mcp/db/repositories/threads.py`
- `src/yamibo_mcp/db/repositories/series.py`
- `tests/unit/test_db/test_threads_repository.py`
- `tests/unit/test_db/test_series_repository.py`

Immutable baseline:

`threads.tid` remains the primary key.

Target SQL:

```sql
CREATE TABLE IF NOT EXISTS forums (
  forum_id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  content_kind TEXT NOT NULL,
  base_url TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS content_blocks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  tid INTEGER NOT NULL,
  pid INTEGER NOT NULL,
  order_index INTEGER NOT NULL,
  block_type TEXT NOT NULL,
  text TEXT,
  asset_id TEXT,
  metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS assets (
  asset_id TEXT PRIMARY KEY,
  tid INTEGER NOT NULL,
  pid INTEGER NOT NULL,
  asset_type TEXT NOT NULL,
  remote_url TEXT NOT NULL,
  local_path TEXT,
  exportable INTEGER NOT NULL DEFAULT 0,
  required INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL
);
```

## Current vs Target Topology

Bad Way:

`threads`, `floors`, `title_parse`, and `series` can store comic archive state but cannot query blocks/assets independently.

Good Way:

`threads` stores summary and classification. `content_blocks` stores ordered content. `assets` stores all media and attachment state.

Why:

Agent workflows need compact diagnostics such as missing required assets, content type, block count, word count, and recommended next actions without loading full Markdown.

## Skeleton And Signatures

Add repositories:

```python
class ContentBlocksRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def upsert_blocks(self, tid: int, blocks: list[ContentBlock]) -> None:
        ...

    def list_blocks(self, tid: int) -> list[sqlite3.Row]:
        ...


class AssetsRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def upsert_assets(self, tid: int, assets: list[AssetSnapshot]) -> None:
        ...

    def list_assets(self, tid: int) -> list[sqlite3.Row]:
        ...
```

## Data And State Lifecycle

Target repository lifecycle:

`ThreadsRepository.upsert_snapshot -> upsert threads summary -> upsert floors compatibility -> upsert content_blocks -> upsert assets -> upsert FTS`

Migration lifecycle:

`migrate -> create new tables -> ensure new columns -> insert default forum rows -> backfill existing thread forum fields`

## Blast Radius

Potentially affected areas:

- Migration idempotency.
- Repository tests.
- FTS search.
- Export readiness.
- Web filters.
- Existing local DB files.

Isolation plan:

- Use `CREATE TABLE IF NOT EXISTS`.
- Use `_ensure_column`.
- Backfill with safe defaults:
  `forum_id=30`, `content_kind='comic'`, `primary_media_type='image'`.
- Add repository tests before using new tables in critical runtime paths.

## Negative Constraints

- Do not perform destructive migrations.
- Do not require users to delete `data/forum.db`.
- Do not change existing column meanings.
- Do not remove `floors`.
- Do not make `forum_id` non-null without a backfill.

## Defense And Fallback

Validation:

```bash
uv run pytest tests/unit/test_db
uv run yamibo-init-db
```

Assertion standards:

- Empty DB migrates successfully.
- Existing DB migrates successfully.
- Running migrate twice succeeds.
- Existing `get_thread` and repository reads still work for old data.

Required test cases:

- Empty in-memory DB migration creates `forums`, `content_blocks`, and `assets`.
- Simulated old DB without new columns migrates and backfills existing threads to `forum_id=30`, `content_kind='comic'`, and `primary_media_type='image'`.
- Running `migrate(conn)` twice leaves schema and default forum rows stable.
- `ContentBlocksRepository.upsert_blocks/list_blocks` preserves `tid`, `pid`, `order_index`, `block_type`, and metadata JSON.
- `AssetsRepository.upsert_assets/list_assets` preserves `required`, `exportable`, `status`, local path, and remote URL.
- Existing `ThreadsRepository.upsert_snapshot` tests still pass without requiring callers to provide blocks/assets.

If failures occur, inspect first:

- `src/yamibo_mcp/db/migrations.py` `_ensure_column`.
- New table foreign key assumptions.
- Missing `conn.commit()`.
- Repository row factory assumptions.

Fallback:

- Keep new tables in migration but temporarily skip runtime writes to `content_blocks` and `assets` until repository tests pass.

---

# Phase 5: Job Event Outbox For Future Notifications

## Reason, Background, And Approach

Reason:

The daemon currently updates job rows, and the server/client can poll job status. There is no durable event stream for future notifications. Direct daemon-to-server calls would couple process lifecycles and create failure modes when server is offline.

Approach:

Add a database-backed job event outbox. Job repository state transitions append events. Server/Web/MCP can read events now, and future SSE/WebSocket/notification dispatchers can subscribe to the same table.

## Context Anchors

Must read:

- `src/yamibo_mcp/db/repositories/jobs.py`
- `src/yamibo_mcp/daemon/runner.py`
- `src/yamibo_mcp/daemon/handlers/sync_thread.py`
- `src/yamibo_mcp/daemon/handlers/export_thread.py`
- `src/yamibo_mcp/server/tools.py`
- `src/yamibo_mcp/web/app.py`

Immutable baseline:

Daemon does not directly call server.

Target event types:

```text
job.created
job.started
job.progressed
job.succeeded
job.partial
job.failed
job.cancelled
```

## Current vs Target Topology

Bad Way:

Only the latest job state is stored in `jobs`. Historical transitions and future notification hooks are missing.

Good Way:

`JobsRepository` updates job state and appends `job_events`. Notification consumers read from the outbox.

Why:

This preserves future daemon notification capability without adding process coupling or external infrastructure.

## Skeleton And Signatures

Add:

```python
@dataclass(frozen=True)
class JobEvent:
    event_id: int
    job_id: str
    event_type: str
    status: str | None
    stage: str | None
    payload: dict[str, object]
    created_at: str
```

> **Phase 4 carryover intent**: `ThreadsRepository.upsert_snapshot` should be extended to accept a `forum_id` parameter and write it to the `threads.forum_id` column, avoiding SQL patches in the handler layer.

```python
class JobEventsRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def append(
        self,
        *,
        job_id: str,
        event_type: str,
        status: str | None = None,
        stage: str | None = None,
        payload: dict[str, object] | None = None,
    ) -> JobEvent:
        ...

    def list(
        self,
        *,
        job_id: str | None = None,
        since_event_id: int | None = None,
        limit: int = 100,
    ) -> list[JobEvent]:
        ...
```

SQL:

```sql
CREATE TABLE IF NOT EXISTS job_events (
  event_id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id TEXT NOT NULL,
  event_type TEXT NOT NULL,
  status TEXT,
  stage TEXT,
  payload_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_job_events_job_id_event_id
ON job_events(job_id, event_id);

CREATE INDEX IF NOT EXISTS idx_job_events_created_at
ON job_events(created_at);
```

## Data And State Lifecycle

Target lifecycle:

`handler updates stage/status -> JobsRepository.update_stage/succeed/fail -> JobEventsRepository.append -> server list_job_events -> future dispatcher or resource reader`

> **Phase 4 carryover intent**: The `job_events` table can be used directly without depending on Phase 4's `content_blocks`/`assets` migration. However, the `sync_thread` handler should set `forum_id` and call `ContentBlocksRepository.upsert_blocks` and `AssetsRepository.upsert_assets` after `upsert_snapshot` to keep new tables in sync with the archive chain.

Event append failure lifecycle:

`job state update succeeds -> event append fails -> log warning -> job remains successful/failed according to primary state transition`

## Blast Radius

Potentially affected areas:

- Job repository state transitions.
- Daemon handlers.
- Job status tests.
- Web jobs page.
- Future MCP progress behavior.

> **Phase 4 carryover risks**:
> - `ThreadsRepository.upsert_snapshot` does not yet write `forum_id`/`content_kind`/`primary_media_type`; the handler must set them via SQL after upsert.
> - `content_blocks` and `assets` upsert are not wired into the `sync_thread` handler archive chain; new tables currently only have test writes.
> - `forum_id` column is nullable without a non-null constraint (per spec: no hard constraint before backfill is fully validated).

Isolation plan:

- Keep `jobs` as source of truth for current state.
- Treat `job_events` as append-only diagnostic/notification support.
- Do not make event append failure rollback job state transition.

## Negative Constraints

- Do not make daemon send HTTP requests to server.
- Do not introduce Redis, Celery, or external queues.
- Do not change job state machine semantics.
- Do not require live server for daemon tests.
- Do not expose secrets in event payloads.

## Defense And Fallback

Validation:

```bash
uv run pytest tests/unit/test_db/test_jobs_repository.py
```

Assertion standards:

- Creating a job can append `job.created`.
- `succeed` appends `job.succeeded`.
- `fail` appends `job.failed`.
- `list(job_id=...)` returns events ordered by increasing `event_id`.

Required test cases:

- `JobsRepository.create` appends `job.created` when event recording is enabled.
- `JobsRepository.update_stage` appends `job.progressed` with current stage and progress payload.
- `JobsRepository.succeed` appends `job.succeeded` with artifacts payload.
- `JobsRepository.fail` appends `job.failed` with error code and message.
- Event append failure does not roll back the primary job state transition.
- `JobEventsRepository.list(since_event_id=...)` returns only later events and respects `limit`.

If failures occur, inspect first:

- `src/yamibo_mcp/db/repositories/jobs.py` `create`, `update_stage`, `succeed`, `fail`.
- New `JobEventsRepository.append`.
- Migration for `job_events`.

Fallback:

- Keep the `job_events` table and repository, but temporarily remove event append calls from `JobsRepository` until transaction behavior is fixed.

---

# Phase 6: Agent-Facing Resources And Documentation

## Reason, Background, And Approach

Reason:

Agents should not need to load full `context.md` or `metadata.json` to understand what happened, what is missing, and what action to take next. Compact summary, diagnostics, posts, assets, and job event resources make the system easier for LLMs to use reliably.

Approach:

Add new read-only resource URIs while keeping all existing URIs. Update docs only for implemented behavior. Build diagnostics from DB summary fields and new job events/assets where available.

## Context Anchors

Must read:

- `src/yamibo_mcp/server/resources.py`
- `src/yamibo_mcp/server/app.py`
- `src/yamibo_mcp/server/tools.py`
- `docs/api-reference.md`
- `README.md`
- `AGENTS.md`

Immutable baseline:

Existing resource URIs remain valid:

```text
yamibo://threads/{tid}/context
yamibo://threads/{tid}/metadata
yamibo://threads/{tid}/export
yamibo://series/index
yamibo://series/{series_id}/chapters
```

Target new URIs:

```text
yamibo://forums/index
yamibo://forums/{forum_id}/summary
yamibo://threads/{tid}/summary
yamibo://threads/{tid}/diagnostics
yamibo://threads/{tid}/posts
yamibo://threads/{tid}/assets
yamibo://jobs/{job_id}/events
```

## Current vs Target Topology

Bad Way:

Agents get large payloads or must inspect full files to infer state, completeness, and next actions.

Good Way:

Agents read compact resources first:

`summary -> diagnostics -> posts/assets/context only when needed`

Why:

This reduces token cost and makes agent workflows more deterministic.

## Skeleton And Signatures

Add resource helpers:

```python
def forums_index_uri() -> str:
    return "yamibo://forums/index"


def forum_summary_uri(forum_id: int) -> str:
    return f"yamibo://forums/{forum_id}/summary"


def thread_summary_uri(tid: int) -> str:
    return f"yamibo://threads/{tid}/summary"


def thread_diagnostics_uri(tid: int) -> str:
    return f"yamibo://threads/{tid}/diagnostics"


def thread_posts_uri(tid: int) -> str:
    return f"yamibo://threads/{tid}/posts"


def thread_assets_uri(tid: int) -> str:
    return f"yamibo://threads/{tid}/assets"


def job_events_uri(job_id: str) -> str:
    return f"yamibo://jobs/{job_id}/events"
```

Add diagnostics builder:

```python
def build_thread_diagnostics(tid: int) -> dict[str, object]:
    return {
        "tid": tid,
        "archive_status": None,
        "content_kind": None,
        "missing_required_assets": 0,
        "warnings": [],
        "next_actions": [],
    }
```

## Data And State Lifecycle

Agent workflow:

`search_threads -> receive item resources -> read thread summary -> if incomplete read diagnostics -> call archive/export/retry -> read job events -> read context/metadata only when needed`

Resource read lifecycle:

`resource URI -> parse_resource_uri -> repository/storage read -> compact JSON or text response`

> **Phase 4 carryover intent**: The `yamibo://threads/{tid}/posts` and `yamibo://threads/{tid}/assets` resources can be built directly on Phase 4's `content_blocks` and `assets` tables without additional migrations.

> **Phase 5 carryover intent**: The `yamibo://jobs/{job_id}/events` resource can be built directly on Phase 5's `job_events` table; `JobEventsRepository.list` already supports filtering by `job_id` and pagination via `since_event_id`. Optional extensions: `acquire` → `job.started`; `mark_expired_running_interrupted` → `job.interrupted`; Web console job detail page can render an events timeline.

## Blast Radius

Potentially affected areas:

- FastMCP resource registration.
- Legacy `resources/read`.
- CLI `read-resource`.
- API docs.
- Existing URI parser.

> **Phase 5 carryover risks**:
> - The `sync_thread` handler's partial status path writes SQL directly via `repo.conn.execute` (not `succeed`/`fail`), so it does not trigger event append — the partial path needs to be wired into events.
> - `runner.py` handler exception path calls `repo.fail` which already appends events, but `acquire` / `heartbeat` / `mark_expired_running_interrupted` do not yet append events.
> - Web console job detail page should render an events timeline for improved observability.

Isolation plan:

- Add new URI kinds without changing old URI behavior.
- Keep summary resources compact.
- Mark not-yet-populated fields as empty arrays or nulls, not invented values.

## Negative Constraints

- Do not delete old resources.
- Do not make summary resources read full `context.md`.
- Do not expose cookie values, API keys, login credentials, or sensitive config.
- Do not document unimplemented resources as available.
- Do not use diagnostics to mutate job or thread state.

## Defense And Fallback

Validation:

```bash
uv run pytest tests/unit/test_parsers tests/unit/test_storage tests/unit/test_db
```

Assertion standards:

- Every new URI can be parsed.
- Missing resources return clear errors.
- Diagnostics contains no cookie content or secrets.
- Old resource URIs still resolve.

Required test cases:

- Existing resources still resolve: thread context, thread metadata, thread export, series index, and series chapters.
- New resources parse and dispatch: forums index, forum summary, thread summary, thread diagnostics, thread posts, thread assets, and job events.
- Thread summary does not read full `context.md`; use a large fake context file and assert summary remains compact.
- Diagnostics reports `archive_status`, `content_kind`, missing required asset count, warnings, and next actions from local DB/metadata.
- Job events resource returns append-only events ordered by event id.
- Secret scrub test confirms diagnostics/resources do not include cookie values, API keys, login password, or full credential file contents.

If failures occur, inspect first:

- `src/yamibo_mcp/server/resources.py`
- `src/yamibo_mcp/server/app.py` resource decorators.
- `src/yamibo_mcp/server/tools.py::read_resource`.

Fallback:

- Keep URI helper functions but remove FastMCP registration for any resource whose implementation is not ready.

---

# Completion Criteria

The refactor is complete when:

- `get_thread` has one agent-facing meaning: provide `tid` or `url`, receive thread detail; cache and remote differences are internal.
- `forum-30` default behavior remains compatible.
- `forum_id` can extend to `forum-55`, `forum-5`, and `forum-33`.
- Data structures can represent comic, novel, discussion, and mixed thread content.
- Job status changes append durable outbox events.
- Agents can inspect summary, diagnostics, and events without loading large files.
- Existing tests pass, and new tests cover the introduced contracts.
- **(Phase 2 carryover)** `build_series_summary` no longer imports from `yamibo_mcp.server.tools` in reverse; it has been moved to `application/` or `schemas.py`.
- **(Phase 2 carryover)** References to `DEFAULT_COMIC_FORUM_ID` are unified to `DEFAULT_FORUM_ID` (compat alias preserved).
- **(Phase 4 carryover)** `ThreadsRepository.upsert_snapshot` is extended to accept a `forum_id` parameter and write it to the `threads.forum_id` column.
- **(Phase 4 carryover)** The `sync_thread` handler calls `ContentBlocksRepository.upsert_blocks` and `AssetsRepository.upsert_assets` after `upsert_snapshot`, keeping new tables in sync with the archive chain.
- **(Phase 5 carryover)** The `sync_thread` handler's partial status path is wired into event append (`job.partial`).
- **(Phase 5 carryover)** `acquire` → `job.started` and `mark_expired_running_interrupted` → `job.interrupted` events are added, completing job lifecycle event coverage.
- **(Phase 5 carryover)** Web console job detail page renders an events timeline.
