# API 接口文档

> 版本：1.2.0 | 更新日期：2026-08-21

## 1. MCP 工具 (Tools)

MCP Server 通过 FastMCP 暴露以下工具。LLM 客户端通过 MCP 协议调用。

新的 Agent-facing 主接口见 [智能体接口说明.md](智能体接口说明.md)。核心原则：

- 远端工具只做发现、预览、更新检查和创建任务
- 本地工具只读当前数据库与物化归档
- 公共工具统一返回 `ok/data/error/resources/next_actions/warnings/side_effects`
- 公共 Agent 工具不再暴露 `limit`
- `llm_transform_text` 与 `parse_thread_title` 不再属于公共 Agent 接口

配置了 `yamibo.account_pool` 时，远端工具会按场景自动挑选账号：列表/搜索/预览优先使用更高 `permission_level` 的账号，归档和更新则先用低权限账号，遇到“阅读权限高于 xx 才能浏览”时再切到更高权限账号重试。

Discussion Trend V1 是 PostgreSQL-only 能力面，包含：

- `create_discussion_trend_index_job`
- `create_discussion_trend_report_job`
- `create_forum_research_report_job`
- `get_discussion_partition_trends`
- `get_discussion_topic_trends`
- `get_discussion_user_trends`
- `get_discussion_topic_evidence`
- `get_forum_evidence_pack`
- `get_discussion_report`

详细契约、示例与边界见 [讨论趋势V1.md](讨论趋势V1.md)。`discussion-report` 的 CLI 当前默认返回 JSON，不额外暴露 `--format` 参数。

### 1.1 search_forum_threads

统一搜索帖子：优先按论坛页搜索和筛选，再结合本地归档补充详情。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| query | string | 否 | "" | 搜索关键词 |
| forum_id | int | 否 | 30 | 论坛分区 ID（30=漫画, 55=轻小说, 5=动漫, 33=水区） |
| start_page | int | 否 | 1 | 起始页码 |
| end_page | int \| null | 否 | null | 结束页码，null 表示到最后一页 |
| posted_on | string \| null | 否 | null | 按发布日期筛选（YYYY-MM-DD） |
| base_url | string | 否 | "https://bbs.yamibo.com" | 站点根 URL |
| cookie_file | string \| null | 否 | null | Cookie 文件路径 |
| include_sticky | bool | 否 | false | 是否包含置顶帖 |
| include_announcements | bool | 否 | false | 是否包含公告帖 |

**返回**：

```json
{
  "query": "星灵感应",
  "source": "forum | local_fallback",
  "forum_id": 30,
  "count": 10,
  "items": [
    {
      "tid": 572313,
      "url": "https://bbs.yamibo.com/forum.php?mod=viewthread&tid=572313",
      "display_title": "...",
      "raw_title": "...",
      "core_title": "...",
      "chapter_name": "...",
      "series_id": 1,
      "series_key": "...",
      "archive_status": "complete | stale | unknown",
      "validation_status": "valid | unknown",
      "sync_time": "...",
      "export_path": "... | null",
      "category": "...",
      "publisher": "...",
      "posted_at": "...",
      "last_reply_at": "...",
      "reply_count": 10,
      "row_kind": "normal | sticky | search_result | archived",
      "resources": {
        "context": "yamibo://threads/{tid}/context",
        "metadata": "yamibo://threads/{tid}/metadata",
        "export": "yamibo://threads/{tid}/export",
        "summary": "yamibo://threads/{tid}/summary",
        "diagnostics": "yamibo://threads/{tid}/diagnostics",
        "posts": "yamibo://threads/{tid}/posts",
        "assets": "yamibo://threads/{tid}/assets"
      }
    }
  ]
}
```

**注意**：搜索接口限流 10 秒/次。远端失败时自动降级到本地 FTS 搜索。

---

### 1.2 inspect_remote_thread

远端只读预览；不会写数据库、下载图片或创建任务。

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| tid | int | 是 | 帖子 ID |
| forum_id | int \| null | 否 | 论坛分区 ID |
| base_url | string \| null | 否 | 站点根 URL |

