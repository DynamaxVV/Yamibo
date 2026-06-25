# 数据字典 / 数据库设计文档

> 版本：0.9.3 | 更新日期：2026-06-26

## 1. 概述

- **数据库引擎**：SQLite >= 3.35
- **存储路径**：`data/forum.db`（可通过 `YAMIBO_DB_PATH` 覆盖）
- **Journal 模式**：WAL（Write-Ahead Logging）
- **外键约束**：启用（`PRAGMA foreign_keys = ON`）
- **Busy Timeout**：5000ms
- **Schema 迁移**：`db/migrations.py` 中的 `migrate()` 函数，通过 `schema_migrations` 表跟踪版本

---

## 2. 表结构

### 2.1 schema_migrations

Schema 版本跟踪表。

| 列名 | 类型 | 约束 | 说明 |
|------|------|------|------|
| version | INTEGER | PRIMARY KEY | 迁移版本号 |
| applied_at | TEXT | NOT NULL, DEFAULT CURRENT_TIMESTAMP | 应用时间 |

---

### 2.2 jobs

后台任务表。Server 创建任务（INSERT），Daemon 抢占执行（UPDATE）。

| 列名 | 类型 | 约束 | 说明 |
|------|------|------|------|
| job_id | TEXT | PRIMARY KEY | 任务 ID，格式 `{type}_{uuid_hex[:16]}` |
| parent_job_id | TEXT | | 父任务 ID（如 export 内创建的 sync 子任务） |
| job_type | TEXT | NOT NULL | 任务类型：noop / sync_thread / export_thread / cleanup_job / title_refine |
| tid | INTEGER | | 关联的帖子 ID |
| payload_json | TEXT | NOT NULL, DEFAULT '{}' | 任务参数 JSON |
| status | TEXT | NOT NULL | 状态：queued / running / succeeded / partial / failed / interrupted / retrying / cancelled |
| stage | TEXT | | 当前执行阶段 |
| progress_current | INTEGER | NOT NULL, DEFAULT 0 | 当前进度 |
| progress_total | INTEGER | | 总进度 |
| worker_id | TEXT | | 持有租约的 Daemon ID |
| heartbeat_at | TEXT | | 最后心跳时间 |
| lease_until | TEXT | | 租约到期时间 |
| retry_count | INTEGER | NOT NULL, DEFAULT 0 | 已重试次数 |
| max_retries | INTEGER | NOT NULL, DEFAULT 3 | 最大重试次数 |
| resumable | INTEGER | NOT NULL, DEFAULT 1 | 是否可恢复 |
| cancel_requested_at | TEXT | | 请求取消时间 |
| error_code | TEXT | | 错误码 |
| error_message | TEXT | | 错误信息 |
| artifacts_json | TEXT | NOT NULL, DEFAULT '{}' | 产出物 JSON |
| created_at | TEXT | NOT NULL, DEFAULT CURRENT_TIMESTAMP | 创建时间 |
| updated_at | TEXT | NOT NULL, DEFAULT CURRENT_TIMESTAMP | 更新时间 |
| finished_at | TEXT | | 完成时间 |

**索引**：

| 名称 | 列 | 用途 |
|------|-----|------|
| idx_jobs_status_lease | (status, lease_until) | Daemon 抢占查询 |
| idx_jobs_tid_type | (tid, job_type) | 按帖子查任务 |
| idx_jobs_updated_at | (updated_at) | 时间排序 |

---

### 2.3 threads

帖子归档主表。

| 列名 | 类型 | 约束 | 说明 |
|------|------|------|------|
| tid | INTEGER | PRIMARY KEY | 帖子 ID |
| series_id | INTEGER | FK → series(series_id) | 关联系列 |
| page_type | TEXT | NOT NULL, DEFAULT 'unknown' | 页面类型 |
| raw_title | TEXT | NOT NULL | 原始标题 |
| display_title | TEXT | | 展示标题（处理后） |
| publisher | TEXT | | 发布者 |
| publisher_uid | TEXT | | 发布者 UID |
| pub_time | TEXT | | 发布时间 |
| sync_time | TEXT | | 最后同步时间 |
| last_pid | INTEGER | | 最后楼层 PID |
| permission | INTEGER | NOT NULL, DEFAULT 0, CHECK(>=0) | 权限等级 |
| is_finished | INTEGER | NOT NULL, DEFAULT 0 | 是否完结 |
| image_count | INTEGER | NOT NULL, DEFAULT 0, CHECK(>=0) | 图片数量 |
| context_path | TEXT | | context.md 相对路径 |
| archive_status | TEXT | NOT NULL, DEFAULT 'stale' | 归档状态：stale / complete / partial |
| validation_status | TEXT | NOT NULL, DEFAULT 'unknown' | 校验状态：unknown / valid / invalid |
| validation_errors_json | TEXT | | 校验错误 JSON |
| missing_images_json | TEXT | | 缺失图片 URL JSON |
| needs_title_review | INTEGER | NOT NULL, DEFAULT 0 | 需要标题复核 |
| needs_series_review | INTEGER | NOT NULL, DEFAULT 0 | 需要系列复核 |
| is_exported | INTEGER | NOT NULL, DEFAULT 0 | 是否已导出 |
| export_path | TEXT | | 导出 ZIP 路径 |
| forum_id | INTEGER | | 论坛分区 ID（30=漫画, 55=轻小说, 5=动漫, 33=水区） |
| content_kind | TEXT | | 内容类型：comic / novel / discussion / mixed |
| primary_media_type | TEXT | | 主要媒体类型：image / text |
| category | TEXT | | 子分类或标签（从帖子标题/页面解析得到） |

