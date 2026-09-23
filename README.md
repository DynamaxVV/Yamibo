# 百合会归档助手

百合会 (yamibo.com) 论坛本地归档系统。通过 MCP 协议让 LLM 客户端浏览、搜索、归档、检查更新和导出论坛贴子；内嵌 React WebUI 控制台，支持多主题切换。

> 当前版本：`1.3.1`

## 功能特性

- **MCP Server** — 基于 FastMCP，支持 stdio/SSE/HTTP 传输，LLM 客户端直接调用
- **多分区支持** — 漫画区(30)、轻小说区(55)、动漫区(5)、海域区(33) + 7 个扩展分区，通过 `forum_id` 参数切换
- **智能标题解析** — 规则引擎 + LLM 辅助作为内部归档实现细节
- **自动归档** — 抓取帖子 HTML，解析楼层，下载图片，生成结构化本地存档
- **轻小说更新检测** — 独立 `check_thread_updates` / `update_thread` 流程，轻小说贴子支持只看楼主增量更新
- **图片回填闲时任务** — Daemon 以 single-flight、有界 TID 游标扫描自动补跑 `image_backfill`，避免维护查询与在线 Web 请求竞争
- **阅读中单图补取** — 本地帖子阅读预览可只重试一张缺失图片；优先复用原位置的有效文件，避免为一张图片重新同步整帖
- **内容模型** — 支持 comic/novel/discussion/mixed 四种内容形态，有序内容块 + 资产管理
- **系列管理** — 按 series_key 自动聚合同一系列的多个章节帖子
- **标准化导出** — 漫画/通用贴子 ZIP 打包（context.md + metadata.json + 图片），轻小说导出为可追加的 TXT 文件
- **批量任务** — 支持批量归档和批量 RAG 索引，便于一次性处理多个 tid
- **批量归档探测** — `probe_archived_threads` 可先读取本地归档尾部状态，再配合远端 `last_reply_at` 决定是否补跑
- **Job Event Outbox** — 任务状态变更追加耐久化事件，支持诊断和未来通知
- **本地 RAG 检索（低频可选）** — 基于 FTS5 / PostgreSQL `tsvector` + `pgvector` 的归档内容混合检索；不影响归档主流程
- **动漫区清洗语料物化（低频可选）** — 为特定研究场景生成 RAG 中间产物；不属于日常归档流程
- **Discussion Trend V1（低频可选）** — PostgreSQL-only 的分区趋势和证据查询；不属于日常维护主线
- **抗反爬增强** — HTTP 客户端切到 `curl_cffi` + 浏览器指纹，请求内置 `acw_sc__v2` 挑战解算、维护页探测与软封锁重试
- **Agent-Friendly Interface** — 区分远端预览/任务创建与本地归档读取，统一结构化错误和紧凑输出
- **Web 控制台** — React+Vite SPA + FastAPI 嵌入式服务，中英文双语；采用编辑式档案视觉语言，覆盖归档、任务、审核、日志与沉浸阅读等页面，支持多套主题和暗黑模式，并可实时浏览远程论坛（`/forum`）
- **CLI** — 所有工具均可通过命令行直接调用

## 快速开始

更细的文档入口见 [docs/文档索引.md](docs/文档索引.md)。

Capability Manifest、Job Recovery 和外部 Agent 接口边界见 [LLM 原生运行时路线图](docs/LLM原生运行时路线图.md)。Yamibo 提供可选的内置 Pydantic AI 业务助手，通过受限本地 MCP 调用业务能力；Codex、Hermes 等外部终端仍可复用公开 MCP/CLI 接口。内置助手的边界与验收记录见 [实现与配置](docs/内置Agent实现与配置.md)。

### 安装

```bash
git clone git@github.com:DynamaxVV/Yamibo.git
cd Yamibo
uv sync --extra dev
```

### 配置

在浏览器中登录 bbs.yamibo.com，复制 Cookie 到 `.cookie` 文件。

如需 LLM 辅助标题解析和本地 RAG embedding，复制 `yamibo.local.example.json` 为 `yamibo.local.json` 后再按需修改：

