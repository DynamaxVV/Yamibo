# 百合会归档助手

百合会 (yamibo.com) 论坛本地归档系统。通过 MCP 协议让 LLM 客户端浏览、搜索、归档、检查更新和导出论坛贴子；内嵌 React WebUI 控制台，支持多主题切换。

> 当前版本：`0.12.2`

## 功能特性

- **MCP Server** — 基于 FastMCP，支持 stdio/SSE/HTTP 传输，LLM 客户端直接调用
- **多分区支持** — 漫画区(30)、轻小说区(55)、动漫区(5)、海域区(33) + 7 个扩展分区，通过 `forum_id` 参数切换
- **智能标题解析** — 规则引擎 + LLM 辅助作为内部归档实现细节
- **自动归档** — 抓取帖子 HTML，解析楼层，下载图片，生成结构化本地存档
- **轻小说更新检测** — 独立 `check_thread_updates` / `update_thread` 流程，轻小说贴子支持只看楼主增量更新
- **图片回填闲时任务** — Daemon 空闲时可自动为已归档帖子补跑 `image_backfill`，优先修复缺失图片和非首楼漏记资产
- **内容模型** — 支持 comic/novel/discussion/mixed 四种内容形态，有序内容块 + 资产管理
- **系列管理** — 按 series_key 自动聚合同一系列的多个章节帖子
- **标准化导出** — 漫画/通用贴子 ZIP 打包（context.md + metadata.json + 图片），轻小说导出为可追加的 TXT 文件
- **批量任务** — 支持批量归档和批量 RAG 索引，便于一次性处理多个 tid
- **批量归档探测** — `probe_archived_threads` 可先读取本地归档尾部状态，再配合远端 `last_reply_at` 决定是否补跑
- **Job Event Outbox** — 任务状态变更追加耐久化事件，支持诊断和未来通知
- **本地 RAG 检索** — 基于 FTS5 / PostgreSQL `tsvector` + `pgvector` 的归档内容混合检索，返回可追溯证据片段
- **动漫区清洗语料物化** — 新增 `yamibo-rag-anime-materialize`，可为动漫区生成清洗后的 RAG 中间产物与统计报告
- **Discussion Trend V1** — PostgreSQL-only 的分区趋势查询、topic/user 排名、topic/forum evidence 检索，以及 trend / research report artifact
- **抗反爬增强** — HTTP 客户端切到 `curl_cffi` + 浏览器指纹，请求内置 `acw_sc__v2` 挑战解算、维护页探测与软封锁重试
- **Agent-Friendly Interface** — 区分远端预览/任务创建与本地归档读取，统一结构化错误和紧凑输出
- **Web 控制台** — React+Vite SPA + FastAPI 嵌入式服务，中英文双语，多套可切换主题 + 暗黑模式，支持远程论坛实时浏览（`/forum`）
- **CLI** — 所有工具均可通过命令行直接调用

## 快速开始

更细的文档入口见 [docs/README.md](/Users/vv/Code/Yamibo/docs/README.md)。

### 安装

```bash
git clone git@github.com:DynamaxVV/Yamibo.git
cd Yamibo
uv sync --extra dev
```

### 配置

在浏览器中登录 bbs.yamibo.com，复制 Cookie 到 `.cookie` 文件。

如需 LLM 辅助标题解析和本地 RAG embedding，创建 `yamibo.local.json`：

```json
{
  "yamibo": {
    "cookie_file": ".cookie",
    "novel_author_only_max_pages": 50,
    "novel_author_only_page_delay_seconds": 0.5,
    "image_backfill_enabled": true,
    "image_backfill_dry_run": true,
    "image_backfill_forum_id": 5,
    "image_backfill_auto_interval_seconds": 60,
    "image_backfill_daily_limit": 100,
    "image_backfill_max_pages": 1
  },
  "export": {
    "dir": "data/exports",
    "novel_txt_dir": "data/novel_exports",
    "novel_txt_include_filtered_notes": false,
    "novel_txt_debug_markers": false
  },
  "llm": {
    "base_url": "https://api.openai.com/v1",
    "api_key": "sk-your-key",
    "model": "gpt-4.1-mini"
  },
  "rag": {
    "enabled": true,
    "base_url": "https://api.openai.com/v1",
    "api_key": "sk-your-rag-key",
    "embedding_model": "text-embedding-3-small",
    "embedding_dimensions": 512
  }
}
```

如果要启用多账号池，可以在 `yamibo.account_pool` 里为不同账号配置独立的账号项。常用字段如下：