**返回**：紧凑快照，包含标题、分区、发布者、楼层数、图片数和少量 preview。

```json
{
  "tid": 572313,
  "title": "...",
  "publisher": "...",
  "publisher_uid": "...",
  "forum_id": 30,
  "category": "漫画区",
  "floor_count": 2,
  "image_url_count": 24,
  "preview": [
    {
      "floor_no": 1,
      "publisher": "...",
      "content_preview": "..."
    }
  ],
  "remote_url": "https://bbs.yamibo.com/forum.php?mod=viewthread&tid=572313"
}
```

---

### 1.3 browse_forum_page

读取论坛某一页的帖子列表；短调用，直接返回该页帖子信息。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| page | int | 是 | - | 页码（正整数） |
| forum_id | int | 否 | 30 | 论坛分区 ID（30=漫画, 55=轻小说, 5=动漫, 33=水区） |
| order | string | 否 | "default" | 排序方式：`default`=最后回复时间，`dateline`=发帖时间。使用 `dateline` 时返回 `total_pages` |
| base_url | string | 否 | "https://bbs.yamibo.com" | 站点根 URL |
| cookie_file | string \| null | 否 | null | Cookie 文件路径 |
| include_sticky | bool | 否 | false | 是否包含置顶帖 |
| include_announcements | bool | 否 | false | 是否包含公告帖 |

---

### 1.4 create_thread_archive_job

创建帖子归档任务；长操作只返回 job_id。

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| html_path | string \| null | 否 | 本地 HTML 文件路径 |
| tid | int \| null | 否 | 帖子 ID |
| url | string \| null | 否 | 帖子 URL |
| base_url | string \| null | 否 | 站点根 URL |

三个参数至少提供一个。返回 `{"job_id": "sync_thread_xxxx"}`。

---

### 1.4.1 create_thread_archive_batch_jobs

为多个帖子批量创建本地归档任务。只写 job queue，不同步执行归档。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| tids | int[] | 是 | - | 要归档的帖子 ID 列表 |
| base_url | string \| null | 否 | null | 站点根 URL |
| forum_id | int \| null | 否 | null | 指定分区 ID |

返回结果包含 `target_count / created_count / reused_count / created_job_ids / reused_job_ids / tids`。

---

### 1.4.2 create_thread_archive_job（兼容摘要）

该接口用于创建单贴归档任务。批量场景优先使用 `create_thread_archive_batch_jobs`，避免重复写入同类任务。

---

### 1.5 ensure_thread_archived

检查本地是否已有归档；若缺失则创建归档任务。

### 1.6 read_archived_thread

读取本地归档视图。支持：

- `summary`
- `content`
- `assets`
- `diagnostics`
- `export`
- `metadata`

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| tid | int | 是 | - | 帖子 ID |
| view | string | 是 | - | `summary/content/assets/diagnostics/export/metadata` |
| floor_start | int \| null | 否 | null | content 视图楼层范围起点 |
| floor_end | int \| null | 否 | null | content 视图楼层范围终点 |
| cursor | string \| null | 否 | null | content 视图分页 cursor，例如 `offset:20` |
| chunk_size | int \| null | 否 | 20 | content 视图每页楼层数，最大 50 |

`content` 视图返回 `has_more`、`next_cursor`、`resource_hints`，大帖应按 cursor 分页读取。

### 1.6.1 probe_archived_threads

批量读取本地归档事实，不创建任务，不抓远端。适合在大批量归档前先判断哪些 `tid` 已经有本地归档，以及本地最后楼层时间是否已经落后于远端列表页。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| tids | int[] | 是 | - | 要探测的帖子 ID 列表 |

**返回**：

```json
{
  "count": 2,
  "items": [
    {
      "tid": 572313,
      "archived": true,
      "archive_status": "complete",
      "sync_time": "2026-06-25T10:11:12+00:00",
      "forum_id": 30,
      "content_kind": "comic",
      "publisher": "author",
      "pub_time": "2026-06-14 12:00",
      "local_floor_count": 7,
      "local_reply_count": 6,
      "local_last_pid": 40852500,
      "local_last_floor_no": 7,
      "local_last_floor_pub_time": "2026-06-16 11:50",
      "local_last_reply_at": "2026-06-16 11:50"
    }
  ]
}
```

