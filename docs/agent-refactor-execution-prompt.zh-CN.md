# 面向 Agent 的渐进式重构执行 Prompt

你是一位谨慎的资深 Python 重构工程师。请在 `/Users/vv/Code/Yamibo` 中工作。

你的任务是将 Yamibo MCP 从一个面向漫画区的归档工具，渐进式重构为一个更适合 Agent 使用、支持多分区、多内容形态的论坛归档系统。

全局要求：

- 一次只执行一个阶段。
- 每个阶段开始改动前，必须先阅读该阶段 `Context Anchors` 中列出的所有文件。
- 保留现有 `server + daemon + SQLite jobs` 架构。
- 保持现有 `forum-30` 漫画区行为兼容，除非某个阶段明确说明只是在不改变外部行为的前提下调整内部实现。
- 将 `get_thread` 视为意图级 API：调用方表达“我要拿到帖子详情”，缓存命中、缓存缺失、远端抓取、本地归档这些细节都应隐藏在应用层内部。
- 除非用户明确批准，不要使用 `git reset --hard`、`git checkout --` 这类破坏性 git 命令。
- 优先提交小而清晰、便于审查的补丁。每个阶段完成后运行该阶段列出的测试。

## 总体背景与重构方向

当前系统形态：

- `server/tools.py` 混合了协议适配、数据库访问、远端抓取、inline sync、响应拼装、资源定位，以及部分用例编排逻辑。
- 系统在多个路径上默认假设只处理 `forum-30` 漫画区。URL helper 有一定通用性，但 client、search、jobs、文档和 fixtures 仍带有明显的漫画区假设。
- 当前领域模型以 `ThreadSnapshot`、`FloorSnapshot` 和图片导向的归档为中心。这适合漫画帖，但并不天然适合轻小说帖、讨论帖或混合型短文本帖。
- Job 状态持久化在 SQLite 中，但任务完成后没有耐久化事件流或 outbox，未来若要支持 server/web/MCP 的主动通知，目前没有干净的承接点。

目标系统形态：

- MCP/CLI/Web 入口调用一个更薄的 `server` 适配层。
- 适配层调用 `application` 用例层。
- 用例层调用 repositories、storage services、forum client、parser 以及 daemon 的 job helper。
- forum 身份和内容 profile 成为显式概念。
- 帖子内容可以表示为有序 blocks 和 assets，同时旧的 floors/image 字段在迁移期内继续保留以保证兼容。
- Job 状态变化会追加耐久化事件，未来可以在不依赖 server 进程是否在线的前提下支持通知能力。
- 面向 Agent 的资源层先提供紧凑 summary 和 diagnostics，只有在必要时才让 Agent 读取较大的 `context.md` 或 `metadata.json`。

重构路线：

`server adapter -> application use case -> repository/service/client -> domain contract -> resource/response`

不要直接跳到最终拓扑，请按阶段推进。

## 全局测试要求

每个阶段都必须为该阶段引入的行为新增或更新测试。现有测试全部通过只是必要条件，不是充分条件。

测试纪律：

- 测试必须离线、可重复、确定性。单元测试和集成风格测试都不能真实请求 `yamibo.com`。
- 优先使用 `tests/fixtures/` 和 fake client，而不是 live HTTP。如果需要新增 fixture，请放在明确的目录中，例如 `tests/fixtures/forum_pages/forum_55/` 或 `tests/fixtures/content_shapes/`。
- 遵循仓库在 `docs/testing-strategy.md` 中定义的 3A 风格：Arrange、Act、Assert 清晰分段。
- 测试公共 contract 时，只断言稳定的公开结构，不要断言时间戳、随机生成 id、绝对路径、worker id 等易变值。
- 对 public tool 和 resource 行为，至少要有一个通过 public boundary 的测试，而不是只测 repository 内部实现。
- 凡是数据库改动，都要覆盖：空库迁移、旧库迁移、多次重复迁移的幂等性。
- 凡是引入 fallback 的阶段，都要覆盖成功路径和失败路径：远端成功、远端失败、本地 fallback、资源缺失、非法输入。
- 凡是新增带默认值的参数，都要同时测试“省略参数”和“显式传入默认值”这两种行为。

建议测试组织：

```text
tests/unit/test_application/       # 用例层测试，适合配 fake client/repository
tests/unit/test_server/            # Tool/resource contract 测试
tests/unit/test_db/                # 迁移与 repository 测试
tests/unit/test_yamibo/            # URL/client/parser 单元测试
tests/fixtures/content_shapes/     # 离线的 novel/discussion/mixed 内容夹具
```

不要引入真实联网依赖。如果某个行为当前依赖远端 HTML，请增加 fixture，或为其注入 fake fetcher/client。

---

# Phase 0: 基线审计与 Contract 快照

## 原因、背景与思路

原因：

当前代码能工作，但公开行为主要依赖约定和零散测试来维持。在把逻辑迁移到应用层之前，需要先把当前对外行为固定下来，防止后续重构时无意间改变 MCP/CLI contract。