- `account_id`：账号标识
- `username` / `password`：登录凭据
- `cookie_file`：该账号自己的 cookie 文件
- `enabled`：是否启用
- `weight`：并发/负载权重
- `permission_level`：阅读权限梯队，推荐按 `0/10/20/...` 配置，`0` 是最低
- `request_interval_seconds` / `request_interval_jitter_seconds`：请求节流
- `max_concurrent_leases`：最大并发租约数
- `login_mode`：登录策略

默认低权限账号会优先用于普通抓取；遇到“阅读权限高于 xx 才能浏览”或需要先看论坛列表/搜索结果时，会自动切到更高权限账号。

`yamibo.image_backfill_*` 用于控制 Daemon 的闲时图片回填调度器：

- `image_backfill_enabled`：是否启用自动回填
- `image_backfill_dry_run`：只做差异探测，不写回本地归档
- `image_backfill_forum_id`：候选帖子来源分区
- `image_backfill_auto_interval_seconds`：两次自动入队之间的最短间隔
- `image_backfill_daily_limit`：每日最多自动入队数量
- `image_backfill_max_pages`：单个回填任务最多抓取的远端页数
- `image_backfill_fixed_after`：仅处理某个时间点之后的归档代次

其中：

- `llm.*` 负责标题解析等 chat/completions 场景
- `rag.*` 负责 `/embeddings` 场景
- 若未单独配置 `rag.base_url` / `rag.api_key`，会自动回退到 `llm.base_url` / `llm.api_key`
- `rag.debug_indexing` 或 `YAMIBO_RAG_DEBUG_INDEXING=1` 可开启 RAG 索引调试日志，daemon 会在终端输出请求与失败上下文

所有配置项均可通过 `YAMIBO_*` 环境变量覆盖。详见 [`.env.example`](.env.example)。

数据库后端默认使用 PostgreSQL（`pgvector`）。SQLite 相关路径仅保留给历史迁移、测试和少量兼容代码，不再作为后续主支持方向。通过 `database.backend` 配置切换。

其中轻小说 TXT 导出目录对应 `YAMIBO_NOVEL_TXT_EXPORT_DIR`，轻小说只看楼主更新检测阈值对应 `YAMIBO_NOVEL_AUTHOR_ONLY_MAX_PAGES` 和 `YAMIBO_NOVEL_AUTHOR_ONLY_PAGE_DELAY_SECONDS`。

### Docker 部署

推荐 Docker 部署时使用 PostgreSQL/pgvector + Yamibo Daemon，并将 `data/` 挂载到宿主机；Cookie 放在 `data/cookies/` 下：

```bash
cp .env.docker.example .env
mkdir -p data data/exports data/novel_exports data/cookies data/backups
docker compose build
docker compose up -d postgres
docker compose run --rm yamibo yamibo-init-db
docker compose up -d yamibo
```

默认 Web 控制台地址：`http://localhost:8765`。

对话页默认按外接 OpenAI-compatible Hermes 容器设计，可在 `.env` 中配置：

```env
YAMIBO_LLM_BASE_URL=http://hermes:8000/v1
YAMIBO_LLM_API_KEY=dummy
YAMIBO_LLM_MODEL=hermes
```

如果 Hermes 运行在宿主机，改用 `http://host.docker.internal:8000/v1`。更完整的 data 外挂、Hermes Docker 网络、PostgreSQL 备份恢复和迁移说明见 [`docs/deployment-guide.md`](docs/deployment-guide.md)。

## 运行期行为

- **论坛维护暂停**：当 Daemon 识别到维护页时，会把所有远程任务统一切到 `paused`，并每 10 分钟做一次轻量探测；维护结束后自动恢复。
- **软封锁恢复**：遇到 HTTP 444、429 或未知反爬页时，会优先清理代理缓存并把任务转入重试，而不是直接失败。
- **闲时图片回填**：当队列空闲且达到预算条件时，Daemon 会自动创建 `image_backfill` 任务；Web「任务」页可查看当日入队计数和最近一次入队原因。

### 初始化数据库

```bash
uv run yamibo-init-db
```

### 启动

```bash
# 启动 Daemon（后台任务消费 + Web 控制台）
uv run yamibo-daemon
```

MCP Server 无需手动启动，由 LLM 客户端自动调用。

## 架构

