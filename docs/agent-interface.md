# Agent Interface

> 版本：0.7.0 | 更新日期：2026-06-22

## 概览

新的 Agent-facing MCP 接口分成两类：

- 远端只读：发现、搜索、预览、更新检查
- 本地只读：读取 SQLite 和已物化归档

所有公共工具统一返回结构：

```json
{
  "ok": true,
  "data": {},
  "resources": {},
  "next_actions": [],
  "warnings": [],
  "side_effects": []
}
```

失败时返回：

```json
{
  "ok": false,
  "error": {
    "code": "LOCAL_ARCHIVE_NOT_FOUND",
    "message": "Thread 572313 is not archived locally.",
    "agent_hint": "Call create_thread_archive_job or ensure_thread_archived before reading local archive views.",
    "retryable": false
  }
}
```

## 公共工具

- `browse_forum_page`
- `search_forum_threads`
- `inspect_remote_thread`
- `create_thread_archive_job`
- `ensure_thread_archived`
- `read_archived_thread`
- `check_thread_updates`
- `create_thread_update_job`
- `create_thread_export_job`
- `read_job`
- `read_job_events`
- `read_forum_profiles`

## 导航资源

- `yamibo://guide/agent-workflows`
- `yamibo://guide/error-codes`
- `yamibo://guide/archive-model`
- `yamibo://schema/tools`

## 本地读取

`read_archived_thread(tid, view, floor_start=None, floor_end=None, cursor=None, chunk_size=None)`

支持视图：

- `summary`
- `content`
- `assets`
- `diagnostics`
- `export`
- `metadata`

该工具绝不触发远端抓取。

`content` 视图默认按 chunk 返回，响应包含 `has_more`、`next_cursor` 和 `resource_hints`。读取大帖时应在 `has_more=true` 时用同一参数加 `cursor=next_cursor` 继续分页，不要假设一次调用返回全量楼层。

## 推荐工作流

### 搜索并归档

1. `search_forum_threads`
2. `inspect_remote_thread`
3. `create_thread_archive_job`
4. `read_job`
5. `read_archived_thread`

### 已知 tid 读取

1. `ensure_thread_archived`
2. `read_archived_thread`

### 轻小说更新

1. `check_thread_updates`
2. `create_thread_update_job`
3. `read_job`

## 错误码

- `INVALID_ARGUMENT`
- `LOCAL_ARCHIVE_NOT_FOUND`
- `REMOTE_LOGIN_REQUIRED`
- `REMOTE_MAINTENANCE`
- `REMOTE_FETCH_FAILED`
- `UNEXPECTED_REMOTE_PAGE`
- `JOB_NOT_FOUND`
- `EXPORT_PRECHECK_FAILED`
- `INTERNAL_ERROR`
