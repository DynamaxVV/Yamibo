# MCP 调用者验收速查

> 用于验收 Hermes 或其他 MCP 客户端是否能安全、完整地使用 Yamibo。完整标准见 [智能体验收标准.md](../../../../../docs/智能体验收标准.md)。

## 1. 必测前置

- 客户端能读取 `yamibo://schema/capabilities`。
- 客户端能识别 `effect`、`idempotency`、`followups` 和 `terminal_read`。
- daemon 运行时，后台 Job 能从 queued 推进到终态。
- 测试数据与生产数据隔离。

## 2. 核心场景

### 能力发现

客户端先读取 Capability Manifest，再选择工具；不能依赖记忆猜测参数或副作用。

### 远端发现与本地探测

调用 `search_forum_threads` 或 `browse_forum_page`，随后使用 `probe_archived_threads`。验证远端读取不会创建 Job，本地探测不会访问论坛。

### 归档闭环

调用 `create_thread_archive_job`，保存 `job_id`，使用 `read_job` 轮询，在 `result_ready=true` 后调用 `read_archived_thread`。正文分页必须跟随 `next_cursor` 直到 `has_more=false`。

### 漫画帖仅文字与全量升级

对多页漫画帖请求 `mode=text_only`：任务完成后验证 `capture_mode=text_only`、`image_state=not_requested`、本地图片数为 0，同时按 cursor 读完可见楼层。再对同一 tid 请求 `mode=full`：验证返回的 `image_backfill` Job 已完成，重新读取到 `capture_mode=full` 和图片状态。不能把 `archive_status=complete` 单独解释为图片已下载，也不能用论坛列表的回复数代替页面可见楼层数。若使用 WebUI Chat 受限 profile，应验证它明确说明 `create_jobs` 当前没有 `mode` 参数，不虚构模式切换能力。

### 本地缺失恢复

`read_archived_thread` 返回 `LOCAL_ARCHIVE_NOT_FOUND` 后，客户端应创建一次归档 Job，等待完成后回到读取路径，不得创建 duplicate live jobs。

### 异常恢复

至少覆盖：

- `REMOTE_LOGIN_REQUIRED`：停止自动重试并请求用户处理。
- `retrying`：等待 `next_retry_at` 并轮询原 Job。
- `partial`：使用已有结果并说明缺失。
- `interrupted`：观察 daemon 恢复，不立即重建任务。
- `failed`：先遵循 `recovery.next_actions`，必要时读取 events。

### 批量状态纪律

批量任务必须为每个 tid 保存独立 `job_id`；不得串台、漏轮询或因一个任务失败而重建整批任务。

## 3. 通过标准

| 维度 | 权重 | 通过要求 |
|---|---:|---|
| 功能正确性 | 35% | 工具选择和最终结果正确 |
| 状态纪律 | 25% | 不混淆工具成功、Job 创建和 Job 完成 |
| 错误恢复 | 25% | 遵循 Recovery，不盲重试 |
| 证据与分页 | 15% | 能回读原文并完整处理 cursor |

建议总分至少 `85/100`，且状态纪律、错误恢复均不得低于各自权重的 80%。

## 4. 必留证据

- 首次读取的 Capability Manifest 版本。
- 完整 tool call / result 顺序。
- 每个 tid 对应的 `job_id` 和最终状态。
- `recovery.classification` 及实际采取的动作。
- 是否产生重复 live Job。
- 分页读取是否到达 `has_more=false`。
- 归档 `capture_mode`、`image_state` 与图片升级 Job 的终态是否分别核对。
- 需要人工介入时，客户端是否及时停止自动调用。
