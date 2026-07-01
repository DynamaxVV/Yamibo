# Agent Workflows

> 推荐调用顺序与典型工作流。

## 核心原则

CLI 是主要操作方式。所有操作均可通过 `uv run yamibo-archiver <command>` 完成，无需启动 MCP 服务或 daemon。MCP 工具是为 LLM 客户端提供的辅助通道。

## 典型工作流

### 1. 探索论坛

**CLI（推荐）**：
```bash
uv run yamibo-archiver browse-forum-page --page 1
uv run yamibo-archiver browse-forum-page --page 1 --forum-id 55
uv run yamibo-archiver search-threads --query "星灵感应"
```

**MCP**：调用 `browse_forum_page` / `search_forum_threads` 工具。大结果集通过 `next_cursor` 分页。

### 2. 检查更新

**CLI（推荐）**：
```bash
uv run yamibo-archiver check-thread-updates --tid 544422
```

**MCP**：调用 `check_thread_updates` 工具。

### 3. 创建归档任务

**CLI（推荐）**：
```bash
uv run yamibo-archiver create-thread-archive-job --tid 572313
uv run yamibo-archiver create-sync-thread-batch-jobs --tid 572313 --tid 572314
```

**MCP**：调用 `create_thread_archive_job` 工具。

任务创建后由 daemon 异步消费，通过 `job-status` 查看进度：
```bash
uv run yamibo-archiver job-status <job_id>
```

### 4. 读取本地归档

**CLI（推荐）**：
```bash
uv run yamibo-archiver read-resource "yamibo://threads/572313/summary"
uv run yamibo-archiver read-resource "yamibo://threads/572313/diagnostics"
```

**MCP**：调用 `read_archived_thread` 工具或读取资源 URI。`content` 视图支持 `next_cursor` 分页。

### 5. 搜索归档内容

**CLI（推荐）**：
```bash
uv run yamibo-archiver search-archived-content --query "星空 告白" --mode hybrid --top-k 5
```

**MCP**：调用 `search_archived_content` 工具。

### 6. 导出

**CLI（推荐）**：
```bash
uv run yamibo-archiver create-export-thread-job --tid 572313
```

## 任务状态机

长任务不是同步执行的——工具创建 job，daemon 消费 job。

| 状态 | 语义 | 应对 |
|------|------|------|
| `queued` | 已入库，等待 worker | 轮询，勿重复创建 |
| `running` | 正在执行 | 用 `job-status` 查看 |
| `retrying` | 正在内部重试 | 继续等待 |
| `succeeded` | 完成 | 切到本地读取 |
| `partial` | 主体成功，部分失败 | 可读归档，查看 diagnostics |
| `failed` | 失败 | 读错误码和事件，勿无条件重试 |
| `interrupted` | worker 中断 | 等待恢复或人工介入 |

## 推荐调用节奏

1. CLI 命令创建任务，拿到 `job_id`
2. `job-status <job_id>` 轻量轮询
3. 仅 failed / partial / 长时间 running 时才深入事件日志
4. succeeded 后切换到资源读取
