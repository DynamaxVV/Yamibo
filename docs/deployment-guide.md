# 部署指南 & 运维手册

> 版本：0.9.3 | 更新日期：2026-06-28

## 1. 环境要求

| 依赖 | 版本要求 | 说明 |
|------|---------|------|
| Python | >= 3.11 | 推荐 3.13 |
| uv | 最新版 | Python 包管理器 |
| SQLite | >= 3.35 | 需支持 FTS5 和 `RETURNING` |
| 操作系统 | macOS / Linux | Windows 未测试 |
| 网络 | 可访问 bbs.yamibo.com | 远程归档需要 |

RAG 相关补充：

- 若要启用向量检索，SQLite 开发环境需要能安装并加载 `sqlite-vec`；PostgreSQL 环境需要 `pgvector`
- 若要生成 embedding，需要配置可用的 RAG API 凭据；未单独配置时会回退到 `YAMIBO_LLM_API_KEY`

数据库后端预留配置：

- 默认后端仍是 SQLite，继续使用 `YAMIBO_DB_PATH`
- 预留的 PostgreSQL 开关包括 `YAMIBO_DB_BACKEND`、`YAMIBO_DB_URL`、`YAMIBO_DB_POOL_MIN`、`YAMIBO_DB_POOL_MAX`、`YAMIBO_DB_POOL_TIMEOUT`、`YAMIBO_DB_CONNECT_TIMEOUT`、`YAMIBO_DB_SCHEMA`、`YAMIBO_DB_SSL_MODE` 和 `YAMIBO_DB_SSL_ROOT_CERT`
- 当前阶段这些值只作为配置底座，不改变运行时默认行为

---

## 2. 安装

### 2.1 克隆与安装依赖

```bash
git clone <repo-url> yamibo
cd yamibo

# 安装所有依赖（含开发依赖）
uv sync --extra dev
```

### 2.2 配置

#### 方式一：配置文件

创建 `yamibo.local.json`（不提交到版本控制）：

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

如需多账号池，可在 `yamibo.account_pool` 里补充多个账号。每个账号建议使用独立的 `cookie_file`，并用 `permission_level` 标记阅读权限；推荐按 `0/10/20/...` 这种梯队配置，`0` 是最低权限。系统会优先用低权限账号做普通抓取，只有在权限不足或列表/搜索场景下才切换到更高权限账号。

#### 方式二：环境变量

```bash
export YAMIBO_LLM_API_KEY="sk-your-key"
export YAMIBO_LLM_MODEL="gpt-4.1-mini"
```

或复制 `.env.example` 为 `.env` 并编辑。

如需启用或调整 RAG，可追加：

```bash
export YAMIBO_RAG_ENABLED=true
export YAMIBO_RAG_BASE_URL="https://api.openai.com/v1"
export YAMIBO_RAG_API_KEY="sk-your-rag-key"
export YAMIBO_RAG_EMBEDDING_MODEL="text-embedding-3-small"
export YAMIBO_RAG_EMBEDDING_DIMENSIONS=512
```

说明：

- `YAMIBO_LLM_*` 主要用于标题解析等聊天模型调用
- `YAMIBO_RAG_*` 主要用于 embedding
- 如果不设置 `YAMIBO_RAG_BASE_URL` / `YAMIBO_RAG_API_KEY`，RAG 会自动回退到 `YAMIBO_LLM_BASE_URL` / `YAMIBO_LLM_API_KEY`

### 2.3 论坛 Cookie

在浏览器中登录 bbs.yamibo.com，复制 Cookie 到 `.cookie` 文件：

```
cookie
```

或使用单行格式：`name1=value1; name2=value2`

### 2.4 初始化数据库

```bash
uv run yamibo-init-db
```

---

## 3. 启动服务

系统由两个独立进程组成：

| 进程 | 生命周期 | 说明 |
|------|---------|------|
| MCP Server | 由 LLM 客户端触发，随会话长期运行 | 处理 MCP 请求，创建任务到 SQLite |
| Daemon | 独立后台进程，支持单次或持续运行 | 轮询 SQLite 消费任务，内嵌 Web 控制台 |

### 3.1 启动 Daemon（后台任务消费 + Web 控制台）

```bash
# 持续运行（默认模式，内嵌 Web 控制台）
uv run yamibo-daemon

# 单次执行后退出（测试用）
uv run yamibo-daemon --once

# 指定 Daemon ID
uv run yamibo-daemon --worker-id my-daemon

# 不启动嵌入式 Web 控制台
uv run yamibo-daemon --no-web
```

Daemon 启动后，Web 控制台自动在 `http://127.0.0.1:8765` 提供服务。

### 3.2 MCP Server（由 LLM 客户端自动启动）

MCP Server 不需要手动启动。配置好 LLM 客户端后，客户端会自动调用：

```bash
uv run yamibo-mcp-server stdio
```

支持的传输模式：

```bash
uv run yamibo-mcp-server stdio                        # 默认 stdio
uv run yamibo-mcp-server stdio --transport sse         # SSE
uv run yamibo-mcp-server stdio --transport streamable-http  # HTTP
```

### 3.3 CLI 直接调用（无需启动服务）

所有 MCP 工具也可通过命令行直接调用，返回 JSON 结果：

```bash
uv run yamibo-mcp-server browse-forum-page --page 1
uv run yamibo-mcp-server search-threads --query "关键词"
uv run yamibo-mcp-server create-sync-thread-job --tid 572313
uv run yamibo-mcp-server job-status <job_id>
uv run yamibo-mcp-server read-resource "yamibo://threads/572313/summary"
```