`local_last_floor_pub_time` 是本地最后楼层的发布时间；`local_last_reply_at` 是它的语义别名，方便直接和远端列表页的 `last_reply_at` 做更新判断。

### 1.7 create_thread_export_job

创建帖子导出任务。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| tid | int | 是 | - | 帖子 ID |
| strategy | string \| null | 否 | "cache_only" | 导出策略 |

**策略说明**：

| 策略 | 行为 |
|------|------|
| `cache_only` | 仅导出本地已有的归档数据，不触发远程同步 |
| `sync_if_stale` | 若本地归档过旧或缺失，先同步再导出 |
| `force_resync` | 强制重新从远端同步后再导出 |

轻小说贴子导出为 TXT 文件，默认输出到独立的轻小说导出目录；漫画和其他帖子继续导出为 ZIP。

---

### 1.7.1 create_rag_index_job

为本地已归档帖子创建 RAG 索引任务。只写 job queue，不同步执行索引。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| tid | int \| null | 否 | null | 要重建的帖子 ID；当前版本建议显式传入 |
| force | bool | 否 | false | 是否忽略现有 live job 直接新建 |
| embedding_dimensions | int \| null | 否 | null | 覆盖默认 embedding 维度（通常 512） |

返回 `{"job_id": "rag_index_xxxx"}`。

---

### 1.7.1.1 create_rag_index_batch_jobs

为多个已归档帖子批量创建 RAG 索引任务。只写 job queue，不同步执行索引。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| tids | int[] | 是 | - | 要建立索引的帖子 ID 列表 |
| force | bool | 否 | false | 是否忽略现有 live job 直接新建 |
| embedding_dimensions | int \| null | 否 | null | 覆盖默认 embedding 维度（通常 512） |

返回结果包含 `target_count / created_count / reused_count / created_job_ids / reused_job_ids / tids`。

---

### 1.7.2 search_archived_content

搜索本地归档文本内容。只读当前数据库和本地向量索引，不抓远端论坛。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| query | string | 是 | - | 查询文本 |
| mode | string | 否 | "hybrid" | `hybrid / keyword / vector` |
| top_k | int | 否 | 10 | 返回条数 |
| forum_id | int \| null | 否 | null | 按分区过滤 |
| content_kind | string \| null | 否 | null | 按内容类型过滤 |
| tid | int \| null | 否 | null | 限定单帖 |
| series_id | int \| null | 否 | null | 限定系列 |
| floor_start | int \| null | 否 | null | 楼层起点 |
| floor_end | int \| null | 否 | null | 楼层终点 |

返回结果包含 `chunk_id / tid / pid / floor_no / display_title / publisher / pub_time / content_kind / snippet / score / score_parts / source_uri`。

---

### 1.7.3 任务暂停与恢复

Web 控制台和 API 提供任务暂停 / 恢复能力，便于在图片下载或外部资源异常时临时让出并发额度。

| 接口 | 方法 | 说明 |
|------|------|------|
| `/jobs/pause` | POST | 将 `queued` / `running` / `retrying` 任务转为 `paused` |
| `/jobs/resume` | POST | 恢复 `paused` 任务并重新进入队列 |

---

### 1.8 check_thread_updates

检查已归档轻小说贴子是否有新更新；只读，不创建任务。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| tid | int | 是 | - | 帖子 ID |
| base_url | string \| null | 否 | null | 站点根 URL |

返回结果包含：

- `status`：`up_to_date` / `updated` / `unknown` / `not_supported` / `failed`
- `local_snapshot`：本地归档快照
- `remote_snapshot`：远端作者只看楼主快照
- `evidence`：判断依据与字段差异

---

### 1.9 create_thread_update_job

创建轻小说贴子追加更新任务；会先执行更新检测，检测到新内容后再追加归档。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| tid | int | 是 | - | 帖子 ID |
| base_url | string \| null | 否 | null | 站点根 URL |

返回 `{"job_id": "update_thread_xxxx"}`。

---

### 1.10 create-sync-forum-range-jobs

