# MCP 归档数据模型

> 帮助调用者区分远端事实、本地归档和后台 Job，避免把“已创建任务”误认为“已获得结果”。

## 1. 三类能力

| 能力层 | 代表工具 | 是否访问论坛 | 是否创建 Job |
|---|---|---:|---:|
| 远端只读 | `search_forum_threads`、`browse_forum_page`、`inspect_remote_thread`、`check_thread_updates` | 是 | 否 |
| 本地只读 | `read_archived_thread`、`probe_archived_threads`、`search_archived_content`、`read_job` | 否 | 否 |
| 后台任务 | `create_thread_archive_job`、`create_thread_update_job`、`create_thread_export_job`、RAG/报告创建工具 | 视任务而定 | 是 |

远端只读结果是“论坛当前可见状态”；本地只读结果是“已经持久化的归档状态”。两者可能存在时间差，调用者必须明确说明来源。

## 2. 远端只读

远端工具不会写入帖子、楼层、内容块或图片，也不会替调用者创建归档任务。它们可能读取本地数据库，用于补充 `is_archived` 等提示。

远端访问受 Cookie、权限、维护时段、限流和反爬暂停影响。错误时先读取结构化错误码，不要连续重试。

## 3. 本地归档读取

`read_archived_thread` 是主要读取工具：

- `view="summary"`：帖子级摘要，适合第一步读取。
- `view="content"`：分页正文；检查 `has_more` 并跟随 `next_cursor`。
- `view="diagnostics"`：缺图、部分归档和处理诊断。
- 其他视图以 `yamibo://schema/capabilities` 的输入 schema 为准。

本地不存在时返回 `LOCAL_ARCHIVE_NOT_FOUND`。这不是远端帖子不存在；调用者可以在确认副作用后创建归档 Job。

`capture_mode` 描述归档范围，`archive_status` 描述任务结果，两者要一起看：

| `capture_mode` | `image_state` | 解释 |
|---|---|---|
| `text_only` | `not_requested` | 楼层文字与图片引用已保存；本地图片有意未下载，即使 `archive_status=complete` 也不能当作完整图片归档 |
| `full` | `complete` | 资产记录显示图片引用均已下载；如需验证文件完整性仍应检查实际文件或诊断结果 |
| `full` | `partial` | 图片仍有缺失；读取 `diagnostics` 与 Job 恢复建议 |

这些字段可从 `read_archived_thread` 获取。长帖的文字应使用 `view="content"` 按返回的 cursor 读完；仅文字归档不能直接导出或单图补取。

## 4. 后台 Job

`enqueue_job` 工具只向数据库写入队列记录。真正的抓取、更新、导出、索引或报告由 daemon 执行。

调用者必须：

1. 从返回数据保存单个或批量 `job_id`。
2. 使用 `read_job` 轮询原 Job。
3. 仅在 `data.result_ready=true` 后读取最终结果。
4. 遵循 `data.recovery`，避免 duplicate live jobs。

`read_job_events` 是排障面，不是常规高频轮询面。

归档创建工具支持 `mode=text_only|full`，默认 `full`。对已完成的仅文字归档请求 `full` 会创建 `image_backfill` Job；返回 `job_id` 时仍须等待其终态。返回 `status=satisfied`、`job_id=null` 表示当前归档已经满足所请求的模式。

## 5. 批量归档

批量操作前调用 `probe_archived_threads`。该工具只读取本地事实，不访问论坛、不创建 Job。

推荐把以下字段与远端列表结果比较：

- `is_archived`
- `local_last_reply_at`
- `local_floor_count`
- 远端 `last_reply_at`

只对缺失或落后的 tid 调用 `create_thread_archive_batch_jobs`，并为每个 tid 单独维护 `job_id`。

## 6. Resource URI

工具返回的 `resources` 字段是继续读取的依据。常见 URI 包括：

- `yamibo://threads/<tid>/summary`
- `yamibo://threads/<tid>/diagnostics`
- `yamibo://jobs/<job_id>/status`
- `yamibo://jobs/<job_id>/events`

Resource 是读取面，不会隐式创建 Job。正文分页仍以工具返回的 `has_more` / `next_cursor` 为准。
