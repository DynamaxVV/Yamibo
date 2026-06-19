# API 接口文档

> 版本：0.1.0 | 更新日期：2026-06-19

## 1. MCP 工具 (Tools)

MCP Server 通过 FastMCP 暴露以下工具。LLM 客户端通过 MCP 协议调用。

### 1.1 search_threads

统一搜索帖子：优先按论坛页搜索和筛选，再结合本地归档补充详情。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| query | string | 否 | "" | 搜索关键词 |
| limit | int | 否 | 0 | 最大返回数，0 表示不限 |
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
        "export": "yamibo://threads/{tid}/export"
      }
    }
  ]
}
```

**注意**：搜索接口限流 10 秒/次。远端失败时自动降级到本地 FTS 搜索。

---

### 1.2 get_thread

读取帖子详情；若本地未归档则自动远端抓取并归档后返回。

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| tid | int | 是 | 帖子 ID |
| url | string \| null | 否 | 帖子 URL（可选） |
| base_url | string \| null | 否 | 站点根 URL |

**返回**：完整的帖子详情，包含楼层列表、标题解析结果、系列信息等。

```json
{
  "tid": 572313,
  "url": "...",
  "display_title": "...",
  "raw_title": "...",
  "publisher": "...",
  "publisher_uid": "...",
  "pub_time": "...",
  "image_count": 24,
  "archive_status": "complete",
  "title_parse": {
    "group_name": "...",
    "author_guess": "...",
    "core_title_guess": "...",
    "series_key": "...",
    "chapter_name": "...",
    "chapter_index": 1.0,
    "confidence": 0.9,
    "needs_review": false
  },
  "floors": [
    {
      "pid": 12345,
      "floor_no": 1,
      "publisher": "...",
      "pub_time": "...",
      "has_images": true,
      "content": "...",
      "content_preview": "..."
    }
  ],
  "floor_count": 2,
  "series": {
    "series_id": 1,
    "canonical_title": "...",
    "resources": {
      "index": "yamibo://series/index",
      "chapters": "yamibo://series/1/chapters"
    }
  }
}
```

---

### 1.3 browse_forum_page

读取漫画区某一页的帖子列表；短调用，直接返回该页帖子信息。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| page | int | 是 | - | 页码（正整数） |
| base_url | string | 否 | "https://bbs.yamibo.com" | 站点根 URL |
| cookie_file | string \| null | 否 | null | Cookie 文件路径 |
| include_sticky | bool | 否 | false | 是否包含置顶帖 |
| include_announcements | bool | 否 | false | 是否包含公告帖 |

---

### 1.4 archive_thread

创建帖子归档任务；长操作只返回 job_id。

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| html_path | string \| null | 否 | 本地 HTML 文件路径 |
| tid | int \| null | 否 | 帖子 ID |
| url | string \| null | 否 | 帖子 URL |
| base_url | string \| null | 否 | 站点根 URL |

三个参数至少提供一个。返回 `{"job_id": "sync_thread_xxxx"}`。

---

### 1.5 export_thread

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

---

### 1.6 sync_forum_range

按漫画区页码范围抓取真实帖子列表并批量创建同步任务。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| start_page | int | 是 | - | 起始页码 |
| end_page | int | 是 | - | 结束页码 |
| base_url | string | 否 | "https://bbs.yamibo.com" | 站点根 URL |
| cookie_file | string \| null | 否 | null | Cookie 文件路径 |
| include_sticky | bool | 否 | false | 是否包含置顶帖 |
| include_announcements | bool | 否 | false | 是否包含公告帖 |

---

### 1.7 get_job_status

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

---

### 1.8 cleanup_job

创建后台清理任务。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| job_id | string \| null | 否 | null | 指定 job 的 staging 目录 |
| mode | string | 否 | "job_staging" | 清理模式 |
| older_than_hours | int \| null | 否 | null | 过期时间阈值 |

**模式**：`job_staging`（清理指定 job 的 staging）、`stale_staging`（清理所有过期 staging）。

---

### 1.9 parse_thread_title

解析帖子标题；低置信度时可自动调用内置 LLM 做二次提取。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| title | string | 是 | - | 原始标题 |
| use_llm_on_low_confidence | bool | 否 | true | 低置信度时是否调用 LLM |

---

### 1.10 llm_transform_text

调用内置 OpenAI-compatible LLM 做文本提取或清洗。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| task | string | 是 | - | 任务描述 |
| text | string | 是 | - | 输入文本 |
| system_prompt | string \| null | 否 | null | 系统提示词 |
| temperature | float | 否 | 0.0 | 温度参数 |

---

## 2. MCP 资源 (Resources)

MCP Server 暴露以下只读资源，通过 `yamibo://` URI scheme 访问。

