# Agent 接口说明

> 版本：0.12.1 | 更新日期：2026-07-05

## 概览

CLI 是主要操作方式，所有功能均可通过 `uv run yamibo-archiver <command>` 直接调用。MCP 工具是为 LLM 客户端提供的辅助通道，底层与 CLI 共享同一 application 层。

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

所有工具均有对应的 CLI 命令。**推荐优先使用 CLI**。

| MCP 工具 | CLI 命令 |
|---|---|
| `browse_forum_page` | `uv run yamibo-archiver browse-forum-page --page 1` |
| `search_forum_threads` | `uv run yamibo-archiver search-threads --query "..."` |
| `inspect_remote_thread` | `uv run yamibo-archiver inspect-remote-thread --tid <tid>` |
| `create_thread_archive_job` | `uv run yamibo-archiver create-thread-archive-job --tid <tid>` |
| `create_thread_archive_batch_jobs` | `uv run yamibo-archiver create-sync-thread-batch-jobs --tid ... --tid ...` |
| `check_thread_updates` | `uv run yamibo-archiver check-thread-updates --tid <tid>` |
| `create_thread_update_job` | `uv run yamibo-archiver update-thread --tid <tid>` |
| `create_thread_export_job` | `uv run yamibo-archiver create-export-thread-job --tid <tid>` |
| `create_rag_index_job` | `uv run yamibo-archiver create-rag-index-job --tid <tid>` |
| `create_rag_index_batch_jobs` | `uv run yamibo-archiver create-rag-index-batch-jobs --tid ... --tid ...` |
| `search_archived_content` | `uv run yamibo-archiver search-archived-content --query "..." --mode hybrid` |
| `read_job` / `wait_for_job` | `uv run yamibo-archiver job-status <job_id>` |
| `read_job_events` | (通过 Web 控制台或 API) |
| `read_archived_thread` | `uv run yamibo-archiver read-resource "yamibo://threads/<tid>/summary"` |
| `probe_archived_threads` | `uv run yamibo-archiver probe-archived-threads --tid ... --tid ...` |
| `create_discussion_trend_index_job` | `uv run yamibo-archiver create-discussion-trend-index-job --forum-id 5 --start-date 2014-11-01 --end-date 2014-11-30` |
| `get_discussion_partition_trends` | `uv run yamibo-archiver discussion-partition-trends --forum-id 5 --start-date 2014-11-01 --end-date 2014-11-30` |
| `get_discussion_topic_trends` | `uv run yamibo-archiver discussion-topic-trends --forum-id 5 --start-date 2014-11-01 --end-date 2014-11-30` |
| `get_discussion_user_trends` | `uv run yamibo-archiver discussion-user-trends --forum-id 5 --start-date 2014-11-01 --end-date 2014-11-30` |
| `get_discussion_topic_evidence` | `uv run yamibo-archiver discussion-topic-evidence --forum-id 5 --start-date 2014-11-01 --end-date 2014-11-30 --topic-label 百合动画 --mode auto` |
| `get_forum_evidence_pack` | `uv run yamibo-archiver forum-evidence-pack --forum-id 33 --query 黑话 --intent slang_usage --mode auto` |
| `create_discussion_trend_report_job` | `uv run yamibo-archiver create-discussion-trend-report-job --forum-id 5 --start-date 2014-11-01 --end-date 2014-11-30 --period monthly` |
| `create_forum_research_report_job` | `uv run yamibo-archiver create-forum-research-report-job --forum-id 33 --start-date 2014-11-01 --end-date 2014-11-30 --question "海域区这个时期的讨论氛围如何？" --intent community_atmosphere` |
| `get_discussion_report` | `uv run yamibo-archiver discussion-report --forum-id 5 --start-date 2014-11-01 --end-date 2014-11-30` |

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

`probe_archived_threads(tids)` 是批量本地探测接口。它只返回本地归档事实，不创建任务，不抓远端。返回项包含：