思路：

先补 characterization tests 或契约快照，不改生产代码。重点让 `get_thread`、`search_threads`、`browse_forum_page`、resource URI 解析和 job status payload 这些核心行为变得可观察、可回归。

## Context Anchors

必须阅读：

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

不可变基准：

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

本阶段不可变的 MCP tool 名称：

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

## 现状与目标拓扑

Bad Way：

`server/tools.py` 直接承担面向协议的函数、迁移、DB 连接、repository 调用、远端 client 调用、inline sync 和 response shaping。

Good Way：

本阶段不改变拓扑，只记录基线行为，为后续逐步演进到下面的结构做准备：

`server/tools.py -> application/use_cases -> repositories/storage/yamibo client -> schemas/resources`

这样做的原因：

在开始搬动逻辑之前，先用 characterization tests 把当前 contract 围起来，形成保护网。

## 骨架与签名

本阶段不应新增生产抽象。如果测试需要 helper，把它们局部放在测试文件中即可。

建议测试 helper 形状：

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

## 数据与状态流向

当前生命周期：

`MCP/CLI -> server.tools -> db/client/handler -> schemas/resources -> response`

后续目标生命周期：

`MCP/CLI -> server adapter -> application use case -> repositories/services -> contract response`

## 爆炸半径

可能影响：

- MCP tool 输出 shape。
- CLI 命令输出 shape。
- 直接 import `server.tools` 的测试。
- `server/protocol.py` 中的 legacy JSON-RPC 协议。
- Web 控制台对 repository 行数据的假设。

隔离方案：

- 只给 public behavior 加测试。
- 不要断言绝对路径、时间戳、生成的 job id、worker id 等易变值，除非它们是 contract 的关键部分。
- 避免修改生产代码。

## 负向约束

- 不要修改数据库 schema。
- 不要重命名 tools。
- 不要改变 `get_thread`、`search_threads`、`browse_forum_page` 的返回字段。
- 不要删除或移动 `tests/unit/_backup_20260619`。
- 不要引入新依赖。

## 防御与降级

验证方式：

```bash
uv run pytest tests/unit/
```

必须新增/更新的测试案例：

- 基于本地 DB 和 fixtures，对 `thread_summary_payload`、`thread_detail_payload`、`job_status_payload` 做 contract snapshot。
- 覆盖所有现有 `yamibo://threads/*` 和 `yamibo://series/*` URI 形式的 resource URI 解析快照。
- 至少选一个不需要联网的短命令，验证其 CLI 输出 shape 兼容。
- 增加一个负向测试，确保 characterization tests 不依赖 `job_id`、时间戳、绝对路径、worker id 等易变字段。

如果失败：

- 先检查是否把测试写得过于严格，错误断言了时间、随机 id、绝对路径或远端依赖行为。
- 优先放宽新增测试，让它断言稳定 shape，而不是偶发值。
- 如需回退，只手工删除本阶段新增的测试或文档，不要使用破坏性 git 命令。

---

# Phase 1: 应用层与 Agent Contract 包装

## 原因、背景与思路

原因：

Agent-facing tools 应该表达“用户意图”，而不是暴露内部执行细节。当前 `server/tools.py` 编排职责过重，后续要同时支持 cache hiding、多 forum、content profile 和 diagnostics，会越来越难稳定维护。

思路：

新增 `application` 包，把用例编排下沉到 use case 层；`server/tools.py` 退化为兼容层适配器。优先抽 thread 和 job 相关用例，同时继续复用现有 schema builder，以保持 public response shape 稳定。

## Context Anchors

必须阅读：

- `src/yamibo_mcp/server/tools.py`
- `src/yamibo_mcp/server/schemas.py`
- `src/yamibo_mcp/server/resources.py`
- `src/yamibo_mcp/server/protocol.py`
- `src/yamibo_mcp/db/repositories/jobs.py`
- `src/yamibo_mcp/db/repositories/threads.py`
- `src/yamibo_mcp/daemon/handlers/sync_thread.py`

不可变基准：

现有 MCP tool 名称保持不变：

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

内部目标 contract：

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

## 现状与目标拓扑

Bad Way：

每个 tool 函数都直接知道 DB 初始化、迁移、repository 调用、远端抓取和 job 执行细节。

Good Way：

```text
server/tools.py
  -> application/thread_use_cases.py
  -> application/search_use_cases.py
  -> application/job_use_cases.py
  -> repositories/services/storage/yamibo
```

这样做的原因：

把 agent-facing 语义和 transport 细节隔离开，后续 MCP、CLI、Web 可以共享相同的用例行为。

## 骨架与签名

新增：

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
    """返回帖子详情；若本地缺失则在内部完成抓取与归档。"""
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

## 数据与状态流向

`get_thread(tid)` 目标生命周期：

`server adapter -> ensure_thread -> ThreadsRepository.get_thread -> if missing run internal sync -> ThreadsRepository.get_thread -> thread_detail_payload -> return stable payload`