按论坛页码范围抓取真实帖子列表并批量创建同步任务。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| start_page | int | 是 | - | 起始页码 |
| end_page | int | 是 | - | 结束页码 |
| forum_id | int | 否 | 30 | 论坛分区 ID（30=漫画, 55=轻小说, 5=动漫, 33=水区） |
| base_url | string | 否 | "https://bbs.yamibo.com" | 站点根 URL |
| cookie_file | string \| null | 否 | null | Cookie 文件路径 |
| include_sticky | bool | 否 | false | 是否包含置顶帖 |
| include_announcements | bool | 否 | false | 是否包含公告帖 |

---

### 1.11 read_job

读取后台任务状态。

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| job_id | string | 是 | 任务 ID |

**返回**：

```json
{
  "job_id": "sync_thread_xxxx",
  "job_type": "sync_thread",
  "status": "succeeded",
  "stage": "finalize",
  "progress_current": 6,
  "progress_total": 6,
  "worker_id": "worker_xxxx",
  "error_code": null,
  "error_message": null,
  "artifacts": {
    "tid": 572313,
    "context_path": "threads/572313/context.md",
    "metadata_path": "threads/572313/metadata.json",
    "floors": 2,
    "downloaded_image_count": 24,
    "archive_status": "complete"
  },
 "created_at": "...",
  "updated_at": "...",
  "finished_at": "..."
}
```

`status` 还可能取值 `paused`，表示任务已暂停等待恢复。

---

### 1.12 wait_for_job

阻塞等待后台任务到达终态，适合脚本、benchmark 或一次性等待结果的 Agent 流程。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| job_id | string | 是 | - | 任务 ID |
| timeout_seconds | float | 否 | 120 | 最长等待时间 |
| poll_interval_seconds | float | 否 | 2 | 轮询间隔 |
| include_events | bool | 否 | false | 是否把事件流一并带回 |

---

### 1.13 cleanup_job

创建后台清理任务。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| job_id | string \| null | 否 | null | 指定 job 的 staging 目录 |
| mode | string | 否 | "job_staging" | 清理模式 |
| older_than_hours | int \| null | 否 | null | 过期时间阈值 |

**模式**：`job_staging`（清理指定 job 的 staging）、`stale_staging`（清理所有过期 staging）。

---

### 1.14 CLI 对应入口与本地命令

以下命令通过 `yamibo-archiver` 暴露给本地人工操作和脚本。多数是 MCP 工具的 CLI 对应入口；`cleanup-*`、范围同步等命令偏本地运维或批处理：

- `search-threads`
- `inspect-remote-thread`
- `create-thread-archive-job`
- `create-sync-thread-job`（legacy alias，优先使用 `create-thread-archive-job`）
- `create-sync-thread-batch-jobs`
- `probe-archived-threads`
- `ensure-thread-archived`
- `create-export-thread-job`
- `update-thread`
- `job-status`
- `wait-for-job`
- `read-job-events`
- `cleanup-job`
- `create-sync-forum-range-jobs`

---

## 2. MCP 资源 (Resources)

MCP Server 暴露以下只读资源，通过 `yamibo://` URI scheme 访问。

### 2.1 帖子资源

| URI | Content-Type | 说明 |
|-----|-------------|------|
| `yamibo://threads/{tid}/summary` | application/json | 帖子紧凑摘要（不含完整正文） |
| `yamibo://threads/{tid}/diagnostics` | application/json | 帖子诊断信息（归档状态、缺失资产、建议操作） |
| `yamibo://threads/{tid}/posts` | application/json | 帖子内容块列表（text/image/attachment/quote/link） |
| `yamibo://threads/{tid}/assets` | application/json | 帖子资产列表（图片、附件、共享资源） |
| `yamibo://threads/{tid}/update-check` | application/json | 轻小说更新检测结果（只读） |
| `yamibo://threads/{tid}/context` | text/markdown | 帖子正文 Markdown（含 frontmatter） |
| `yamibo://threads/{tid}/metadata` | application/json | 帖子完整元数据 |
| `yamibo://threads/{tid}/export` | application/zip | 帖子导出 ZIP 包 |

