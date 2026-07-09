# 部署指南 & 运维手册

> 版本：0.12.1 | 更新日期：2026-07-06

## 1. 环境要求

| 依赖 | 版本要求 | 说明 |
|------|---------|------|
| Python | >= 3.11 | 推荐 3.13 |
| uv | 最新版 | Python 包管理器 |
| PostgreSQL | 15+ | 推荐配合 `pgvector` |
| 操作系统 | macOS / Linux | Windows 未测试 |
| 网络 | 可访问 bbs.yamibo.com | 远程归档需要 |

RAG 相关补充：

- 当前主路径为 PostgreSQL + `pgvector`
- 若要生成 embedding，需要配置可用的 RAG API 凭据；未单独配置时会回退到 `YAMIBO_LLM_API_KEY`

数据库说明：

- 当前默认后端是 PostgreSQL，使用 `YAMIBO_DB_BACKEND=postgres` / `YAMIBO_DB_URL=...`
- SQLite 仅保留给历史迁移输入、测试和少量兼容逻辑，不再作为后续部署推荐方案

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

#### Mihomo 代理池（可选）

如需为每个 thread 归档任务绑定独立代理 IP，需自行运行 mihomo 代理（如 Clash Verge），然后在 `yamibo.proxy_pool` 中配置：

```json
{
  "yamibo": {
    "proxy_pool": {
      "enabled": true,
      "controller_url": "http://127.0.0.1:9090",
      "secret": "",
      "proxy_url": "http://127.0.0.1:7890",
      "selector_group": "yamibo",
      "test_url": "https://www.gstatic.com/generate_204",
      "test_timeout_ms": 3000,
      "failure_policy": "fail_open",
      "allowed_patterns": [],
      "denied_patterns": [],
      "max_delay_ms": 0
    }
  }
}
```

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `enabled` | `false` | 开关，默认关闭不影响现有行为 |
| `controller_url` | `""` | mihomo controller REST API 地址 |
| `secret` | `""` | controller auth secret（空则不加 Authorization header） |
| `proxy_url` | `""` | 代理地址，传给 HTTP 请求和图片下载 |
| `selector_group` | `""` | mihomo 中类型为 Selector 的代理组名 |
| `test_url` | `gstatic.com/generate_204` | 节点延迟测试 URL |
| `test_timeout_ms` | `3000` | 延迟测试超时（毫秒） |
| `failure_policy` | `"fail_open"` | mihomo 故障时直连；HTTP 444 反爬始终 fail-closed |
| `allowed_patterns` | `[]` | 正则白名单，非空时节点必须匹配至少一项 |
| `denied_patterns` | `[]` | 正则黑名单，匹配任意一项即排除 |
| `max_delay_ms` | `0` | 延迟上限（毫秒），超过的排除（0 = 不限制） |

验证配置：`uv run yamibo-archiver check-proxy-pool`

#### Cookie 定时刷新

`yamibo.cookie_refresh_interval_hours`（默认 `12`）控制 cookie 文件的定时刷新周期。设为 `0` 关闭。刷新时机：`borrow_yamibo_client()` 创建客户端前检查，到期则删除 cookie 文件，`YamiboClient` 构造时会自动重新登录。

```json
{
  "yamibo": {
    "cookie_refresh_interval_hours": 12
  }
}
```

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

## 3. Docker 部署（推荐）

Docker 部署会启动 PostgreSQL/pgvector 与 Yamibo Daemon。Daemon 同时负责后台任务消费和 Web 控制台。项目运行数据建议通过宿主机 `data/` 目录外挂，PostgreSQL 数据默认使用 Docker named volume，也可以切换为宿主机目录。

### 3.1 准备目录和配置

```bash
cp .env.docker.example .env
mkdir -p data data/exports data/novel_exports data/cookies data/backups
```

Cookie 文件放在 `data/cookies/` 下。容器内路径对应 `/app/data/cookies/`。如果使用多账号池，建议为每个账号显式配置独立 cookie 文件，例如 `/app/data/cookies/primary.cookie`、`/app/data/cookies/secondary.cookie`。

创建或调整 `yamibo.local.json`：

```json
{
  "yamibo": {
    "cookie_file": "/app/data/cookies/primary.cookie",
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
    "dir": "/app/data/exports",
    "novel_txt_dir": "/app/data/novel_exports",
    "novel_txt_include_filtered_notes": false,
    "novel_txt_debug_markers": false
  }
}
```

数据库、端口、LLM/Hermes 和 RAG 建议放在 `.env`，不要写入 `yamibo.local.json`。

### 3.2 data 目录外挂

默认 compose 配置：

```env
YAMIBO_DATA_BIND=./data
```

容器内路径固定为 `/app/data`。常见持久化内容：

| 容器路径 | 宿主机路径 | 说明 |
|----------|------------|------|
| `/app/data/exports` | `./data/exports` | 通用导出 |
| `/app/data/novel_exports` | `./data/novel_exports` | 轻小说 TXT 导出 |
| `/app/data/cookies` | `./data/cookies` | 多账号 cookie |
| `/app/data/backups` | `./data/backups` | 备份输出 |
| `/app/data/staging` | `./data/staging` | 临时归档工作区 |
| `/app/data/title_hints.json` | `./data/title_hints.json` | 标题提示 |