---

## 4. LLM 客户端集成

### 4.0 SSE 连接方式

如果你的 LLM 客户端支持通过 URL 连接 MCP 服务，可以使用 SSE 模式：

```bash
uv run yamibo-mcp-server stdio --transport sse
```

对应的客户端配置一般写成指向 SSE 入口的 URL，例如：

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

SSE 适合常驻服务进程；如果客户端只支持本地命令启动，继续使用下面的 `stdio` 配置即可。

### 4.1 Claude Desktop

在 `claude_desktop_config.json` 中添加：

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

### 4.2 Cursor

在 `.cursor/mcp.json` 中添加：

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

---

## 5. 运维操作

### 5.1 数据库备份

```bash
# 创建带时间戳的备份
uv run yamibo-backup-db

# 指定目标目录
uv run yamibo-backup-db --dest-dir /path/to/backups

# 同时备份 Cookie 文件
uv run yamibo-backup-db --copy-cookie

# 指定保留数量（默认 20）
uv run yamibo-backup-db --keep-count 10
```

备份文件格式：SQLite 为 `forum_YYYYMMDD_HHMMSS.sqlite3`，PostgreSQL 为 `forum_YYYYMMDD_HHMMSS.pgdump`

### 5.2 SQLite -> PostgreSQL 迁移

完整切换步骤见 [docs/postgres-migration-runbook.md](postgres-migration-runbook.md)。常用 ETL 命令：

```bash
uv run python scripts/migrate_sqlite_to_postgres.py \
  --source-db data/forum.db \
  --target-db-url "$YAMIBO_DB_URL" \
  --schema public
```

迁移脚本会导入源表、同步自增序列、重建搜索索引，并执行 `ANALYZE`。

### 5.3 清理过期数据

```bash
# 清理过期 staging 目录和临时导出文件
uv run yamibo-maintenance-cleanup

# 查看将要清理的内容（不实际删除）
uv run yamibo-maintenance-cleanup --dry-run

# 自定义过期时间
uv run yamibo-maintenance-cleanup --staging-older-than-hours 24
```

### 5.3 数据重置

```bash
# 查看将要删除的内容
uv run yamibo-reset-data --dry-run

# 确认删除并重建空数据库
uv run yamibo-reset-data --yes

# 删除但不重建数据库
uv run yamibo-reset-data --yes --no-reinit-db
```

### 5.4 通过 Web 控制台创建任务

Web 控制台（默认 `http://127.0.0.1:8765`）提供以下运维操作：

- **创建同步任务**：输入 tid 或 URL
- **创建导出任务**：选择策略
- **批量同步**：输入页码范围
- **标题复核**：修正解析结果
- **系列管理**：合并、确认系列
- **删除归档**：删除帖子或系列
- **RAG 管理**：查看索引覆盖、创建单贴 `rag_index` 任务、调试本地检索

---

## 6. 监控

### 6.1 Web Dashboard

访问 `http://127.0.0.1:8765/` 查看：

- 帖子总数、系列数、导出数
- 最近任务列表（含状态、错误信息）
- Daemon 心跳状态
- 审计事件

### 6.2 日志

Daemon 和 Server 使用 Python stdlib logging：

```bash
# 日志格式
2026-06-19 12:00:00,000 INFO [yamibo_mcp.worker.runner] Acquired job sync_thread_xxxx (sync_thread)
```

### 6.3 任务排查

当任务卡在某个阶段时，检查 `data/staging/{job_id}/` 目录：

| 文件 | 存在说明 |
|------|---------|
| `snapshot.json` | HTML 解析成功，问题在后续阶段 |
| `failure.json` | 查看 stage、error_type、error_message |
| `title_parse_log.json` | 对比规则解析和 LLM 结果 |

---

## 7. 已知约束

### 7.1 论坛维护窗口

- **时间**：每天 5:30-6:30（UTC+8）
- **影响**：此时访问论坛返回维护页面
- **系统行为**：抛出 `RemoteMaintenanceError`，任务标记为 failed

### 7.2 搜索限流

- **限制**：约 10 秒/次
- **影响**：频繁搜索会被论坛限流
- **系统行为**：远端失败时自动降级到本地 FTS 搜索

### 7.3 Cookie 过期

- Cookie 有效期取决于论坛设置
- 配置 `login.username` 和 `login_password` 可启用自动登录

---

## 8. 故障排除

| 症状 | 原因 | 解决 |
|------|------|------|
| 任务一直 queued | Daemon 未启动 | `uv run yamibo-daemon` |
| 任务 failed + LoginRequired | Cookie 过期 | 更新 `.cookie` 文件 |
| 任务 failed + RemoteMaintenance | 论坛维护中 | 等待维护结束（5:30-6:30 UTC+8） |
| 任务 partial | 部分图片下载失败 | 检查 `missing_images_json`，可重新同步 |
| LLM 解析失败 | API Key 未配置或无效 | 检查 `llm.api_key` 配置 |
| Web 控制台无法访问 | 端口被占用 | 修改 `web.port` 配置 |
| RAG 向量索引失败 | SQLite 下 `sqlite-vec` 无法加载，或 PostgreSQL 下 `pgvector` / embedding 配置缺失 | 检查向量扩展、`YAMIBO_LLM_API_KEY` 和 RAG 页面中的失败提示 |