```json
{
  "yamibo": {
    "cookie_file": ".cookie",
    "novel_author_only_max_pages": 50,
    "novel_author_only_page_delay_seconds": 0.5,
    "image_backfill_enabled": false,
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

- `image_backfill_enabled`：是否启用自动回填；新版本部署后先保持 `false`，完成 PostgreSQL dry-run canary 后再开启
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

本地空配置默认使用 SQLite，开箱即可启动；Docker 和生产部署推荐 PostgreSQL（`pgvector`）。通过 `database.backend` 配置切换；选择 PostgreSQL 时必须同时提供 `database.url` 或 `YAMIBO_DB_URL`。

其中轻小说 TXT 导出目录对应 `YAMIBO_NOVEL_TXT_EXPORT_DIR`，轻小说只看楼主更新检测阈值对应 `YAMIBO_NOVEL_AUTHOR_ONLY_MAX_PAGES` 和 `YAMIBO_NOVEL_AUTHOR_ONLY_PAGE_DELAY_SECONDS`。

### Docker 部署

推荐 Docker 部署时使用 PostgreSQL/pgvector，并将 `data/` 挂载到宿主机；Cookie 放在 `data/cookies/` 下。默认会同时启动 Web UI、后台 daemon 和独立的 MCP SSE 服务：

```bash
cp .env.docker.example .env
cp yamibo.local.example.json data/yamibo.local.json
mkdir -p data data/exports data/novel_exports data/cookies data/backups
docker compose build
docker compose up -d postgres
docker compose run --rm yamibo yamibo-init-db
docker compose up -d yamibo yamibo-mcp
```

注意：以上 `yamibo-init-db` 是显式数据库迁移命令；当前工作区还会在 `yamibo` 启动时
自动检查并应用待处理 schema migration，其中包含 `016_add_jobs_started_at`。仅执行
`git push` 或 `docker compose build` 不会改库，但远端若据此启动新镜像，可能会增加
`jobs.started_at` 列并更新 `alembic_version`。允许改库时，请先完成备份和维护窗口
准备，再按[已批准数据库变更后的发布流程](docs/部署运维指南.md#已批准数据库变更后的发布流程)
执行迁移；不允许改库的发布则继续运行现有镜像，不要启动包含该 revision 的新镜像。

默认 Web 控制台地址：`http://localhost:8765`。
MCP SSE 入口默认地址：`http://localhost:8000/sse`。

1.0 的 Compose 默认只将 Web、MCP 和 PostgreSQL 绑定到 `127.0.0.1`。Web/MCP 尚未内置互联网身份认证；远程访问必须放在 TLS 反向代理、VPN 或 identity-aware proxy 后面，不能直接开放端口。

对话页支持内置 Pydantic AI 和外部 Hermes 两种后端，验证期间默认仍为 `hermes`。设置 `YAMIBO_CHAT_BACKEND=embedded` 并配置现有 `YAMIBO_LLM_*` 即可启用内置业务助手，保存后须重启 Web 宿主。完整认证、预算、持久化和回退步骤见 [内置 Agent 实现与配置](docs/内置Agent实现与配置.md)。

以下为 Hermes 回退配置；它调用 Yamibo MCP 的连接仍需在外部 Hermes 运行时单独配置：

```env
YAMIBO_LLM_BASE_URL=http://hermes:8000/v1
YAMIBO_LLM_API_KEY=dummy
YAMIBO_LLM_MODEL=hermes
```

如果 Hermes 运行在宿主机，改用 `http://host.docker.internal:8000/v1`。更完整的 data 外挂、Hermes Docker 网络、PostgreSQL 备份恢复和迁移说明见 [`docs/部署运维指南.md`](docs/部署运维指南.md)。

## 运行期行为

- **语义远端结果**：明确的帖子删除/权限提示不会被代理轮换掩盖；“本帖已经删除，错误权限代码255”直接终止为 THREAD_DELETED，只有传输失败或没有语义提示的未知反爬页进入重试。
- **论坛维护暂停**：当 Daemon 识别到维护页时，会把所有远程任务统一切到 `paused`，并每 10 分钟做一次轻量探测；维护结束后自动恢复。
- **软封锁恢复**：遇到 HTTP 444、429 或未知反爬页时，会优先清理代理缓存并把任务转入重试，而不是直接失败。
- **闲时图片回填**：当队列空闲且达到预算条件时，Daemon 会在跨 worker single-flight 锁内扫描最多 200 个 TID，并自动创建 `image_backfill` 任务；Web「任务」页可查看当日入队计数和最近一次入队原因。
- **交互式单图补取**：本地阅读预览中的缺图卡片可创建 selected `image_backfill` Job。同一图片的重复点击会复用活动 Job；内站附件按配置重试，第三方外链只做一次快速尝试。

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

如果你的 LLM 客户端支持 URL 形式的 MCP 连接，Docker 部署时建议直接连到暴露出来的 SSE 入口：

```json
{
  "mcpServers": {
    "yamibo": {
      "transport": "sse",
      "url": "http://localhost:8000/sse"
    }
  }
}
```