### 2.2 系列资源

| URI | Content-Type | 说明 |
|-----|-------------|------|
| `yamibo://series/index` | text/markdown | 系列索引（Markdown 格式） |
| `yamibo://series/{series_id}/chapters` | application/json | 系列章节列表 |

### 2.3 论坛资源

| URI | Content-Type | 说明 |
|-----|-------------|------|
| `yamibo://forums/index` | application/json | 所有论坛分区列表 |
| `yamibo://forums/{forum_id}/summary` | application/json | 单个论坛分区摘要 |

### 2.4 任务资源

| URI | Content-Type | 说明 |
|-----|-------------|------|
| `yamibo://jobs/{job_id}/status` | application/json | 任务主状态快照 |
| `yamibo://jobs/{job_id}/events` | application/json | 任务事件时间线（append-only） |

### 2.5 Guide / Schema 资源

| URI | Content-Type | 说明 |
|-----|-------------|------|
| `yamibo://guide/agent-workflows` | text/markdown | Agent 推荐调用顺序与典型工作流 |
| `yamibo://guide/error-codes` | text/markdown | 公共错误码、修复建议、重试边界 |
| `yamibo://guide/archive-model` | text/markdown | 远端只读 / 本地只读 / 后台任务 三层模型 |
| `yamibo://schema/tools` | application/json | 兼容工具参数签名与描述 |

### 2.6 Agent 工作流

推荐的资源读取顺序：

```
search_forum_threads → items[].resources.summary
  → yamibo://threads/{tid}/summary        (紧凑摘要)
  → yamibo://threads/{tid}/diagnostics    (缺失资产、建议操作)
  → yamibo://threads/{tid}/posts          (内容块，仅需要时)
  → yamibo://threads/{tid}/assets         (资产详情，仅需要时)
  → yamibo://threads/{tid}/context        (完整正文，仅需要时)
```

如果涉及长任务，推荐补充以下顺序：

```text
create_thread_archive_job
  → read_job
  → wait_for_job                 (脚本/回归/一次性等待结果时可用)
  → read_job_events                  (仅在失败、部分成功、长时间运行时)
  → yamibo://threads/{tid}/summary
  → yamibo://threads/{tid}/diagnostics
  → yamibo://threads/{tid}/context
```

---

## 3. 任务状态机说明

### 3.1 状态枚举

`read_job` 返回的 `status` 当前包含以下语义：

| 状态 | 含义 | 后续建议 |
|------|------|----------|
| `queued` | 已创建，等待 daemon 抢占 | 继续轮询 |
| `running` | 已被 worker 抢占，正在执行 | 继续轮询，必要时读取事件 |
| `retrying` | 正在内部重试 | 继续轮询，不要重复建任务 |
| `succeeded` | 已成功完成 | 读取本地归档或导出产物 |
| `partial` | 主体成功，但部分资产或步骤未完成 | 读取 `diagnostics` 与任务事件评估后续补救 |
| `failed` | 已失败结束 | 先检查 `error_code` / `error_message` / 事件流 |
| `interrupted` | worker 中断，可被恢复 | 等待 recovery 或人工判断 |
| `cancelled` | 已取消 | 结束跟踪，需要时新建任务 |

### 3.2 典型轮询流程

1. 调用创建类工具，拿到 `job_id`
2. 使用 `read_job(job_id)` 作为主轮询接口
3. 在以下情况补读 `read_job_events(job_id)`：
   - 状态为 `failed`
   - 状态为 `partial`
   - `running` 持续过久
   - 状态进入 `interrupted`
4. 状态到达 `succeeded` 或 `partial` 后，再读取帖子资源或导出资源

### 3.3 事件时间线用途

`read_job_events` 与 `yamibo://jobs/{job_id}/events` 都读取同一类 append-only 事件流，适合以下场景：

`read_job_events(job_id, since_event_id=...)` 可按事件 ID 增量读取；Web 接口 `/api/jobs/{job_id}/events?since_event_id=<id>` 以 `X-Next-Event-ID` 和 `X-Has-More` 响应头返回下一游标。