`archive_thread(tid)` 目标生命周期：

`server adapter -> archive_thread_job -> JobsRepository.create(sync_thread) -> return job_id payload`

## 爆炸半径

可能影响：

- `server/tools.py` 的导入关系和函数体。
- `server/protocol.py` 中的 `TOOLS` 映射。
- `server/app.py` 的 FastMCP 注册。
- `yamibo-mcp-server` 的 CLI 输出。
- 直接 import `server.tools` 的现有单元测试。

隔离方案：

- 保留 `server/tools.py` 中原有函数名。
- 逐步把内部逻辑迁移到 use case。
- 复用现有 `thread_detail_payload`、`thread_summary_payload`、`job_status_payload`。
- 不要在这个阶段一次性把所有 public tool 响应改成新的 envelope。

## 负向约束

- 不要修改 public tool 名称。
- 不要要求调用方必须先 `archive_thread` 再 `get_thread`。
- 不要一次性改掉所有 public response envelope。
- 不要把 daemon handler 内部逻辑搬进 server。
- 测试中不要引入真实网络调用。

## 防御与降级

验证方式：

```bash
uv run pytest tests/unit/test_db tests/unit/test_storage tests/unit/test_domain
uv run pytest tests/unit/test_parsers
```

断言标准：

- `get_thread` 的缓存命中路径与缓存缺失路径返回相同的顶层结构。
- `archive_thread` 继续保持 job-oriented 语义。
- legacy protocol 仍然可以通过旧名字调用所有现有 tool。

必须新增/更新的测试案例：

- `ensure_thread` 缓存命中路径返回与现有 `get_thread` 一致的稳定顶层 keys。
- `ensure_thread` 缓存缺失路径通过 fake sync/fetch 走通，并返回相同顶层 keys，不要求调用方分支处理。
- `archive_thread_job` 创建 queued `sync_thread` job，并在提供时保留 `tid`、`url`、`base_url`、`forum_id` 到 payload。
- legacy JSON-RPC `tools/call` 至少可以继续分发 `get_thread`、`archive_thread`、`get_job_status`。
- 同步/抓取异常的失败响应应包含结构化 code/message，且不能泄露 secrets 或 cookie 内容。

如果失败，优先检查：

- `src/yamibo_mcp/server/tools.py` 的 imports。
- `src/yamibo_mcp/server/protocol.py` 的 `TOOLS`。
- `src/yamibo_mcp/server/app.py` 的 FastMCP tool 注册。
- `src/yamibo_mcp/application/thread_use_cases.py` 的 DB 连接生命周期。

降级方案：

- 保留新的 application function，但先让 `server/tools.py` 在失败的 tool 上继续调用旧实现。
- 只手工回退对应函数体，不做大范围回滚。

---

# Phase 2: 多分区支持

## 原因、背景与思路

原因：

当前系统主要服务于 `https://bbs.yamibo.com/forum-30-1.html`。未来要支持轻小说区 `forum-55`、动漫区 `forum-5`、水区 `forum-33` 等分区，不应该为每个分区复制一套 client/tool/repository 代码。

思路：

把 forum 身份变成显式参数，并沿着 URL 生成、client 抓取、search、sync job payload、本地 fallback search、storage metadata 全链路传递。默认值仍为 `forum_id=30`，以确保兼容。

## Context Anchors

必须阅读：

- `src/yamibo_mcp/yamibo/urls.py`
- `src/yamibo_mcp/yamibo/client.py`
- `src/yamibo_mcp/server/tools.py`
- `src/yamibo_mcp/application/thread_use_cases.py`（如果已创建）
- `src/yamibo_mcp/db/migrations.py`
- `src/yamibo_mcp/db/repositories/threads.py`
- `docs/release-notes.md`

不可变基准：

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

目标 forum profile：

```python
@dataclass(frozen=True)
class ForumProfile:
    forum_id: int
    name: str
    content_kind: str
    base_url: str = "https://bbs.yamibo.com"
    enabled: bool = True
```

## 现状与目标拓扑

Bad Way：

`forum_id=30` 被隐藏在默认参数和文档里。search 在 client 内部默认 `forum_id=30`。browse/sync 调用链没有一致地暴露 forum 身份。

Good Way：

所有 forum list/search/sync 路径都能接受 `forum_id`，默认仍是 `30`，同时把 forum 身份写入归档数据。

这样做的原因：

URL helper 已经接近通用化，缺的主要是把 forum 身份沿着整条链路传下去。

## 骨架与签名

新增：

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

更新签名：

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

## 数据与状态流向

`browse_forum_page(page=1, forum_id=55)`：

`tool -> resolve_forum -> forum_page_url(page, forum_id=55) -> client.fetch_forum_threads -> parser -> merge local archive status by tid/forum_id -> response`

`search_threads(query, forum_id=55)`：

`tool -> client.fetch_search_results_all(forum_id=55) -> merge local rows filtered by forum_id -> response`

## 爆炸半径

可能影响：