- `archived`
- `archive_status`
- `sync_time`
- `local_floor_count`
- `local_reply_count`
- `local_last_pid`
- `local_last_floor_no`
- `local_last_floor_pub_time`
- `local_last_reply_at`，与 `local_last_floor_pub_time` 同义，便于和远端 `last_reply_at` 对照

大批量归档前，优先用这个接口判断某个 `tid` 是否已经有本地归档，以及本地最后楼层时间是否已经落后于远端列表页的 `last_reply_at`。

## Discussion Trend V1

Discussion Trend V1 为 PostgreSQL-only 能力面，适合历史分区分析、topic 证据抽样和非趋势类论坛研究。建议顺序：

1. `create_discussion_trend_index_job`
2. `read_job` 或 `wait_for_job`
3. `get_discussion_partition_trends` / `get_discussion_topic_trends` / `get_discussion_user_trends`
4. `get_discussion_topic_evidence` 或 `get_forum_evidence_pack`
5. `create_discussion_trend_report_job` 或 `create_forum_research_report_job`
6. `get_discussion_report`

当前行为说明：

- 同一 `(forum_id, start_date, end_date, version)` 允许重复重跑，current pointer 由 `discussion_current_indexes` 负责切换
- `get_discussion_topic_evidence` 的 SQL path 支持 thread-level assignment，不再要求 assignment 必须绑定到单个 `pid`
- `discussion_user_daily.topic_count` 已修正为按真实 bucket/user 聚合，不再因为中间键缺失而恒为 0

详细契约见 [discussion-trend-v1.md](discussion-trend-v1.md)。

## 推荐工作流

所有操作优先使用 CLI。MCP 工具在 LLM 客户端中作为自动化通道使用。

### 搜索并归档

**CLI（推荐）**：
```bash
uv run yamibo-archiver search-threads --query "星灵感应"
uv run yamibo-archiver create-thread-archive-job --tid <tid>
uv run yamibo-archiver job-status <job_id>
uv run yamibo-archiver read-resource "yamibo://threads/<tid>/summary"
```

**MCP**：`search_forum_threads` → `create_thread_archive_job` → `read_job` → `read_archived_thread`

### 已知 tid 读取

```bash
uv run yamibo-archiver read-resource "yamibo://threads/<tid>/summary"
```

### 大批量归档前探测

```bash
uv run yamibo-archiver probe-archived-threads --tid <tid1> --tid <tid2>
# 结合 browse-forum-page 返回的 last_reply_at 判断是否需要补跑
```

### 轻小说更新

```bash
uv run yamibo-archiver check-thread-updates --tid <tid>
uv run yamibo-archiver update-thread --tid <tid>
uv run yamibo-archiver job-status <job_id>
```

## 任务状态机

长任务通过 SQLite `jobs` 表落地，由 daemon 消费；Agent 不直接执行归档、更新或导出本体，只负责创建任务和回读结果。

| 状态 | 含义 | Agent 建议动作 |
|------|------|----------------|
| `queued` | 任务已创建，等待 daemon 抢占 | 继续 `read_job` 轮询，不要重复创建同类任务 |
| `running` | daemon 已抢占，正在执行 | 继续 `read_job`；需要排障时加读 `read_job_events` |
| `retrying` | 任务进入内部重试流程 | 保持轮询，避免并行创建第二个同类任务 |
| `paused` | 任务已暂停，等待恢复 | 先由用户恢复，再继续轮询；不要重复创建 |
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

1. 创建任务：CLI 命令或 MCP 工具
2. 轮询主状态：`uv run yamibo-archiver job-status <job_id>`
3. 出现 `failed`、`partial`、`execution_state in {attention, stalled}` 或 `interrupted` 时，查看 Web 控制台 Job Detail 页或事件日志
4. 状态进入 `succeeded` 或 `partial` 后，切到本地读取

MCP 侧 `read_job` 是主入口，适合低成本轮询。`wait_for_job` 适合脚本和 benchmark。`read_job_events` 是排障入口。

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