- 查看阶段切换，例如 `fetch_remote`、`parse_html`、`download_images`、`finalize`
- 定位失败点，而不只看最终 `error_message`
- 分辨 `partial` 是“正文已落地但缺图”，还是“导出完成但收尾失败”
- 给具备长期记忆或计划能力的 Agent 保留更稳定的排障上下文

---

## 4. CLI 命令

所有 CLI 命令通过 `yamibo-archiver` 入口执行，直接返回 JSON 结果。

```bash
# 浏览论坛列表页
yamibo-archiver browse-forum-page --page 1

# 浏览轻小说区
yamibo-archiver browse-forum-page --page 1 --forum-id 55

# 搜索帖子
yamibo-archiver search-threads --query "星灵感应"

# 创建归档任务
yamibo-archiver create-thread-archive-job --tid 572313

# 批量创建归档任务
yamibo-archiver create-sync-thread-batch-jobs --tid 572313 --tid 572314

# 远端只读预览
yamibo-archiver inspect-remote-thread --tid 572313

# 批量本地探测
yamibo-archiver probe-archived-threads --tid 572313 --tid 572314

# 创建导出任务
yamibo-archiver create-export-thread-job --tid 572313

# 检查轻小说更新
yamibo-archiver check-thread-updates --tid 544422

# 创建轻小说追加更新任务
yamibo-archiver update-thread --tid 544422

# 批量同步
yamibo-archiver create-sync-forum-range-jobs --start-page 1 --end-page 5

# 批量重同步旧的 file-only 归档，并顺便回填 PG
uv run python scripts/enqueue_missing_db_thread_sync.py --dry-run --batch-size 100 --max-batches 2
uv run python scripts/enqueue_missing_db_thread_sync.py --batch-size 100 --max-batches 2

# 任务状态
yamibo-archiver job-status <job_id>
yamibo-archiver wait-for-job <job_id>
yamibo-archiver read-job-events <job_id>

# 列出导出包
yamibo-archiver list-exports

# 读取资源
yamibo-archiver read-resource "yamibo://threads/572313/context"
yamibo-archiver read-resource "yamibo://threads/572313/summary"
yamibo-archiver read-resource "yamibo://threads/572313/diagnostics"
yamibo-archiver read-resource "yamibo://threads/572313/posts"
yamibo-archiver read-resource "yamibo://threads/572313/assets"
yamibo-archiver read-resource "yamibo://threads/544422/update-check"
yamibo-archiver read-resource "yamibo://forums/index"
yamibo-archiver read-resource "yamibo://jobs/<job_id>/events"
```

---

## 5. Web API

Web 控制台基于 HTTP，提供 JSON API 和页面路由。

外部终端的只读运行状态入口是 `GET /api/system/status`；它返回应用版本、数据库探测、带新鲜度的 Worker 心跳、Job 摘要、远端暂停状态和 `not_checked`。健康检查 `GET /api/health` 只验证应用与数据库连接，不能替代系统状态检查。两个接口处理请求时都不执行迁移，但启动包含待处理 revision 的新应用仍可能先应用 schema migration。

### 5.1 页面路由

| 路径 | 说明 |
|------|------|
| `/` | Dashboard（控制台） |
| `/jobs` | 任务列表 |
| `/jobs/:id` | 任务详情 |
| `/threads` | 帖子归档列表 |
| `/threads/:tid` | 帖子详情（本地归档） |
| `/forum` | **远程论坛浏览（新增）** |
| `/forum/:tid` | **远程贴子详情（新增）** |
| `/series` | 系列列表 |
| `/series/:id` | 系列详情 |
| `/review` | 标题复核 |
| `/exports` | 导出列表 |
| `/forums` | 版块统计 |
| `/rag` | 知识库 |
| `/settings` | 设置 |
| `/logs` | 日志 |

### 5.2 JSON API — 远程论坛

| 路径 | 方法 | 说明 |
|------|------|------|
| `/api/remote/forums` | GET | 获取启用的论坛分区列表 |
| `/api/remote/forum?forum_id=&page=&order=` | GET | 浏览远程论坛页面（支持 default/dateline 排序） |
| `/api/remote/threads/{tid}?page=` | GET | 读取远程贴子详情（单页） |
| `/api/remote/image?url=` | GET | 代理获取远程图片（服务端带 cookie） |