- URL normalization。
- 搜索表单的 `srhfid`。
- job payload JSON。
- 本地 fallback search SQL。
- 默认假设 `forum-30` 的 fixtures。
- Web sync 表单。
- API 文档。

隔离方案：

- 所有新参数默认值都设为 `30`。
- 在 payload 中新增 forum 字段，但不删除旧字段。
- DB 新列做成 nullable 或有默认值。
- 保持旧 CLI 命令在不传新参数时继续可用。

## 负向约束

- 不要把 `threads.tid` 改成复合主键。
- 不要删除 `DEFAULT_COMIC_FORUM_ID`，即使引入 `DEFAULT_FORUM_ID` 也要保留兼容别名。
- 不要要求用户必须先配置 forums 才能使用默认漫画区。
- 不要在 title parser 里引入 forum-specific 分支。
- 不要假设所有 forum 都有漫画区式的标题语法。

## 防御与降级

验证方式：

```bash
uv run pytest tests/unit/test_yamibo tests/unit/test_parsers
```

必要断言：

```python
assert forum_page_url(1, forum_id=30).endswith("/forum-30-1.html")
assert forum_page_url(1, forum_id=55).endswith("/forum-55-1.html")
```

必须新增/更新的测试案例：

- 调用 `browse_forum_page(page=1)` 省略 `forum_id` 时，仍指向 `forum-30-1.html`。
- 调用 `browse_forum_page(page=1, forum_id=55)` 时，通过 injected/fake client 指向 `forum-55-1.html`。
- `search_threads(query="x", forum_id=55)` 会把 `srhfid=55` 传入搜索路径或 fake client 边界。
- 远端搜索失败时，本地 fallback search 仍按相同 `forum_id` 过滤。
- range sync 和 archive 创建的 job payload 在传入时包含 `forum_id`，不传时保持兼容。
- migration/backfill 测试确认旧 thread 行默认回填 `forum_id=30` 和 `content_kind='comic'`。

如果失败，优先检查：

- `src/yamibo_mcp/yamibo/client.py` 中是否仍有硬编码 `forum_id=30`。
- `src/yamibo_mcp/server/tools.py` 中是否遗漏默认 `forum_id`。
- `src/yamibo_mcp/yamibo/urls.py` 中的兼容别名。

降级方案：

- 先保留新 `forum_id` 参数，但在失败路径中临时忽略该参数，同时保留其公开签名。

---

# Phase 3: 内容类型模型、Posts、Blocks 与 Assets

## 原因、背景与思路

原因：

不同 forum 的内容形态不同。漫画帖是图片优先，轻小说帖是长文本优先，动漫区和水区则更多是短文本讨论，夹杂少量图片、链接、引用或附件。以 `FloorSnapshot.image_urls` 为中心的模型会把所有 forum 都误当成漫画帖。

思路：

在保留当前兼容模型的同时，引入更泛化的内容表示。暂时继续保留 `FloorSnapshot` 和 `ThreadSnapshot.floors`，但新增 posts、有序 content blocks 和 assets。之后根据 `content_kind` 决定校验逻辑与导出完整性规则。

## Context Anchors

必须阅读：

- `src/yamibo_mcp/domain/models.py`
- `src/yamibo_mcp/yamibo/parsers/thread_detail.py`
- `src/yamibo_mcp/storage/markdown.py`
- `src/yamibo_mcp/storage/images.py`
- `src/yamibo_mcp/storage/exports.py`
- `src/yamibo_mcp/domain/validation.py`
- `tests/unit/test_storage/test_markdown.py`

不可变基准：

迁移期内 `FloorSnapshot` 和 `ThreadSnapshot.floors` 必须继续可用。

目标结构：

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

## 现状与目标拓扑

Bad Way：

正文和图片数据耦合在 `FloorSnapshot.content`、`has_images`、`image_urls` 中。图片是否缺失对归档状态影响过重，即使某个 forum 其实以文本为主。

Good Way：

Discuz 楼层抽象成 posts；每个 post 由有序 blocks 组成；媒体和附件统一收敛成 assets；不同 profile 决定什么是“必需内容”。

这样做的原因：

这样可以在一条 parser/storage/export 链上同时支撑漫画、小说、讨论帖和混合内容，而不用在 tool 层做大量 forum 特判。

## 骨架与签名

新增：

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

新增 helper：

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

> **Phase 2 遗留意图**：`classify_content_kind` 应优先调用 `resolve_forum(forum_id).content_kind` 获取 forum 级别的内容类型，再根据 `image_count`/`word_count` 做细粒度修正，避免在 tool 层重复维护 forum→content_kind 映射。

## 数据与状态流向

目标生命周期：

`HTML -> parse_thread_snapshot -> derive posts/content_blocks/assets -> validate_by_profile -> download required assets -> materialize context/metadata -> repository upsert`

Profile 规则：

- `comic`：缺失 required content images 记为 `partial`。
- `novel`：缺失主文本记为 invalid；缺失非关键图片记为 warning。
- `discussion`：保留顺序、引用、链接和附件；缺失非关键图片记为 warning。
- `mixed`：保守兜底，尽量保留所有可提取内容。