```
LLM Client (Claude Desktop / Cursor)
    │ MCP Protocol (stdio)
    ▼
yamibo-archiver ──创建任务──▶ PostgreSQL (jobs + job_events)
    │                               ▲
    │ application layer             │ 轮询 + 抢占
    │ (archive, update_thread)      │
yamibo-daemon ──────────────────────┘
    ├── 论坛 HTTP 客户端（多分区支持）
    ├── HTML 解析器
    ├── 标题解析（规则 + LLM）
    ├── 图片下载 + 资产管理
    ├── 内容块模型 (content_blocks)
    ├── 文件归档（Markdown + JSON）
    └── 嵌入式 Web 控制台 (http://127.0.0.1:8765)
```

## MCP 客户端集成

### SSE 模式

如果你的 LLM 客户端支持 URL 形式的 MCP 连接，可以把服务端改为 SSE：

```bash
uv run yamibo-archiver stdio --transport sse
```

客户端一般需要配置为指向 SSE 入口，例如：

```json
{
  "mcpServers": {
    "yamibo": {
      "transport": "sse",
      "url": "http://127.0.0.1:8000/sse"
    }
  }
}
```

SSE 模式适合需要保持一个常驻 MCP 服务进程的场景；如果客户端只支持本地进程启动，继续用下面的 `stdio` 配置。

### Claude Desktop

```json
{
  "mcpServers": {
    "yamibo": {
      "command": "uv",
      "args": ["run", "yamibo-archiver", "stdio"],
      "cwd": "/path/to/yamibo"
    }
  }
}
```

### Cursor

```json
{
  "servers": {
    "yamibo": {
      "command": "uv",
      "args": ["run", "yamibo-archiver", "stdio"],
      "cwd": "/path/to/yamibo"
    }
  }
}
```

## CLI 速查

```bash
# 浏览论坛
uv run yamibo-archiver browse-forum-page --page 1

# 按发帖时间排序浏览
uv run yamibo-archiver browse-forum-page --page 1 --order dateline

# 浏览轻小说区
uv run yamibo-archiver browse-forum-page --page 1 --forum-id 55

# 搜索帖子
uv run yamibo-archiver search-threads --query "星灵感应"

# 远端只读预览单帖
uv run yamibo-archiver inspect-remote-thread --tid 572313

# 检查轻小说更新
uv run yamibo-archiver check-thread-updates --tid 544422

# 创建轻小说追加更新任务
uv run yamibo-archiver update-thread --tid 544422

# 创建单帖归档任务
uv run yamibo-archiver create-thread-archive-job --tid 572313

# 批量创建归档任务
uv run yamibo-archiver create-sync-thread-batch-jobs --tid 572313 --tid 572314

# 批量重同步“磁盘有归档、DB 无 thread row”的历史帖子，并顺便回填 PG
uv run python scripts/enqueue_missing_db_thread_sync.py --dry-run --batch-size 100 --max-batches 2
uv run python scripts/enqueue_missing_db_thread_sync.py --batch-size 100 --max-batches 2

# 批量同步
uv run yamibo-archiver create-sync-forum-range-jobs --start-page 1 --end-page 5

# 创建导出任务
uv run yamibo-archiver create-export-thread-job --tid 572313

# 为本地归档构建 RAG 索引
uv run yamibo-archiver create-rag-index-job --tid 572313

# 批量创建 RAG 索引任务
uv run yamibo-archiver create-rag-index-batch-jobs --tid 572313 --tid 572314

# 物化动漫区清洗后的 RAG 语料（PostgreSQL-first）
uv run yamibo-rag-anime-materialize --forum-id 5 --limit 200

# 搜索本地归档内容（只读，不抓远端）
uv run yamibo-archiver search-archived-content --query "星空 告白" --mode hybrid --top-k 5

# 构建分区讨论趋势索引（PostgreSQL-only）
uv run yamibo-archiver create-discussion-trend-index-job --forum-id 5 --start-date 2014-11-01 --end-date 2014-11-30

# 查询 topic 证据和 forum 证据包
uv run yamibo-archiver discussion-topic-evidence --forum-id 5 --start-date 2014-11-01 --end-date 2014-11-30 --topic-label 百合动画 --mode auto
uv run yamibo-archiver forum-evidence-pack --forum-id 33 --query 黑话 --intent slang_usage --mode auto

# 创建趋势报告和论坛研究报告
uv run yamibo-archiver create-discussion-trend-report-job --forum-id 5 --start-date 2014-11-01 --end-date 2014-11-30 --period monthly
uv run yamibo-archiver create-forum-research-report-job --forum-id 33 --start-date 2014-11-01 --end-date 2014-11-30 --question "海域区这个时期的讨论氛围如何？" --intent community_atmosphere

# 查看任务状态
uv run yamibo-archiver job-status <job_id>
uv run yamibo-archiver wait-for-job <job_id>
uv run yamibo-archiver read-job-events <job_id>

# 读取帖子摘要（紧凑 JSON，适合 Agent）
uv run yamibo-archiver read-resource "yamibo://threads/572313/summary"

# 读取帖子诊断（缺失资产、建议操作）
uv run yamibo-archiver read-resource "yamibo://threads/572313/diagnostics"

# 读取轻小说更新检测结果
uv run yamibo-archiver read-resource "yamibo://threads/544422/update-check"

# 数据库备份
uv run yamibo-backup-db
```