**索引**：

| 名称 | 列 | 用途 |
|------|-----|------|
| idx_threads_series_id | (series_id) | 系列关联查询 |
| idx_threads_archive_status | (archive_status) | 状态筛选 |
| idx_threads_sync_time | (sync_time) | 时间排序 |

---

### 2.4 floors

楼层表。每个帖子的每一层楼对应一条记录。

| 列名 | 类型 | 约束 | 说明 |
|------|------|------|------|
| pid | INTEGER | PRIMARY KEY | 楼层 ID（论坛 post ID） |
| tid | INTEGER | NOT NULL, FK → threads(tid) | 所属帖子 |
| floor_no | INTEGER | NOT NULL, CHECK(>0) | 楼层序号（从 1 开始） |
| publisher | TEXT | | 发布者 |
| content | TEXT | | 正文内容（清洗后） |
| pub_time | TEXT | | 发布时间 |
| has_images | INTEGER | NOT NULL, DEFAULT 0 | 是否含图片 |
| content_hash | TEXT | | 内容哈希（预留） |

**索引**：

| 名称 | 列 | 用途 |
|------|-----|------|
| idx_floors_tid_floor | (tid, floor_no) | 按帖子查楼层 |

---

### 2.5 title_parse

标题解析结果表。每个帖子一条记录。

| 列名 | 类型 | 约束 | 说明 |
|------|------|------|------|
| tid | INTEGER | PRIMARY KEY, FK → threads(tid) | 帖子 ID |
| raw_title | TEXT | NOT NULL | 原始标题 |
| display_title | TEXT | | 展示标题 |
| group_name | TEXT | | 汉化组名 |
| author_guess | TEXT | | 作者名 |
| core_title_guess | TEXT | | 核心标题（漫画名） |
| normalized_core_title | TEXT | | 规范化核心标题 |
| series_key | TEXT | | 系列键（用于聚合） |
| title_aliases_json | TEXT | | 标题别名 JSON 数组 |
| chapter_name | TEXT | | 章节名（如 "第13话"） |
| chapter_index | REAL | | 章节序号（如 13.2） |
| chapter_index_end | REAL | | 范围章节结束序号 |
| chapter_title | TEXT | | 章节标题 |
| subtitle | TEXT | | 副标题 |
| tags_json | TEXT | | 标签 JSON 数组 |
| confidence | REAL | | 解析置信度（0-1） |
| parser_version | TEXT | NOT NULL | 解析器版本 |
| needs_review | INTEGER | NOT NULL, DEFAULT 0 | 需要复核 |
| warnings_json | TEXT | | 警告信息 JSON |

---

### 2.6 series

系列管理表。通过 series_key 自动聚合。

| 列名 | 类型 | 约束 | 说明 |
|------|------|------|------|
| series_id | INTEGER | PRIMARY KEY AUTOINCREMENT | 系列 ID |
| canonical_title | TEXT | NOT NULL | 规范标题 |
| normalized_title | TEXT | NOT NULL | 规范化标题 |
| series_key | TEXT | NOT NULL, UNIQUE | 系列键 |
| alias_keys_json | TEXT | | 别名键 JSON |
| aliases_json | TEXT | | 别名 JSON |
| author_guess | TEXT | | 作者 |
| creator_key | TEXT | | 创建者键（用于同名不同作者的区分） |
| merge_confidence | REAL | | 合并置信度 |
| needs_review | INTEGER | NOT NULL, DEFAULT 0 | 需要复核 |
| created_at | TEXT | NOT NULL, DEFAULT CURRENT_TIMESTAMP | 创建时间 |
| updated_at | TEXT | NOT NULL, DEFAULT CURRENT_TIMESTAMP | 更新时间 |