PostgreSQL 默认使用 named volume：

```env
YAMIBO_POSTGRES_DATA=yamibo_postgres_data
```

如果希望数据库文件也外挂到宿主机目录，改为：

```env
YAMIBO_POSTGRES_DATA=./postgres-data
```

首次切换前请停止容器，并确认目标目录为空或已完成数据迁移。

### 3.3 首次启动

```bash
docker compose build
docker compose up -d postgres
docker compose run --rm yamibo yamibo-init-db
docker compose up -d yamibo
```

访问 Web 控制台：

```text
http://localhost:8765
```

健康检查：

```bash
curl http://localhost:8765/api/health
```

### 3.4 外接 Hermes Docker（对话页优先场景）

Web 对话页和标题解析使用 OpenAI-compatible `/v1/chat/completions`。默认 `.env.docker.example` 已按 Hermes 容器名配置：

```env
YAMIBO_LLM_BASE_URL=http://hermes:8000/v1
YAMIBO_LLM_API_KEY=dummy
YAMIBO_LLM_MODEL=hermes
```

Hermes 需要满足：

- 容器内可通过 `http://hermes:8000/v1` 访问
- 支持 `POST /v1/chat/completions`
- 支持非流式响应，返回 `choices[0].message.content`
- 对话模型能够按 Yamibo 对话页提示输出可解析内容

如果 Hermes 在同一个 compose 中，添加类似服务：

```yaml
services:
  hermes:
    image: your-hermes-image
    container_name: hermes
    ports:
      - "8000:8000"
    restart: unless-stopped
```

如果 Hermes 已在另一个 Docker compose 中，建议使用共享网络：

```bash
docker network create llm-net
docker network connect llm-net hermes
```

然后在 `docker-compose.yml` 为 `yamibo` 增加该外部网络，或启动后执行：

```bash
docker network connect llm-net yamibo-app
```

如果 Hermes 运行在宿主机：

```env
YAMIBO_LLM_BASE_URL=http://host.docker.internal:8000/v1
```

compose 已包含 `host.docker.internal:host-gateway`，Linux Docker Engine 下也可使用该地址。

### 3.5 RAG embedding 与 Hermes 的关系

很多 Hermes/OpenAI-compatible chat 服务只支持 `/v1/chat/completions`，不支持 `/v1/embeddings`。这种情况下建议先关闭 RAG：

```env
YAMIBO_RAG_ENABLED=false
```

如果需要 RAG，请把 embedding 单独接到支持 `/v1/embeddings` 的服务：

```env
YAMIBO_RAG_ENABLED=true
YAMIBO_RAG_BASE_URL=https://api.openai.com/v1
YAMIBO_RAG_API_KEY=sk-your-embedding-key
YAMIBO_RAG_EMBEDDING_MODEL=text-embedding-3-small
YAMIBO_RAG_EMBEDDING_DIMENSIONS=512
```

### 3.6 日常运维

查看日志：

```bash
docker compose logs -f yamibo
```

重启：

```bash
docker compose restart yamibo
```

升级镜像和代码后执行数据库迁移：

```bash
docker compose build
docker compose run --rm yamibo yamibo-init-db
docker compose up -d
```

手动进入容器运行 CLI：

```bash
docker compose run --rm yamibo yamibo-archiver browse-forum-page --page 1
```

### 3.7 数据库备份、恢复与迁移

#### PostgreSQL 备份

推荐用 `pg_dump` 从 postgres 容器导出：

```bash
docker compose exec postgres pg_dump -U yamibo -d yamibo -Fc -f /tmp/yamibo.dump
docker compose cp postgres:/tmp/yamibo.dump ./data/backups/yamibo.dump
```

也可以导出 SQL 文本：

```bash
docker compose exec postgres pg_dump -U yamibo -d yamibo -f /tmp/yamibo.sql
docker compose cp postgres:/tmp/yamibo.sql ./data/backups/yamibo.sql
```

#### PostgreSQL 恢复到新库

先停止 Yamibo，避免恢复过程中写入：

```bash
docker compose stop yamibo
```

恢复 custom dump：

```bash
docker compose cp ./data/backups/yamibo.dump postgres:/tmp/yamibo.dump
docker compose exec postgres dropdb -U yamibo --if-exists yamibo
docker compose exec postgres createdb -U yamibo yamibo
docker compose exec postgres pg_restore -U yamibo -d yamibo /tmp/yamibo.dump
docker compose run --rm yamibo yamibo-init-db
docker compose up -d yamibo
```

#### 从本机 PostgreSQL 迁移到 Docker PostgreSQL

在旧环境导出：

```bash
pg_dump "$YAMIBO_DB_URL" -Fc -f ./data/backups/yamibo-local.dump
```

导入 Docker：