## 爆炸半径

可能影响：

- parser tests。
- Markdown 渲染。
- 图片下载与分类逻辑。
- export readiness。
- Web reading preview。
- `metadata.json` shape。

隔离方案：

- 在 metadata 中新增字段，同时继续保留旧的 `floors`、`image_urls`、`archived_images`、`missing_image_urls`。
- 在测试覆盖新表示之前，不要立刻删除旧的图片兼容字段。
- 保持 storage path 不变。

## 负向约束

- 不要删除 `context.md` 或 `metadata.json`。
- 不要因为 novel thread 没有图片就把它标记为 `partial`。
- 不要把所有图片都视为 export-required。
- 本阶段不要重命名或删除 `floors` DB 表。
- 不要破坏现有漫画 fixture 的行为。

## 防御与降级

验证方式：

```bash
uv run pytest tests/unit/test_storage tests/unit/test_domain tests/unit/test_parsers
```

断言标准：

- 漫画 fixture 的导出行为保持不变。
- 纯长文本 thread 至少生成一个 `text` block。
- 空回复楼可以产生 warning。
- 主楼若既无文本又无 required asset，应保持 invalid。

必须新增/更新的测试案例：

- 漫画 fixture 在缺失 required content image 时，仍会被标记为 `partial`。
- novel fixture 在有足够长文本但没有图片时，仍归档为 complete。
- discussion fixture 中的短文本、引用、链接和可选图片会保持 block 顺序。
- mixed fixture 中无法识别的内容应生成 `unknown` block，而不是被静默丢弃。
- `metadata.json` 中应包含新的 `content_blocks` 和 `assets` 数据，同时保留旧的 `floors`、`image_urls`、`archived_images` 兼容字段。
- export readiness 应按 profile 规则判断：comic 检查 required image，novel 检查 required text，discussion 将非关键图片降为 warning。

如果失败，优先检查：

- `src/yamibo_mcp/storage/markdown.py`
- `src/yamibo_mcp/domain/validation.py`
- `src/yamibo_mcp/storage/exports.py`
- `src/yamibo_mcp/yamibo/parsers/thread_detail.py`

降级方案：

- 保留新的 block/asset 类，但在 rendering 测试稳定前，暂时不要把它们写入 metadata。

---

# Phase 4: 为 Forums、Blocks 与 Assets 做数据库迁移

## 原因、背景与思路

原因：

仅有内存模型和 metadata 能表达多内容形态还不够。数据库也必须存储 forum 身份、content kind、blocks 和 assets，才能支撑搜索、诊断、导出完整性检查和面向 Agent 的资源读取。

思路：

通过幂等迁移增加新表和新列，保留 `threads.tid` 作为主键，把历史数据回填为漫画区数据。旧表继续作为兼容层存在，直到新 repository 路径完全稳定。

## Context Anchors

必须阅读：

- `src/yamibo_mcp/db/migrations.py`
- `src/yamibo_mcp/db/repositories/threads.py`
- `src/yamibo_mcp/db/repositories/series.py`
- `tests/unit/test_db/test_threads_repository.py`
- `tests/unit/test_db/test_series_repository.py`

不可变基准：

`threads.tid` 继续作为主键。

目标 SQL：

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

## 现状与目标拓扑

Bad Way：

`threads`、`floors`、`title_parse`、`series` 能表示漫画归档的摘要状态，但不能独立查询 blocks/assets。

Good Way：

`threads` 负责摘要与分类，`content_blocks` 存有序内容，`assets` 负责统一的媒体和附件状态。

这样做的原因：

Agent 工作流需要低 token 的 summary，也需要在必要时深入读取 blocks/assets。数据库不能只保存一个 Markdown 文件。

## 骨架与签名

新增 repositories：

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

## 数据与状态流向

目标 repository 生命周期：

`ThreadsRepository.upsert_snapshot -> upsert threads summary -> upsert floors compatibility -> upsert content_blocks -> upsert assets -> upsert FTS`

迁移生命周期：

`migrate -> create new tables -> ensure new columns -> insert default forum rows -> backfill existing thread forum fields`

> **Phase 2 遗留意图**：迁移必须给 `threads` 表新增 `forum_id INTEGER` 列（nullable + 默认回填 `30`），才能让本地 fallback search 按分区过滤。同时逐步将代码中对 `DEFAULT_COMIC_FORUM_ID` 的引用统一为 `DEFAULT_FORUM_ID`（别名已存在于 `urls.py`）。

## 爆炸半径

可能影响：

- migration 的幂等性。
- repository tests。
- FTS search。
- export readiness。
- Web filters。
- 用户已有的本地 DB 文件。

隔离方案：

- 使用 `CREATE TABLE IF NOT EXISTS`。
- 使用 `_ensure_column`。
- 用安全默认值回填历史数据：
  `forum_id=30`、`content_kind='comic'`、`primary_media_type='image'`。
