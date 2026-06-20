# 百合会归档助手

百合会 (yamibo.com) 论坛本地归档系统。通过 MCP 协议让 LLM 客户端浏览、搜索、归档和导出论坛贴子；内嵌 React WebUI 控制台，支持多主题切换。

## 功能特性

- **MCP Server** — 基于 FastMCP，支持 stdio/SSE/HTTP 传输，LLM 客户端直接调用
- **多分区支持** — 漫画区(30)、轻小说区(55)、动漫区(5)、水区(33)，通过 `forum_id` 参数切换
- **智能标题解析** — 规则引擎 + LLM 辅助，自动提取汉化组、作者、漫画名、章节信息
- **自动归档** — 抓取帖子 HTML，解析楼层，下载图片，生成结构化本地存档
- **内容模型** — 支持 comic/novel/discussion/mixed 四种内容形态，有序内容块 + 资产管理
- **系列管理** — 按 series_key 自动聚合同一系列的多个章节帖子
- **标准化导出** — ZIP 打包（context.md + metadata.json + 图片），按系列分目录
- **Job Event Outbox** — 任务状态变更追加耐久化事件，支持诊断和未来通知
- **Agent-Friendly Resources** — 紧凑 summary、diagnostics、posts、assets 资源，低 token 开销
- **Web 控制台** — 嵌入式 HTTP 管理界面，中英文双语，随 Daemon 自动启动
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

如需 LLM 辅助标题解析，创建 `yamibo.local.json`：

```json
{
  "yamibo": {
    "cookie_file": ".cookie"
  },
  "llm": {
    "base_url": "https://api.openai.com/v1",
    "api_key": "sk-your-key",
    "model": "gpt-4.1-mini"
  }
}
```

所有配置项均可通过 `YAMIBO_*` 环境变量覆盖。详见 [`.env.example`](.env.example)。

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
yamibo-mcp-server ──创建任务──▶ SQLite (jobs + job_events)
    │                               ▲
    │ application layer             │ 轮询 + 抢占
    │ (ensure_thread, archive)      │
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

# 浏览轻小说区
uv run yamibo-mcp-server browse-forum-page --page 1 --forum-id 55

# 搜索帖子
uv run yamibo-mcp-server search-threads --query "星灵感应"

# 获取帖子详情（自动归档）
uv run yamibo-mcp-server get-thread --tid 572313

# 批量同步
uv run yamibo-mcp-server create-sync-forum-range-jobs --start-page 1 --end-page 5

# 创建导出任务
uv run yamibo-mcp-server create-export-thread-job --tid 572313

# 查看任务状态
uv run yamibo-mcp-server job-status <job_id>

# 读取帖子摘要（紧凑 JSON，适合 Agent）
uv run yamibo-mcp-server read-resource "yamibo://threads/572313/summary"

# 读取帖子诊断（缺失资产、建议操作）
uv run yamibo-mcp-server read-resource "yamibo://threads/572313/diagnostics"

# 解析标题
uv run yamibo-mcp-server parse-thread-title "【提灯喵汉化组】[ポテトルス] ray 第13话"

# 数据库备份
uv run yamibo-backup-db
```

## 项目结构

```
src/yamibo_mcp/
├── server/          # MCP Server + CLI 子命令
├── application/     # 用例层（ensure_thread, archive_thread_job, get_job_status）
├── daemon/          # 后台任务消费 + 处理器
├── web/             # 嵌入式 Web 控制台
├── yamibo/          # 论坛 HTTP 客户端、HTML 解析器、标题解析
├── storage/         # 文件 I/O（staging、归档、导出、图片）
├── db/              # SQLite schema、迁移、Repository
│   └── repositories/  # jobs, threads, series, content_blocks, assets, job_events
├── services/        # LLM 客户端、标题提示词
├── domain/          # 领域模型、枚举、校验、内容类型
└── maintenance/     # 备份、清理、重置命令
```

## 测试

```bash
uv run pytest                      # 全部测试
uv run pytest tests/unit/          # 单元测试
uv run pytest tests/unit/test_parsers/  # 解析器测试
uv run pytest -k "test_name"       # 按名称过滤
```

## 文档

| 文档 | 说明 |
|------|------|
| [产品需求 & 架构设计](docs/product-requirements.md) | PRD + 系统架构 |
| [API 接口文档](docs/api-reference.md) | MCP 工具/资源、CLI、Web 路由 |
| [数据库设计](docs/database-design.md) | 表结构、文件存储格式 |
| [核心模块开发说明](docs/development-guide.md) | 标题解析、Job 系统、配置 |
| [部署指南 & 运维手册](docs/deployment-guide.md) | 安装、配置、运维操作 |
| [用户操作手册](docs/user-manual.md) | MCP/Web/CLI 使用方式 |
| [测试方案](docs/testing-strategy.md) | 测试原则、规范、架构 |
| [测试报告](docs/test-report.md) | 测试执行结果 |
| [版本发布说明](docs/release-notes.md) | v0.2.0 功能清单 |
| [WebUI 设计文档](docs/webui-design.md) | 功能点、主题系统、组件设计、API 端点 |
