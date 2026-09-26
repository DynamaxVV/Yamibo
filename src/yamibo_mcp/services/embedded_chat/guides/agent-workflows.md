# MCP 调用者工作流

> 面向 Hermes、Claude Desktop 及其他 MCP 客户端。工具名、Resource URI 和 JSON 字段名保持英文，说明使用中文。

## 1. 开始前

Yamibo MCP 负责提供结构化工具，不负责理解自然语言或自行规划。调用者是唯一规划者，应满足以下运行条件：

- MCP 服务已通过 `yamibo-archiver stdio` 接入客户端。
- PostgreSQL 可连接。
- 创建归档、更新、导出、RAG 或报告任务时，`yamibo-daemon` 必须持续运行。
- 远端论坛读取需要有效 Cookie；RAG 向量能力还需要可用的 embedding 配置。

每个新会话先读取：

1. `yamibo://schema/capabilities`：完整能力契约，优先使用。
2. `yamibo://guide/agent-workflows`：本工作流。
3. 需要时读取 `yamibo://guide/archive-model` 和 `yamibo://guide/error-codes`。

`yamibo://schema/tools` 仅为兼容资源，缺少副作用、幂等性和 Job 终态信息。

## 2. 通用返回包络

所有公开工具返回统一的 `AgentResult`：

| 字段 | 调用者行为 |
|---|---|
| `ok` | `true` 表示调用成功，不等于后台 Job 已完成 |
| `data` | 读取业务数据或 `job_id` |
| `error` | 失败时读取 `code`、`message`、`retryable` 和 `agent_hint` |
| `resources` | 大文本或可继续读取的 Resource URI |
| `next_actions` | 工具层给出的候选后续动作 |
| `warnings` | 向最终用户披露重要限制 |
| `side_effects` | 核对本次调用实际产生的写入或远端访问 |

不要根据自然语言错误文本猜测恢复动作；Job 创建后以 `read_job.data.recovery` 为准。

## 3. 选择工具

调用前检查 Capability Manifest 中的字段：

- `effect=read_only`：只读本地数据库或文件，可安全重试。
- `effect=remote_read`：访问论坛，有网络成本和反爬风险。
- `effect=enqueue_job`：只创建后台 Job，不同步完成业务工作。
- `idempotency.mode=deduplicated_by_active_job`：重复请求通常复用 live Job。
- `idempotency.mode=not_deduplicated`：重复调用会创建新任务，必须由调用者避免重复。
- `terminal_read`：告诉调用者从哪里取得 `job_id`、使用哪个状态工具以及完成字段。

公共 MCP 面不提供 destructive 工具。

## 4. 常用工作流

### 4.1 搜索并归档帖子

1. `search_forum_threads(query=...)` 或 `browse_forum_page(...)`。
2. 使用 `inspect_remote_thread(tid=...)` 确认目标。
3. 使用 `probe_archived_threads(tids=[...])` 检查本地状态。
4. 需要归档时调用 `create_thread_archive_job(tid=...)`。
5. 保存返回的 `job_id`，按第 5 节轮询同一个 Job。
6. `result_ready=true` 后调用 `read_archived_thread(tid=..., view="summary")`。
7. 正文较长时使用 `view="content"`，持续跟随 `next_cursor`，直到 `has_more=false`。

### 4.2 已知 tid 的可靠读取

1. 调用 `read_archived_thread(tid=..., view="summary")`。
2. 如果返回 `LOCAL_ARCHIVE_NOT_FOUND`，调用 `create_thread_archive_job`。
3. 等待原 Job 完成后重新读取，不要在轮询期间创建 duplicate live jobs。

也可以使用 `ensure_thread_archived` 合并“检查本地状态并按需创建 Job”，但仍需读取返回的 `job_id` 并等待完成。

### 4.2.1 漫画帖仅文字归档与全量升级