如果客户端不在宿主机，而是在另一台机器上，把 `localhost` 换成 Docker 宿主机的地址，例如 `http://192.168.1.10:8000/sse`。

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
uv run yamibo-archiver read-system-status

# 读取帖子摘要（紧凑 JSON，适合 Agent）
uv run yamibo-archiver read-resource "yamibo://threads/572313/summary"

# 读取帖子诊断（缺失资产、建议操作）
uv run yamibo-archiver read-resource "yamibo://threads/572313/diagnostics"

# 读取轻小说更新检测结果
uv run yamibo-archiver read-resource "yamibo://threads/544422/update-check"

# 数据库备份
uv run yamibo-backup-db
```

PostgreSQL 隔离恢复、`data/` 文件快照和大规模归档抽样验证见 [部署运维指南](docs/部署运维指南.md#postgresql-隔离恢复演练推荐)。首次演练不要覆盖生产数据库。

历史 file-only 归档说明：

- 一部分 2011–2012 老帖可能在 `data/threads/<tid>/` 下已有 `metadata.json + context.md`，但当前数据库里没有对应 `threads` 记录
- 这类帖子优先建议走远端 `sync_thread` 重同步，让系统自动补齐 `forum_id`、`title_parse`、`floors`、`local_reply_count` 和新版 `context.md`
- [scripts/enqueue_missing_db_thread_sync.py](scripts/enqueue_missing_db_thread_sync.py) 会自动扫描这类缺失 tid 并按批次创建同步任务
- 在这批帖子处理完成前，不要执行 `cleanup-orphan-thread-dirs`，否则可能误删这些历史归档目录

## 项目结构

```
src/yamibo_mcp/
├── server/          # MCP 注册、CLI、资源处理适配
├── application/     # Agent-facing commands/queries
├── daemon/          # 后台任务消费 + 处理器
├── web_fastapi/     # FastAPI 嵌入式 Web API / 静态资源服务（主路径）
├── web/             # 随 Python 包分发的已打包静态资源
├── yamibo/          # 论坛 HTTP 客户端、HTML 解析器、标题解析
├── storage/         # 文件 I/O（staging、归档、导出、图片）
├── db/              # PostgreSQL schema、迁移、Repository（含少量 SQLite 兼容层）
│   └── repositories/  # jobs, threads, series, content_blocks, assets, job_events
├── services/        # LLM 客户端、标题提示词
├── domain/          # 领域模型、枚举、校验、内容类型
└── maintenance/     # 备份、清理、重置命令

c/                   # React + Vite 前端源码，构建产物输出到 src/yamibo_mcp/web/static/
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
| [产品需求文档](docs/产品需求文档.md) | PRD + 系统架构 |
| [智能体架构导航](docs/智能体架构导航.md) | 给 AI 编码代理的目录职责、修改路径、简化约束和测试矩阵 |
| [API 接口参考](docs/API接口参考.md) | MCP 工具/资源、CLI、Web 路由 |
| [智能体接口说明](docs/智能体接口说明.md) | Agent-facing 工具、错误契约、推荐工作流 |
| [讨论趋势 V1](docs/讨论趋势V1.md) | PostgreSQL-only 趋势索引、evidence 检索、trend/report artifact 与已知约束 |
| [智能体验收标准](docs/智能体验收标准.md) | OpenClaw/Hermes 类 Agent 的验收场景、评分维度与证据要求 |
| [数据库设计](docs/数据库设计.md) | 表结构、文件存储格式、PostgreSQL JSONB 决策 |
| [账号池设计](docs/账号池设计.md) | 多账号权限分配与 Cookie 管理 |
| [SQLite-Vec RAG 设计](docs/历史/SQLite-VecRAG设计.md) | 历史 SQLite-first RAG 设计，保留作演化参考 |
| [开发指南](docs/开发指南.md) | 标题解析、Job 系统、配置 |
| [FastAPI 迁移方案](docs/历史/FastAPI迁移方案.md) | Web 层迁移决策、构建自动化与路由收敛背景 |
| [部署运维指南](docs/部署运维指南.md) | 安装、配置、运维操作 |
| [用户手册](docs/用户手册.md) | MCP/Web/CLI 使用方式 |
| [测试策略](docs/测试策略.md) | 测试原则、规范、数据与回归策略 |
| [更新日志](docs/更新日志.md) | 版本演进与本轮变更摘要 |
| [Web 界面设计](docs/Web界面设计.md) | 功能点、主题系统、组件设计、API 端点 |