### 5.3 JSON API — 本地帖子状态

| 路径 | 方法 | 说明 |
|------|------|------|
| `/api/threads/{tid}/active-sync-job` | GET | 返回指定帖子的最新活动 `sync_thread` Job；无活动任务时返回 `{ "job": null }`，不包含 payload、artifact 或事件列表 |
| `/api/threads/{tid}/images/{asset_id}/retry` | POST | 为指定图片创建或复用交互式 selected `image_backfill` Job；只处理目标图片，不重跑原归档 Job |
| `/api/logs?limit=&since=&job_id=&tid=&event_type=&level=&component=&q=&errors_only=` | GET | 查询结构化实时日志；`since` 支持 ISO-8601 或 epoch 秒，`errors_only` 包含失败、部分成功、阻塞和缺失状态；返回时间范围与实际过滤条件 |

单图补取成功响应：

```json
{
  "ok": true,
  "job_id": "...",
  "status": "queued",
  "created": true
}
```

相同 `tid + asset_id + remote_url` 已存在活动 selected Job 时，返回同一个 `job_id`，并令 `created=false`。调用方应只轮询该 Job；所选图片恢复后 Job 可以是 `succeeded`，即使帖子因其他缺图仍保持 `partial`。

站内 `bbs.yamibo.com/forum.php?mod=attachment...` 图片会复用帖子抓取使用的 `curl_cffi` 会话、Cookie、代理和浏览器请求头。下载结果以 `image.download.result` / `image.download.summary` 写入结构化日志；selected 回填还会把脱敏后的逐图诊断写入 Job artifacts 和 `/api/jobs/{job_id}/events`。诊断字段包含稳定附件身份、HTTP 状态、内容类型、响应字节数、尝试次数、耗时、传输方式、错误分类和可重试性，不包含 Cookie、Authorization 或签名查询串。

```text
/api/logs?job_id=<job_id>&event_type=image.download.result&limit=100
/api/logs?tid=575256&event_type=image.download.result&errors_only=true&limit=100
/api/jobs/<job_id>/events
```

错误映射：`LoginRequiredError`→401, `ThreadPermissionRequiredError`→403, `RemoteMaintenanceError`→503, `RemoteFetchError`/`UnexpectedPageError`→502

### 5.4 JSON API — 管理操作

| 路径 | 方法 | 说明 |
|------|------|------|
| `/jobs/noop` | POST | 创建空任务 |
| `/jobs/sync-thread` | POST | 创建同步任务 |
| `/jobs/resync-thread` | POST | 重新同步 |
| `/jobs/export-thread` | POST | 创建导出任务 |
| `/jobs/rebuild-series` | POST | 批量重算系列 |
| `/jobs/pause` | POST | 暂停任务 |
| `/jobs/resume` | POST | 恢复任务 |
| `/threads/delete` | POST | 删除归档 |
| `/series/delete` | POST | 删除系列 |
| `/title-review/confirm-title` | POST | 确认标题 |
| `/title-review/update-title` | POST | 更新标题 |
| `/title-review/confirm-series` | POST | 确认系列 |
| `/title-review/merge-series` | POST | 合并系列 |

---

## 6. 错误码

| 异常类 | HTTP 等价 | 说明 |
|--------|----------|------|
| `JobNotFound` | 404 | 指定的 job_id 不存在 |
| `LeaseNotAcquired` | 409 | Daemon 无法抢占任务（已被其他 Daemon 持有） |
| `RemoteFetchError` | 502 | 远端 HTTP 请求失败 |
| `LoginRequiredError` | 401 | 论坛要求登录 |
| `RemoteMaintenanceError` | 503 | 论坛正在维护（每天 5:30-6:30 UTC+8） |
| `UnexpectedPageError` | 422 | 返回的页面不是预期类型 |
| `LLMRequestError` | 502 | LLM API 调用失败 |
| `ExportPrecheckError` | 422 | 导出前检查不通过（缺图、目录不存在等） |