---

### 2.7 catalog

帖子目录表（预留，用于章节导航）。

| 列名 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | INTEGER | PRIMARY KEY AUTOINCREMENT | 记录 ID |
| tid | INTEGER | NOT NULL, FK → threads(tid) | 帖子 ID |
| chapter_name | TEXT | | 章节名 |
| target_tid | INTEGER | | 目标帖子 ID |
| target_url | TEXT | | 目标 URL |
| resolve_status | TEXT | NOT NULL, DEFAULT 'resolved' | 解析状态 |

**唯一索引**：`(tid, target_tid, chapter_name)`

---

### 2.8 audit_events

审计事件表。记录关键数据变更。

| 列名 | 类型 | 约束 | 说明 |
|------|------|------|------|
| event_id | TEXT | PRIMARY KEY | 事件 ID（UUID hex） |
| actor | TEXT | NOT NULL | 执行者 |
| action | TEXT | NOT NULL | 操作 |
| target_type | TEXT | NOT NULL | 目标类型 |
| target_id | TEXT | NOT NULL | 目标 ID |
| before_json | TEXT | | 变更前 JSON |
| after_json | TEXT | | 变更后 JSON |
| created_at | TEXT | NOT NULL, DEFAULT CURRENT_TIMESTAMP | 创建时间 |

---

### 2.9 sync_runs

同步运行记录表（预留）。

| 列名 | 类型 | 约束 | 说明 |
|------|------|------|------|
| run_id | TEXT | PRIMARY KEY | 运行 ID |
| tid | INTEGER | NOT NULL | 帖子 ID |
| mode | TEXT | NOT NULL | 同步模式 |
| floors_added | INTEGER | NOT NULL, DEFAULT 0 | 新增楼层数 |
| images_added | INTEGER | NOT NULL, DEFAULT 0 | 新增图片数 |
| pages_fetched | INTEGER | NOT NULL, DEFAULT 0 | 抓取页数 |
| validation_status | TEXT | NOT NULL | 校验状态 |
| warnings_json | TEXT | | 警告 JSON |
| errors_json | TEXT | | 错误 JSON |
| created_at | TEXT | NOT NULL, DEFAULT CURRENT_TIMESTAMP | 创建时间 |

---

### 2.10 thread_fts

全文搜索虚拟表（FTS5）。

| 列名 | 说明 |
|------|------|
| tid | 帖子 ID（UNINDEXED） |
| title | 标题 |
| core_title | 核心标题 |
| author | 作者 |
| group_name | 汉化组 |
| content_preview | 内容预览（前 3 层楼） |
| catalog_text | 目录文本 |

---

### 2.11 forums

论坛分区表。预置漫画区(30)、轻小说区(55)、动漫区(5)、水区(33)。

| 列名 | 类型 | 约束 | 说明 |
|------|------|------|------|
| forum_id | INTEGER | PRIMARY KEY | 分区 ID |
| name | TEXT | NOT NULL | 分区名称 |
| content_kind | TEXT | NOT NULL | 内容类型：comic / novel / discussion |
| base_url | TEXT | NOT NULL | 站点根 URL |
| enabled | INTEGER | NOT NULL, DEFAULT 1 | 是否启用 |
| created_at | TEXT | NOT NULL, DEFAULT CURRENT_TIMESTAMP | 创建时间 |
| updated_at | TEXT | NOT NULL, DEFAULT CURRENT_TIMESTAMP | 更新时间 |

---

### 2.12 content_blocks

内容块表。每个帖子的有序内容块（text/image/attachment/quote/link/divider）。

| 列名 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | INTEGER | PRIMARY KEY AUTOINCREMENT | 记录 ID |
| tid | INTEGER | NOT NULL | 帖子 ID |
| pid | INTEGER | NOT NULL | 楼层 ID |
| order_index | INTEGER | NOT NULL | 块序号 |
| block_type | TEXT | NOT NULL | 块类型：text / image / attachment / quote / link / divider / unknown |
| text | TEXT | | 文本内容 |
| asset_id | TEXT | | 关联资产 ID |
| metadata_json | TEXT | NOT NULL, DEFAULT '{}' | 块元数据 JSON |

**索引**：`(tid, order_index)`

---

### 2.13 assets

资产表。统一管理图片、附件、共享资源。

