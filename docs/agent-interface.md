# Agent 接口说明

> 版本：0.7.1 | 更新日期：2026-06-22

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

推荐读取顺序：

1. 先读 `yamibo://guide/agent-workflows`，确定当前意图属于远端预览、本地归档读取还是长任务创建
2. 再读 `yamibo://guide/archive-model`，确认“远端只读 / 本地只读 / daemon 执行”的边界
3. 发生失败时读 `yamibo://guide/error-codes`
4. 需要动态确认 tool 参数时读 `yamibo://schema/tools`

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

## 任务状态机

长任务通过 SQLite `jobs` 表落地，由 daemon 消费；Agent 不直接执行归档、更新或导出本体，只负责创建任务和回读结果。

| 状态 | 含义 | Agent 建议动作 |
|------|------|----------------|
| `queued` | 任务已创建，等待 daemon 抢占 | 继续 `read_job` 轮询，不要重复创建同类任务 |
| `running` | daemon 已抢占，正在执行 | 继续 `read_job`；需要排障时加读 `read_job_events` |
| `retrying` | 任务进入内部重试流程 | 保持轮询，避免并行创建第二个同类任务 |
| `succeeded` | 任务成功完成 | 立即切换到 `read_archived_thread` 或导出读取 |
| `partial` | 主体成功，但有缺图或部分资产失败 | 允许读取本地归档，同时结合 `diagnostics` 和 `read_job_events` 判断是否需要补救 |
| `failed` | 任务失败 | 先读 `read_job_events` 和错误码，再决定是否重建任务 |
| `interrupted` | worker 中断，可被 daemon 恢复 | 先观察是否回到 `running`；长期停留再人工处理 |
| `cancelled` | 任务已取消 | 停止轮询，必要时重新创建新任务 |

`read_job` 额外暴露以下运行态诊断字段，用于长时间 `running` 或 `queued` 的自动判定：

- `running_duration_seconds`
- `seconds_since_update`
- `execution_state`
  - `queued`：正常排队
  - `normal`：正常推进
  - `attention`：还在推进或等待恢复，但已经值得关注
  - `stalled`：长时间没有进度更新，应进入排障面
  - `terminal`：已终态
- `diagnostic_summary`
- `needs_attention`

推荐约定：

- `execution_state=normal`：继续按 `recommended_poll_after_seconds` 轮询
- `execution_state=attention`：允许继续轮询，但应准备切到 `read_job_events`
- `execution_state=stalled`：优先读 `read_job_events`，检查 daemon / 网络 / 图片下载阶段

## 任务轮询与恢复建议

推荐顺序：

1. 创建任务：`create_thread_archive_job` / `create_thread_update_job` / `create_thread_export_job`
2. 轮询主状态：`read_job(job_id)`
3. 出现 `failed`、`partial`、`execution_state in {attention, stalled}` 或 `interrupted` 时，再读 `read_job_events(job_id)`
4. 状态进入 `succeeded` 或 `partial` 后，切到 `read_archived_thread`

约定：

- `read_job` 是主入口，适合低成本轮询
- `read_job_events` 是排障入口，适合阅读事件时间线、阶段切换、错误上下文
- 不要在 `queued` 或 `retrying` 时盲目重复创建同一帖子的新任务
- `partial` 不等于不可读；它表示主归档通常已经落地，但部分资产或附加步骤不完整

## 工作流资源如何配合任务

典型配合关系如下：

- `yamibo://guide/agent-workflows`：告诉 Agent 该先搜索、先预览，还是先建任务
- `yamibo://guide/archive-model`：告诉 Agent 哪些调用只读，哪些调用只会“写 job 不执行”
- `read_job` / `read_job_events`：读取任务生命周期
- `yamibo://jobs/{job_id}/events`：把任务事件时间线作为只读资源暴露给不方便继续调 tool 的客户端
- `yamibo://threads/{tid}/summary` / `diagnostics` / `posts` / `context`：在任务完成后继续读取归档结果

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