| URI | Content-Type | 说明 |
|-----|-------------|------|
| `yamibo://threads/{tid}/context` | text/markdown | 帖子正文 Markdown（含 frontmatter） |
| `yamibo://threads/{tid}/metadata` | application/json | 帖子完整元数据 |
| `yamibo://threads/{tid}/export` | application/zip | 帖子导出 ZIP 包 |
| `yamibo://series/index` | text/markdown | 系列索引（Markdown 格式） |
| `yamibo://series/{series_id}/chapters` | application/json | 系列章节列表 |

---

## 3. CLI 命令

所有 CLI 命令通过 `yamibo-mcp-server` 入口执行，直接返回 JSON 结果。

```bash
# 浏览论坛列表页
yamibo-mcp-server browse-forum-page --page 1

# 搜索帖子
yamibo-mcp-server search-threads --query "星灵感应"

# 获取帖子详情（自动归档）
yamibo-mcp-server get-thread --tid 572313

# 创建归档任务
yamibo-mcp-server create-sync-thread-job --tid 572313

# 创建导出任务
yamibo-mcp-server create-export-thread-job --tid 572313

# 批量同步
yamibo-mcp-server create-sync-forum-range-jobs --start-page 1 --end-page 5

# 任务状态
yamibo-mcp-server job-status <job_id>

# 解析标题
yamibo-mcp-server parse-thread-title "【提灯喵汉化组】[ポテトルス] ray 第13话"

# LLM 文本处理
yamibo-mcp-server llm-transform-text --task "提取作者名" --text "..."

# 列出导出包
yamibo-mcp-server list-exports

# 读取资源
yamibo-mcp-server read-resource "yamibo://threads/572313/context"
```

---

## 4. Web API

Web 控制台基于 HTTP，提供 HTML 页面和表单操作。

### 4.1 页面路由

| 路径 | 方法 | 说明 |
|------|------|------|
| `/` | GET | Dashboard |
| `/jobs` | GET | 任务列表 |
| `/jobs/{job_id}` | GET | 任务详情 |
| `/threads` | GET | 帖子列表 |
| `/threads/{tid}` | GET | 帖子详情 |
| `/series` | GET | 系列列表 |
| `/series/{series_id}` | GET | 系列详情 |
| `/title-review` | GET | 标题复核 |
| `/exports` | GET | 导出列表 |
| `/media/{path}` | GET | 本地图片文件 |
| `/artifacts/jobs/{job_id}/{file}` | GET | Staging 文件 |

### 4.2 表单操作

| 路径 | 方法 | 说明 |
|------|------|------|
| `/jobs/noop` | POST | 创建空任务 |
| `/jobs/sync-thread` | POST | 创建同步任务 |
| `/jobs/resync-thread` | POST | 重新同步 |
| `/jobs/export-thread` | POST | 创建导出任务 |
| `/jobs/rebuild-series` | POST | 批量重算系列 |
| `/threads/delete` | POST | 删除归档 |
| `/series/delete` | POST | 删除系列 |
| `/title-review/confirm-title` | POST | 确认标题 |
| `/title-review/update-title` | POST | 更新标题 |
| `/title-review/confirm-series` | POST | 确认系列 |
| `/title-review/merge-series` | POST | 合并系列 |

---

## 5. 错误码

| 异常类 | HTTP 等价 | 说明 |
|--------|----------|------|
| `JobNotFound` | 404 | 指定的 job_id 不存在 |
| `LeaseNotAcquired` | 409 | Worker 无法抢占任务（已被其他 Worker 持有） |
| `RemoteFetchError` | 502 | 远端 HTTP 请求失败 |
| `LoginRequiredError` | 401 | 论坛要求登录 |
| `RemoteMaintenanceError` | 503 | 论坛正在维护（每天 5:30-6:30 UTC+8） |
| `UnexpectedPageError` | 422 | 返回的页面不是预期类型 |
| `LLMRequestError` | 502 | LLM API 调用失败 |
| `ExportPrecheckError` | 422 | 导出前检查不通过（缺图、目录不存在等） |