| 列名 | 类型 | 约束 | 说明 |
|------|------|------|------|
| asset_id | TEXT | PRIMARY KEY | 资产 ID |
| tid | INTEGER | NOT NULL | 帖子 ID |
| pid | INTEGER | NOT NULL | 楼层 ID |
| asset_type | TEXT | NOT NULL | 资产类型：image / attachment / shared / external_link |
| remote_url | TEXT | NOT NULL | 远端 URL |
| local_path | TEXT | | 本地路径 |
| exportable | INTEGER | NOT NULL, DEFAULT 0 | 是否可导出 |
| required | INTEGER | NOT NULL, DEFAULT 0 | 是否必需 |
| status | TEXT | NOT NULL | 状态：pending / downloaded / skipped / missing |

**索引**：`(tid)`

---

### 2.14 job_events

任务事件表（append-only outbox）。记录任务状态变更历史。

| 列名 | 类型 | 约束 | 说明 |
|------|------|------|------|
| event_id | INTEGER | PRIMARY KEY AUTOINCREMENT | 事件 ID |
| job_id | TEXT | NOT NULL | 任务 ID |
| event_type | TEXT | NOT NULL | 事件类型：job.created / job.started / job.progressed / job.succeeded / job.partial / job.failed / job.cancelled |
| status | TEXT | | 任务状态 |
| stage | TEXT | | 执行阶段 |
| payload_json | TEXT | NOT NULL, DEFAULT '{}' | 事件负载 JSON |
| created_at | TEXT | NOT NULL, DEFAULT CURRENT_TIMESTAMP | 创建时间 |

**索引**：
- `(job_id, event_id)` — 按任务查事件
- `(created_at)` — 时间排序

---

## 3. 文件存储结构

```
data/
├── forum.db                    # SQLite 数据库
├── title_hints.json            # 标题提示词（汉化组、作者列表）
├── threads/                    # 帖子归档
│   └── {tid}/
│       ├── context.md          # 正文 Markdown（含 YAML frontmatter）
│       ├── metadata.json       # 完整元数据 JSON
│       └── images/             # 归档图片
│           ├── floor_001_01.jpg
│           └── ...
├── series/                     # 系列数据
│   ├── index.md                # 系列索引
│   └── {series_id}/
│       └── chapters.json       # 章节列表
├── shared/                     # 共享资源（论坛表情等）
│   └── {host}/{path}
├── staging/                    # 任务临时目录
│   └── jobs/
│       └── {job_id}/
│           ├── snapshot.json       # 解析快照
│           ├── failure.json        # 失败报告
│           ├── title_parse_log.json # 标题解析日志
│           └── images/             # 临时图片
├── exports/                    # 导出 ZIP 包
│   └── {series_name}/
│       └── {chapter}.zip
└── backups/                    # 数据库备份
    └── forum_{timestamp}.sqlite3
```

### 3.1 context.md 格式

```markdown
---
tid: 572313
raw_title: "【提灯喵汉化组】[ポテトルス] ray 第13话"
publisher: "xxx"
series_name: "ray"
chapter_name: "第13话"
chapter_index: 13.0
group_name: "提灯喵汉化组"
author_guess: "ポテトルス"
image_count: 24
archive_status: "complete"
parser_version: "title-v1+llm"
---

# 【提灯喵汉化组】[ポテトルス] ray 第13话

## 1F · publisher · 2026-06-01 12:00

正文内容...

![image](images/floor_001_01.jpg)
![image](images/floor_001_02.jpg)
```

### 3.2 metadata.json 结构

```json
{
  "tid": 572313,
  "url": "https://bbs.yamibo.com/forum.php?mod=viewthread&tid=572313",
  "page_type": "thread_detail",
  "raw_title": "...",
  "display_title": "...",
  "title": {
    "group_name": "...",
    "author_guess": "...",
    "core_title_guess": "...",
    "series_key": "...",
    "chapter_name": "...",
    "chapter_index": 13.0,
    "confidence": 0.9,
    "needs_review": false
  },
  "floors": [
    {
      "pid": 12345,
      "tid": 572313,
      "floor_no": 1,
      "publisher": "...",
      "content": "...",
      "pub_time": "...",
      "has_images": true,
      "image_urls": ["images/floor_001_01.jpg"],
      "content_image_urls": ["images/floor_001_01.jpg"],
      "non_export_image_urls": [],
      "shared_image_urls": [],
      "skipped_image_urls": []
    }
  ],
  "context_path": "threads/572313/context.md",
  "archived_images": { "12345": ["images/floor_001_01.jpg"] },
  "non_export_images": {},
  "shared_images": {},
  "missing_image_urls": [],
  "missing_shared_image_urls": []
}
```