- 在运行时使用新表之前，先补 repository 测试。

## 负向约束

- 不要做 destructive migration。
- 不要要求用户删除 `data/forum.db`。
- 不要改变现有列的语义。
- 不要移除 `floors`。
- 如果没有回填，不要直接把 `forum_id` 设为 non-null 强约束。
- **（Phase 2 遗留）** 不要删除 `DEFAULT_COMIC_FORUM_ID`，它应继续作为 `DEFAULT_FORUM_ID` 的兼容别名存在；新增代码统一使用 `DEFAULT_FORUM_ID`。

## 防御与降级

验证方式：

```bash
uv run pytest tests/unit/test_db
uv run yamibo-init-db
```

断言标准：

- 空库迁移成功。
- 旧库迁移成功。
- 重复执行迁移不会出错。
- 旧数据上的 `get_thread` 和 repository 读取仍然正常。

必须新增/更新的测试案例：

- 空 in-memory DB 迁移后，能成功创建 `forums`、`content_blocks`、`assets`。
- 模拟旧 DB 缺少新列时，迁移后会把已有 threads 回填为 `forum_id=30`、`content_kind='comic'`、`primary_media_type='image'`。
- 重复运行 `migrate(conn)` 两次后，schema 和默认 forum rows 保持稳定。
- `ContentBlocksRepository.upsert_blocks/list_blocks` 能保留 `tid`、`pid`、`order_index`、`block_type`、metadata JSON。
- `AssetsRepository.upsert_assets/list_assets` 能保留 `required`、`exportable`、`status`、local path 和 remote URL。
- 现有 `ThreadsRepository.upsert_snapshot` 测试在调用方不提供 blocks/assets 时仍然通过。
- **（Phase 2 遗留）** 老客户端调用 `browse_forum_page(page=1)` 不传 `forum_id` 时，行为仍等价于 `forum_id=30`，不会报错或返回其他分区数据。
- **（Phase 2 遗留）** `threads` 表新增 `forum_id` 列后，本地 fallback search（`search_threads` 远端失败时）能按传入的 `forum_id` 过滤结果，不混入其他分区的归档帖子。
- **（Phase 2 遗留）** `ThreadsRepository.search_threads` 支持按 `forum_id` 可选过滤参数，不传时返回所有分区（向后兼容）。

如果失败，优先检查：

- `src/yamibo_mcp/db/migrations.py` 的 `_ensure_column`。
- 新表的外键假设是否过强。
- 是否漏掉 `conn.commit()`。
- repository 是否依赖了不兼容的 row factory 或列名。

降级方案：

- 先保留 migration 中的新表，但在 repository 测试稳定前，临时跳过对 `content_blocks` 和 `assets` 的运行时写入。

---

# Phase 5: 为未来通知能力增加 Job Event Outbox

## 原因、背景与思路

原因：

当前 daemon 会更新 job 行，server/client 通过轮询读取状态，但没有历史事件流。若未来直接让 daemon 调 server，会带来进程耦合、server 不在线时失败、启动顺序复杂等问题。

思路：

增加数据库驱动的 job event outbox。job 状态变化时由 repository 追加事件，server/web/MCP 先支持读取事件；未来若要加 SSE/WebSocket/通知分发器，也从同一张表消费。

## Context Anchors

必须阅读：

- `src/yamibo_mcp/db/repositories/jobs.py`
- `src/yamibo_mcp/daemon/runner.py`
- `src/yamibo_mcp/daemon/handlers/sync_thread.py`
- `src/yamibo_mcp/daemon/handlers/export_thread.py`
- `src/yamibo_mcp/server/tools.py`
- `src/yamibo_mcp/web/app.py`

不可变基准：

daemon 不能直接主动调用 server。

目标事件类型：

```text
job.created
job.started
job.progressed
job.succeeded
job.partial
job.failed
job.cancelled
```

## 现状与目标拓扑

Bad Way：

当前只有 `jobs` 表中的最新状态，没有历史状态事件，也没有未来通知能力的挂载点。

Good Way：

`JobsRepository` 在更新 job 状态时同时 append `job_events`；通知消费者未来统一从 outbox 读取。

这样做的原因：

保留 daemon 主动通知的未来能力，同时避免 daemon 依赖 server 进程是否在线。

## 骨架与签名

新增：

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

> **Phase 4 遗留意图**：`ThreadsRepository.upsert_snapshot` 应扩展为接受 `forum_id` 参数，写入 `threads.forum_id` 列，避免 handler 层通过 SQL 补写。

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

SQL：

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

## 数据与状态流向

目标生命周期：

`handler updates stage/status -> JobsRepository.update_stage/succeed/fail -> JobEventsRepository.append -> server list_job_events -> future dispatcher or resource reader`

> **Phase 4 遗留意图**：本 Phase 的 `job_events` 表可直接追加事件，不依赖 Phase 4 的 `content_blocks`/`assets` 迁移。但 `sync_thread` handler 应在 `upsert_snapshot` 后补写 `forum_id`、调用 `ContentBlocksRepository.upsert_blocks` 和 `AssetsRepository.upsert_assets`，以使新表数据与归档链路对齐。