```bash
docker compose up -d postgres
docker compose cp ./data/backups/yamibo-local.dump postgres:/tmp/yamibo-local.dump
docker compose exec postgres dropdb -U yamibo --if-exists yamibo
docker compose exec postgres createdb -U yamibo yamibo
docker compose exec postgres pg_restore -U yamibo -d yamibo /tmp/yamibo-local.dump
docker compose run --rm yamibo yamibo-init-db
```

#### 迁移文件归档数据

数据库只保存结构化记录和索引。归档文件、图片、导出、cookie、备份等在 `data/`。迁移机器时需要同时复制：

```bash
rsync -a ./data/ user@new-host:/path/to/Yamibo/data/
rsync -a ./yamibo.local.json user@new-host:/path/to/Yamibo/yamibo.local.json
```

目标机器启动后执行：

```bash
docker compose run --rm yamibo yamibo-init-db
docker compose up -d
```

#### 历史 file-only 归档补回数据库

如果只迁移了 `data/threads/<tid>/metadata.json` / `context.md`，但数据库没有对应记录，按本指南后文“历史 file-only 归档补回数据库”执行 `scripts/enqueue_missing_db_thread_sync.py`，让 daemon 重新同步远端元数据。

---

## 4. 启动服务

系统由两个独立进程组成：

| 进程 | 生命周期 | 说明 |
|------|---------|------|
| MCP Server | 由 LLM 客户端触发，随会话长期运行 | 处理 MCP 请求，创建任务到 PostgreSQL |
| Daemon | 独立后台进程，支持单次或持续运行 | 轮询 PostgreSQL 消费任务，内嵌 Web 控制台 |

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
uv run yamibo-archiver stdio
```

支持的传输模式：

```bash
uv run yamibo-archiver stdio                        # 默认 stdio
uv run yamibo-archiver stdio --transport sse         # SSE
uv run yamibo-archiver stdio --transport streamable-http  # HTTP
```

### 3.3 CLI 直接调用（无需启动服务）

核心 MCP 工具有对应的命令行入口，返回 JSON 结果；长任务命令只创建 job，仍需 daemon 消费：

```bash
uv run yamibo-archiver browse-forum-page --page 1
uv run yamibo-archiver search-threads --query "关键词"
uv run yamibo-archiver inspect-remote-thread --tid 572313
uv run yamibo-archiver create-thread-archive-job --tid 572313
uv run yamibo-archiver job-status <job_id>
uv run yamibo-archiver wait-for-job <job_id>
uv run yamibo-archiver read-resource "yamibo://threads/572313/summary"
```

### 3.4 历史 file-only 归档补回数据库

如果 `data/threads/<tid>/` 下已有 `metadata.json` / `context.md`，但数据库里没有对应 `threads` 记录，建议优先走远端 `sync_thread` 重同步，而不是手工改库。

```bash
# 先看计划，不写入任务
uv run python scripts/enqueue_missing_db_thread_sync.py --dry-run --batch-size 100 --max-batches 2

# 正式按批次创建 sync_thread jobs，并让 daemon 自动补 forum_id / floors / title_parse / local_reply_count
uv run python scripts/enqueue_missing_db_thread_sync.py --batch-size 100 --max-batches 2
```

说明：

- 不需要手动传 `forum_id`，系统会从远端线程页面面包屑/论坛链接自动提取
- 这些 tid 处理完成前，不要运行 `cleanup-orphan-thread-dirs`

---

## 5. LLM 客户端集成

### 4.0 SSE 连接方式

如果你的 LLM 客户端支持通过 URL 连接 MCP 服务，可以使用 SSE 模式：

```bash
uv run yamibo-archiver stdio --transport sse
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
      "args": ["run", "yamibo-archiver", "stdio"],
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
      "args": ["run", "yamibo-archiver", "stdio"],
      "cwd": "/path/to/yamibo"
    }
  }
}
```

---

## 6. 运维操作

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

备份文件格式：PostgreSQL 为 `forum_YYYYMMDD_HHMMSS.pgdump`

### 5.2 历史 SQLite -> PostgreSQL 迁移

ETL 命令：

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

## 7. 监控

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

## 8. 已知约束

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

## 9. 故障排除

| 症状 | 原因 | 解决 |
|------|------|------|
| 任务一直 queued | Daemon 未启动 | `uv run yamibo-daemon` |
| 任务 failed + LoginRequired | Cookie 过期 | 更新 `data/cookies/` 下对应账号的 cookie 文件 |
| 任务 failed + RemoteMaintenance | 论坛维护中 | 等待维护结束（5:30-6:30 UTC+8） |
| 任务 partial | 部分图片下载失败 | 检查 `missing_images_json`，可重新同步 |
| LLM 解析失败 | API Key 未配置或无效 | 检查 `llm.api_key` 配置 |
| Web 控制台无法访问 | 端口被占用 | 修改 `web.port` 配置 |
| RAG 向量索引失败 | SQLite 下 `sqlite-vec` 无法加载，或 PostgreSQL 下 `pgvector` / embedding 配置缺失 | 检查向量扩展、`YAMIBO_LLM_API_KEY` 和 RAG 页面中的失败提示 |
