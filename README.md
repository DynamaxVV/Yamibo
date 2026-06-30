# 百合会归档助手

百合会 (yamibo.com) 论坛本地归档系统。通过 MCP 协议让 LLM 客户端浏览、搜索、归档、检查更新和导出论坛贴子；内嵌 React WebUI 控制台，支持多主题切换。

> 当前版本：`0.11.1`

## 功能特性

- **MCP Server** — 基于 FastMCP，支持 stdio/SSE/HTTP 传输，LLM 客户端直接调用
- **多分区支持** — 漫画区(30)、轻小说区(55)、动漫区(5)、海域区(33) + 7 个扩展分区，通过 `forum_id` 参数切换
- **智能标题解析** — 规则引擎 + LLM 辅助作为内部归档实现细节
- **自动归档** — 抓取帖子 HTML，解析楼层，下载图片，生成结构化本地存档
- **轻小说更新检测** — 独立 `check_thread_updates` / `update_thread` 流程，轻小说贴子支持只看楼主增量更新
- **内容模型** — 支持 comic/novel/discussion/mixed 四种内容形态，有序内容块 + 资产管理
- **系列管理** — 按 series_key 自动聚合同一系列的多个章节帖子
- **标准化导出** — 漫画/通用贴子 ZIP 打包（context.md + metadata.json + 图片），轻小说导出为可追加的 TXT 文件
- **批量任务** — 支持批量归档和批量 RAG 索引，便于一次性处理多个 tid
- **批量归档探测** — `probe_archived_threads` 可先读取本地归档尾部状态，再配合远端 `last_reply_at` 决定是否补跑
- **Job Event Outbox** — 任务状态变更追加耐久化事件，支持诊断和未来通知
- **本地 RAG 检索** — 基于 FTS5 / PostgreSQL `tsvector` + `pgvector` 的归档内容混合检索，返回可追溯证据片段
- **Agent-Friendly Interface** — 区分远端预览/任务创建与本地归档读取，统一结构化错误和紧凑输出
- **Web 控制台** — React+Vite SPA，中英文双语，多套可切换主题 + 暗黑模式，支持远程论坛实时浏览（`/forum`）
- **CLI** — 所有工具均可通过命令行直接调用

## 快速开始

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
    "novel_author_only_page_delay_seconds": 0.5
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

其中：

- `llm.*` 负责标题解析等 chat/completions 场景
- `rag.*` 负责 `/embeddings` 场景
- 若未单独配置 `rag.base_url` / `rag.api_key`，会自动回退到 `llm.base_url` / `llm.api_key`
- `rag.debug_indexing` 或 `YAMIBO_RAG_DEBUG_INDEXING=1` 可开启 RAG 索引调试日志，daemon 会在终端输出请求与失败上下文

所有配置项均可通过 `YAMIBO_*` 环境变量覆盖。详见 [`.env.example`](.env.example)。

数据库后端默认使用 PostgreSQL（`pgvector`），同时保留 SQLite 支持用于本地开发和单机部署。通过 `database.backend` 配置切换。

其中轻小说 TXT 导出目录对应 `YAMIBO_NOVEL_TXT_EXPORT_DIR`，轻小说只看楼主更新检测阈值对应 `YAMIBO_NOVEL_AUTHOR_ONLY_MAX_PAGES` 和 `YAMIBO_NOVEL_AUTHOR_ONLY_PAGE_DELAY_SECONDS`。

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
yamibo-mcp-server ──创建任务──▶ PostgreSQL / SQLite (jobs + job_events)
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
uv run yamibo-mcp-server stdio --transport sse
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
      "args": ["run", "yamibo-mcp-server", "stdio"],
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
      "args": ["run", "yamibo-mcp-server", "stdio"],
      "cwd": "/path/to/yamibo"
    }
  }
}
```

## CLI 速查

```bash
# 浏览论坛
uv run yamibo-mcp-server browse-forum-page --page 1

# 按发帖时间排序浏览
uv run yamibo-mcp-server browse-forum-page --page 1 --order dateline

# 浏览轻小说区
uv run yamibo-mcp-server browse-forum-page --page 1 --forum-id 55

# 搜索帖子
uv run yamibo-mcp-server search-threads --query "星灵感应"

# 检查轻小说更新
uv run yamibo-mcp-server check-thread-updates --tid 544422

# 创建轻小说追加更新任务
uv run yamibo-mcp-server update-thread --tid 544422

# 批量创建归档任务
uv run yamibo-mcp-server create-sync-thread-batch-jobs --tid 572313 --tid 572314

# 批量同步
uv run yamibo-mcp-server create-sync-forum-range-jobs --start-page 1 --end-page 5

# 创建导出任务
uv run yamibo-mcp-server create-export-thread-job --tid 572313

# 为本地归档构建 RAG 索引
uv run yamibo-mcp-server create-rag-index-job --tid 572313

# 批量创建 RAG 索引任务
uv run yamibo-mcp-server create-rag-index-batch-jobs --tid 572313 --tid 572314

# 搜索本地归档内容（只读，不抓远端）
uv run yamibo-mcp-server search-archived-content --query "星空 告白" --mode hybrid --top-k 5

# 查看任务状态
uv run yamibo-mcp-server job-status <job_id>

# 读取帖子摘要（紧凑 JSON，适合 Agent）
uv run yamibo-mcp-server read-resource "yamibo://threads/572313/summary"

# 读取帖子诊断（缺失资产、建议操作）
uv run yamibo-mcp-server read-resource "yamibo://threads/572313/diagnostics"

# 读取轻小说更新检测结果
uv run yamibo-mcp-server read-resource "yamibo://threads/544422/update-check"

# 数据库备份
uv run yamibo-backup-db
```

## 项目结构

```
src/yamibo_mcp/
├── server/          # MCP 注册、CLI、资源处理适配
├── application/     # Agent-facing commands/queries
├── daemon/          # 后台任务消费 + 处理器
├── web/             # 嵌入式 Web 控制台
├── yamibo/          # 论坛 HTTP 客户端、HTML 解析器、标题解析
├── storage/         # 文件 I/O（staging、归档、导出、图片）
├── db/              # SQLite schema、迁移、Repository
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
| [Agent 能力验收标准](docs/agent-evaluation.md) | OpenClaw/Hermes 类 Agent 的验收场景、评分维度与证据要求 |
| [数据库设计](docs/database-design.md) | 表结构、文件存储格式、PostgreSQL JSONB 决策 |
| [账号池设计](docs/account-pool-design.md) | 多账号权限分配与 Cookie 管理 |
| [SQLite-Vec RAG 设计](docs/rag-sqlite-vec-design.md) | 本地归档检索、chunk、embedding 与 `sqlite-vec` 方案 |
| [核心模块开发说明](docs/development-guide.md) | 标题解析、Job 系统、配置 |
| [部署指南 & 运维手册](docs/deployment-guide.md) | 安装、配置、运维操作 |
| [用户操作手册](docs/user-manual.md) | MCP/Web/CLI 使用方式 |
| [测试方案](docs/testing-strategy.md) | 测试原则、规范、数据与回归策略 |
| [版本更新日志](docs/changelog.md) | 版本演进与本轮变更摘要 |
| [WebUI 设计文档](docs/webui-design.md) | 功能点、主题系统、组件设计、API 端点 |