1. 用户要先分析漫画帖文字时，调用 `create_thread_archive_job(tid=..., forum_id=30, mode="text_only")`；批量目标可用 `create_thread_archive_batch_jobs(tids=[...], forum_id=30, mode="text_only")`。默认 `mode="full"` 会下载图片。
2. 若返回 `job_id`，按第 5 节等待任务结果；`status=satisfied` 且 `job_id=null` 表示现有归档已满足本次请求，可直接读取。不要把 Job 创建成功当作归档完成。
3. `read_archived_thread(view="summary")` 应显示 `capture_mode=text_only`、`image_state=not_requested`；`archive_status=complete` 此时表示文字已抓完，不表示有本地图片。通过 `view="content"` 按 `next_cursor` 读到 `has_more=false`；`assets` 保留图片引用。
4. 后续需要全量图片时，对同一 tid 请求 `create_thread_archive_job(mode="full")`。已完成的仅文字归档会进入 `image_backfill` Job；等待其终态，再确认 `capture_mode=full`、`image_state=complete`。如为 `partial`，按诊断和恢复建议处理。
5. 仅文字归档不可直接导出或单图补取。远端列表的回复数与当前可见楼层数可能不同；判断文字是否抓全应核对实际页数与可见楼层，不能只比较列表回复数。

### 4.3 检查并更新归档

1. `check_thread_updates(tid=...)` 读取远端变化，不创建 Job。
2. 确认需要更新后调用 `create_thread_update_job(tid=...)`。
3. 等待 Job 的 `result_ready=true`。
4. 重新调用 `read_archived_thread`。

### 4.4 批量归档

1. 先调用 `probe_archived_threads(tids=[...])`。
2. 结合远端 `last_reply_at` 和本地 `local_last_reply_at` 筛选目标。
3. 调用 `create_thread_archive_batch_jobs(tids=[...])`。
4. 分别保存并轮询每个非空 `job_id`，不得把不同 tid 的状态混在一起；`jobs` 中 `status=satisfied` 的项可直接读取，不需要轮询。

### 4.5 本地检索和证据读取

1. 调用 `search_archived_content(query=..., mode="hybrid", top_k=...)`。
2. 使用结果中的 `tid`、`pid`、楼层和 Resource URI 回读原文。
3. 最终回答应区分检索结果与原始归档事实。

### 4.6 Discussion Trend

该能力仅支持 PostgreSQL。建议顺序：

1. `create_discussion_trend_index_job`
2. `read_job` 或 `wait_for_job`
3. `get_discussion_partition_trends` / `get_discussion_topic_trends` / `get_discussion_user_trends`
4. `get_discussion_topic_evidence` 或 `get_forum_evidence_pack`
5. 按需创建报告 Job，再用 `get_discussion_report` 读取结果

趋势索引和报告 Job 的幂等模式为 `not_deduplicated`，不要自动重复提交。

## 5. Job 轮询与恢复

创建 Job 后：

1. 保存原 `job_id`。
2. 优先调用 `read_job(job_id=...)`；需要阻塞等待时使用 `wait_for_job`。
3. 按 `recommended_poll_after_seconds` 或 `recovery.poll_after_seconds` 控制频率。
4. `result_ready=false` 时不要读取最终产物，也不要创建重复 Job。
5. `result_ready=true` 时按创建工具的 `terminal_read` 和 `followups` 读取结果。
6. 仅在 `partial`、`failed`、长期无进展或 recovery 要求时调用 `read_job_events`。

| `recovery.classification` | 调用者动作 |
|---|---|
| `completed` | 读取业务结果 |
| `use_partial_result` | 使用已有结果，向用户说明缺失项 |
| `continue_waiting` | 按建议延迟轮询原 Job |
| `automatic_retry` | 等待 `next_retry_at`，不要创建新 Job |
| `automatic_recovery` | 等待 daemon 恢复远端访问 |
| `user_action_required` | 停止自动调用并向用户说明所需操作 |
| `inspect_failure` | 读取 Job events 后再决定是否新建任务 |
| `stopped` | 停止轮询；原 Job 不应继续重试 |

## 6. 分页与大文本

- 工具适合控制流和结构化结果。
- Resource URI 适合归档正文、诊断、事件和导出等大内容。
- 出现 `has_more=true` 时必须携带返回的 `next_cursor` 继续读取。
- 不要自行构造 cursor，也不要默认一次读取完整长帖。

## 7. 本地排障补充

MCP 调用失败且需要人工检查时，可在 Yamibo 主机运行：

```bash
uv run yamibo-archiver job-status <job_id>
uv run yamibo-archiver read-job-events <job_id>
```

这些 CLI 命令是运维补充，不是 MCP 调用者的首选通道。