事件追加失败时的生命周期：

`job state update succeeds -> event append fails -> log warning -> job remains successful/failed according to primary state transition`

## 爆炸半径

可能影响：

- job repository 的状态变更逻辑。
- daemon handlers。
- job 相关测试。
- Web jobs 页面。
- 未来 MCP progress 行为。

> **Phase 4 遗留风险**：
> - `ThreadsRepository.upsert_snapshot` 尚未自动写入 `forum_id`/`content_kind`/`primary_media_type`，需 handler 层在调用后通过 SQL 补写。
> - `content_blocks` 和 `assets` 的 upsert 未接入 `sync_thread` handler 的归档链路，新表目前只有测试写入。
> - `forum_id` 列为 nullable，未设 non-null 约束（符合规范要求：回填前不强制）。

隔离方案：

- `jobs` 仍然是当前状态的 source of truth。
- `job_events` 作为 append-only 的诊断/通知支撑层。
- event append 失败不能回滚主 job 状态变更。

## 负向约束

- 不要让 daemon 发送 HTTP 请求到 server。
- 不要引入 Redis、Celery 或外部队列。
- 不要改变 job state machine 语义。
- 不要让测试依赖在线 server。
- 不要在 event payload 中暴露 secrets。

## 防御与降级

验证方式：

```bash
uv run pytest tests/unit/test_db/test_jobs_repository.py
```

断言标准：

- 创建 job 时可以追加 `job.created`。
- `succeed` 时追加 `job.succeeded`。
- `fail` 时追加 `job.failed`。
- `list(job_id=...)` 结果按 `event_id` 递增。

必须新增/更新的测试案例：

- `JobsRepository.create` 在启用事件记录时会追加 `job.created`。
- `JobsRepository.update_stage` 会追加 `job.progressed`，并携带 stage/progress payload。
- `JobsRepository.succeed` 会追加 `job.succeeded`，并附带 artifacts payload。
- `JobsRepository.fail` 会追加 `job.failed`，并附带 error code/message。
- event append 失败不会回滚主 job 状态变更。
- `JobEventsRepository.list(since_event_id=...)` 只返回后续事件，并正确遵守 `limit`。

如果失败，优先检查：

- `src/yamibo_mcp/db/repositories/jobs.py` 中的 `create`、`update_stage`、`succeed`、`fail`。
- 新增的 `JobEventsRepository.append`。
- `job_events` 的 migration。

降级方案：

- 保留 `job_events` 表和 repository，但在事务行为修稳前，临时移除 `JobsRepository` 中的 append 调用。

---

# Phase 6: 面向 Agent 的 Resources 与文档

## 原因、背景与思路

原因：

Agent 不应该为了判断状态、缺失项或下一步动作，就被迫读取完整 `context.md` 或 `metadata.json`。更紧凑的 summary、diagnostics、posts、assets、job events 资源能显著降低 token 成本，并提高调用稳定性。

思路：

新增只读 resource URI，同时保留所有现有 URI。文档只记录已经实现的行为。diagnostics 尽量基于 DB 摘要字段和新的 job events/assets 来构造。

## Context Anchors

必须阅读：

- `src/yamibo_mcp/server/resources.py`
- `src/yamibo_mcp/server/app.py`
- `src/yamibo_mcp/server/tools.py`
- `docs/api-reference.md`
- `README.md`
- `AGENTS.md`

不可变基准：

现有资源 URI 必须继续可用：

```text
yamibo://threads/{tid}/context
yamibo://threads/{tid}/metadata
yamibo://threads/{tid}/export
yamibo://series/index
yamibo://series/{series_id}/chapters
```

目标新增 URI：

```text
yamibo://forums/index
yamibo://forums/{forum_id}/summary
yamibo://threads/{tid}/summary
yamibo://threads/{tid}/diagnostics
yamibo://threads/{tid}/posts
yamibo://threads/{tid}/assets
yamibo://jobs/{job_id}/events
```

## 现状与目标拓扑

Bad Way：

当前 Agent 要么读大 payload，要么去读完整文件，才能推断状态、完整性和下一步动作。

Good Way：

先读紧凑资源：

`summary -> diagnostics -> posts/assets/context only when needed`

这样做的原因：

这是 agent-friendly 的关键：低 token、可诊断、可恢复。

## 骨架与签名

新增 resource helper：

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

新增 diagnostics builder：

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

## 数据与状态流向

Agent 工作流：

`search_threads -> receive item resources -> read thread summary -> if incomplete read diagnostics -> call archive/export/retry -> read job events -> read context/metadata only when needed`

resource 读取生命周期：

`resource URI -> parse_resource_uri -> repository/storage read -> compact JSON or text response`

> **Phase 4 遗留意图**：`yamibo://threads/{tid}/posts` 和 `yamibo://threads/{tid}/assets` 资源可直接基于 Phase 4 新增的 `content_blocks` 和 `assets` 表构建，无需额外迁移。