历史 file-only 归档说明：

- 一部分 2011–2012 老帖可能在 `data/threads/<tid>/` 下已有 `metadata.json + context.md`，但当前数据库里没有对应 `threads` 记录
- 这类帖子优先建议走远端 `sync_thread` 重同步，让系统自动补齐 `forum_id`、`title_parse`、`floors`、`local_reply_count` 和新版 `context.md`
- [scripts/enqueue_missing_db_thread_sync.py](/Users/vv/Code/Yamibo/scripts/enqueue_missing_db_thread_sync.py) 会自动扫描这类缺失 tid 并按批次创建同步任务
- 在这批帖子处理完成前，不要执行 `cleanup-orphan-thread-dirs`，否则可能误删这些历史归档目录

## 项目结构

```
src/yamibo_mcp/
├── server/          # MCP 注册、CLI、资源处理适配
├── application/     # Agent-facing commands/queries
├── daemon/          # 后台任务消费 + 处理器
├── web_fastapi/     # FastAPI 嵌入式 Web API / 静态资源服务（主路径）
├── web/             # 旧 Web 实现与已打包静态资源
├── yamibo/          # 论坛 HTTP 客户端、HTML 解析器、标题解析
├── storage/         # 文件 I/O（staging、归档、导出、图片）
├── db/              # PostgreSQL schema、迁移、Repository（含少量 SQLite 兼容层）
│   └── repositories/  # jobs, threads, series, content_blocks, assets, job_events
├── services/        # LLM 客户端、标题提示词
├── domain/          # 领域模型、枚举、校验、内容类型
└── maintenance/     # 备份、清理、重置命令

frontend/            # React + Vite 前端源码，构建产物输出到 src/yamibo_mcp/web/static/
```

## 测试

```bash
uv run pytest                      # 全部测试
uv run pytest tests/unit/          # 单元测试
uv run pytest tests/unit/test_parsers/  # 解析器测试
uv run pytest -k "test_name"       # 按名称过滤

# 真实 Hermes Agent 回归
scripts/run_hermes_benchmark.sh
```

## 文档

| 文档 | 说明 |
|------|------|
| [产品需求 & 架构设计](docs/product-requirements.md) | PRD + 系统架构 |
| [Agent 架构导航](docs/architecture-for-agents.md) | 给 AI 编码代理的目录职责、修改路径、简化约束和测试矩阵 |
| [API 接口文档](docs/api-reference.md) | MCP 工具/资源、CLI、Web 路由 |
| [Agent 接口说明](docs/agent-interface.md) | Agent-facing 工具、错误契约、推荐工作流 |
| [Discussion Trend V1](docs/discussion-trend-v1.md) | PostgreSQL-only 趋势索引、evidence 检索、trend/report artifact 与已知约束 |
| [Agent 能力验收标准](docs/agent-evaluation.md) | OpenClaw/Hermes 类 Agent 的验收场景、评分维度与证据要求 |
| [数据库设计](docs/database-design.md) | 表结构、文件存储格式、PostgreSQL JSONB 决策 |
| [账号池设计](docs/account-pool-design.md) | 多账号权限分配与 Cookie 管理 |
| [SQLite-Vec RAG 设计](docs/history/rag-sqlite-vec-design.md) | 历史 SQLite-first RAG 设计，保留作演化参考 |
| [核心模块开发说明](docs/development-guide.md) | 标题解析、Job 系统、配置 |
| [FastAPI 迁移方案](docs/history/fastapi-migration-plan.md) | Web 层迁移决策、构建自动化与路由收敛背景 |
| [部署指南 & 运维手册](docs/deployment-guide.md) | 安装、配置、运维操作 |
| [用户操作手册](docs/user-manual.md) | MCP/Web/CLI 使用方式 |
| [测试方案](docs/testing-strategy.md) | 测试原则、规范、数据与回归策略 |
| [版本更新日志](docs/changelog.md) | 版本演进与本轮变更摘要 |
| [WebUI 设计文档](docs/webui-design.md) | 功能点、主题系统、组件设计、API 端点 |