> **Phase 5 遗留意图**：`yamibo://jobs/{job_id}/events` 资源可直接基于 Phase 5 新增的 `job_events` 表构建，`JobEventsRepository.list` 已支持按 `job_id` 过滤和 `since_event_id` 分页。可选扩展：`acquire` → `job.started`；`mark_expired_running_interrupted` → `job.interrupted`；Web 控制台 job detail 页可追加 events 时间线展示。

## 爆炸半径

可能影响：

- FastMCP resource 注册。
- legacy `resources/read`。
- CLI `read-resource`。
- API 文档。
- 现有 URI parser。

> **Phase 5 遗留风险**：
> - `sync_thread` handler 中的 partial 状态直接通过 `repo.conn.execute` 写 SQL（非 `succeed`/`fail`），不会触发事件追加 — 需后续将 partial 路径也接入事件。
> - `runner.py` 中 handler 异常后调用 `repo.fail` 已有事件追加，但 `acquire` / `heartbeat` / `mark_expired_running_interrupted` 暂未追加事件。
> - Web 控制台 job detail 页可追加 events 时间线展示，提升可观测性。

隔离方案：

- 新增 URI kinds，但不改变旧 URI 行为。
- summary resources 保持紧凑。
- 对于尚未完全填充的字段，用空数组或 null，而不是伪造值。

## 负向约束

- 不要删除旧 resources。
- 不要让 summary resources 读取完整 `context.md`。
- 不要暴露 cookie、API key、login credentials 或其他敏感配置。
- 不要把未实现的资源写进文档当成已实现功能。
- 不要让 diagnostics 具备修改 job 或 thread 状态的副作用。

## 防御与降级

验证方式：

```bash
uv run pytest tests/unit/test_parsers tests/unit/test_storage tests/unit/test_db
```

断言标准：

- 所有新 URI 都能被解析。
- 缺失资源时返回清晰错误。
- diagnostics 中不包含 cookie 内容或 secrets。
- 旧资源 URI 仍然可解析。

必须新增/更新的测试案例：

- 现有 resources 继续可用：thread context、thread metadata、thread export、series index、series chapters。
- 新 resources 可以被解析并分发：forums index、forum summary、thread summary、thread diagnostics、thread posts、thread assets、job events。
- thread summary 不会读取完整 `context.md`；可以构造一个较大的 fake context 文件并断言 summary 仍然紧凑。
- diagnostics 能从本地 DB/metadata 中报告 `archive_status`、`content_kind`、缺失 required asset 数、warnings 和 next actions。
- job events resource 以 event id 递增顺序返回 append-only events。
- secret scrub 测试确认 diagnostics/resources 不包含 cookie 值、API key、login password 或完整凭据文件内容。

如果失败，优先检查：

- `src/yamibo_mcp/server/resources.py`
- `src/yamibo_mcp/server/app.py` 中的 resource decorators。
- `src/yamibo_mcp/server/tools.py::read_resource`

降级方案：

- 保留新的 URI helper，但对于尚未稳定的 resource，临时不要注册到 FastMCP。

---

# 完成标准

当满足以下条件时，可视为本轮重构完成：

- `get_thread` 对 Agent 只有一个语义：提供 `tid` 或 `url`，得到帖子详情；缓存和远端差异都隐藏在内部。
- `forum-30` 默认行为保持兼容。
- 通过 `forum_id` 能扩展到 `forum-55`、`forum-5`、`forum-33`。
- 数据结构能表达 comic、novel、discussion、mixed 四类内容。
- job 状态变化会追加耐久化 outbox events。
- Agent 能通过 summary、diagnostics、events 低成本理解状态和下一步动作。
- 现有测试全部通过，并且新增测试覆盖了新引入的 contract 与行为。
- **（Phase 2 遗留）** `build_series_summary` 不再通过 `from yamibo_mcp.server.tools import` 反向引用 tools.py，已迁入 `application/` 或 `schemas.py`。
- **（Phase 2 遗留）** 代码中对 `DEFAULT_COMIC_FORUM_ID` 的引用已统一为 `DEFAULT_FORUM_ID`（兼容别名保留）。
- **（Phase 4 遗留）** `ThreadsRepository.upsert_snapshot` 已扩展为接受 `forum_id` 参数并写入 `threads.forum_id` 列。
- **（Phase 4 遗留）** `sync_thread` handler 在 `upsert_snapshot` 后调用 `ContentBlocksRepository.upsert_blocks` 和 `AssetsRepository.upsert_assets`，新表数据与归档链路对齐。
- **（Phase 5 遗留）** `sync_thread` handler 中的 partial 状态路径已接入事件追加（`job.partial`）。
- **（Phase 5 遗留）** `acquire` → `job.started`、`mark_expired_running_interrupted` → `job.interrupted` 事件已补充，job lifecycle 事件覆盖完整。
- **（Phase 5 遗留）** Web 控制台 job detail 页已展示 events 时间线。

