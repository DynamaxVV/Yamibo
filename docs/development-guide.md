# 核心模块开发说明

> 版本：0.1.0 | 更新日期：2026-06-19

## 1. 标题解析引擎

### 1.1 架构

标题解析采用 **规则引擎 + LLM 辅助** 的双层架构：

```
原始标题
  │
  ▼
规则引擎 (title/parser.py)
  │ 1. strip_discuz_suffix → 去除论坛后缀
  │ 2. pop_prefixes        → 提取【汉化组】和[标签]前缀
  │ 3. pop_author          → 提取[作者]或［作者］
  │ 4. subtitle_match      → 提取「书名号」内的副标题
  │ 5. pop_chapter         → 提取章节号、章节名
  │ 6. split_aliases       → 分离 / 或 | 分隔的别名
  │
  ▼
TitleParseResult
  │
  ▼
should_parse_title_with_llm? ──→ 是 ──→ LLM 提取 (services/title_llm.py)
  │                                           │
  否                                          ▼
  │                                   合并结果 (_merge_title_payload)
  ▼                                           │
最终 TitleParseResult ◄──────────────────────┘
```

### 1.2 规则引擎核心

**文件**：`yamibo/title/parser.py`

解析流程（按顺序执行）：

1. `strip_discuz_suffix(html_lib.unescape(raw_title))` — 去除 Discuz 后缀 + HTML 实体解码
2. `_pop_prefixes()` — 从标题头部循环提取前缀
   - `【xxx】` 中含 "组/組/汉化/漢化/转载/轉載/研究中心" → `group_name`（仅取第一个）
   - `[童]`、`[GBC]`、`[C数字]`、`[!]` → 追加到 `tags`
   - 其他 `【xxx】` → **跳过**（非组非标签，跳过后继续循环）
   - 其他 `[xxx]` 或 `(xxx)` → **停止**（可能是作者，留给下一步）
3. `_pop_author()` — 从剩余文本头部提取 `[作者]` 或 `［作者］`（仅匹配行首）
4. `_SUBTITLE_RE.match()` — 提取末尾 `「书名号」` 内的内容作为 `subtitle`，剩余部分继续处理
5. `_pop_chapter()` — 通过正则从剩余文本中搜索章节信息
6. `_split_aliases()` — 若剩余文本含 `/`、`|`、`｜`，第一段作为主标题，其余作为 `title_aliases`
7. 若无章节信息 → 默认 `chapter_name="1"`, `chapter_index=1.0`

**章节正则匹配优先级**（`_CHAPTER_RE` 中的 alternation 顺序）：

| 优先级 | 模式 | 示例 | 结果 |
|--------|------|------|------|
| 1 | 范围 `\d+~\d+` | `51~60` | chapter_index=51, chapter_index_end=60 |
| 2 | 冒号 `\d+:标题` | `33:露露娜大人` | chapter_name="33", chapter_index=33.0, chapter_title="露露娜大人" |
| 3 | `第N话` 带前缀 | `第13话`、`第08话` | chapter_index=13.0 / 8.0 |
| 4 | 裸数字 `N话` | `13话`、`233话` | chapter_index=13.0 / 233.0 |
| 5 | 特殊内容 | `特典`、`彩页`、`番外`、`加笔`、`贴附`、`上篇`、`下篇` 等 | chapter_name="999 特典", chapter_index=999.0 |

**章节索引计算规则**（应用在优先级 3 和 4 匹配后）：

| 标题 | chapter_name | chapter_index |
|------|-------------|---------------|
| `第13话` | "第13话" | 13.0 |
| `13话其2` | "13话其2" | 13.2（13 + 2/10） |
| `13话上` / `13话前篇` | "13话上" | 13.1（13 + 0.1） |
| `13话下` / `13话后篇` | "13话下" | 13.2（13 + 0.2） |
| `51~60` | "51~60" | 51.0（end=60.0） |
| `特典` / `彩页` / `番外` | "999 特典" | 999.0 |
| `33:标题` | "33" | 33.0（chapter_title="标题"） |
| `第13话（续）` | "第13话（续）" | 13.0（括号内容被正则吃掉但不影响索引） |
| 无章节信息 | "1" | 1.0（默认值） |

**置信度与复核标记**：

```
confidence = 0.9  if core_title 和 series_key 均非空
             0.4  otherwise

needs_review = confidence < 0.75
             or len(core_title_guess) <= 3
             or bool(aliases)           # 有别名时强制复核
```

### 1.3 文本规范化

**文件**：`yamibo/title/normalizer.py`

- `normalize_display_title()` — NFKC 规范化、全角斜杠/竖线转换、空白压缩
- `normalize_series_key()` — 繁简转换（硬编码映射表）、去标点/括号/假名噪声、小写化，用于 series 聚合
- `strip_discuz_suffix()` — 去除 `- 中文百合漫画区 - 百合会 - Powered by Discuz!` 后缀

### 1.4 LLM 辅助

**文件**：`services/title_llm.py`

**触发判断函数**：`should_parse_title_with_llm(settings, parsed)`

**判断优先级**（短路求值）：

```
1. API Key 未配置 → 不调用（直接返回 False）
2. title_parse_use_llm = true → 始终调用（忽略以下所有条件）
3. 以下条件满足任一 → 调用：
   - needs_review = true
   - confidence < 0.75
   - series_key 为空
   - core_title_guess 长度 <= 3
```

LLM 使用 OpenAI-compatible API，prompt 中包含：
- 提取规则说明（详细的 JSON Schema 要求）
- 常见汉化组/作者提示词（从 `title_hints.json` 加载）
- 规则引擎的 baseline 结果（作为参考）
- 要求返回严格 JSON，字段包括：group_name, author_guess, core_title_guess, title_aliases, chapter_name, chapter_index, chapter_index_end, chapter_title, subtitle, tags, confidence, needs_review

---

## 2. Job 系统

### 2.1 Job 类型

| 类型 | 处理器 | 说明 |
|------|--------|------|
| `noop` | `handlers/noop.py` | 空操作，用于验证系统 |
| `sync_thread` | `handlers/sync_thread.py` | 帖子同步（解析 → 校验 → 下载图片 → 写 DB → 物化） |
| `export_thread` | `handlers/export_thread.py` | 帖子导出（precheck → sync if needed → ZIP 打包） |
| `cleanup_job` | `handlers/cleanup_job.py` | 清理 staging 目录 |
| `title_refine` | `handlers/title_refine.py` | 标题精炼（重新运行 LLM 解析） |

### 2.2 租约抢占机制

```
Worker A: acquire(job_id, worker_id, lease_seconds)
  → UPDATE jobs SET status='running', worker_id='A', lease_until=now+60s
    WHERE job_id=? AND status IN ('queued','retrying','interrupted')
      AND (lease_until IS NULL OR lease_until < now)
  → if rowcount != 1: raise LeaseNotAcquired

Worker A: heartbeat(job_id, worker_id, lease_seconds)
  → UPDATE jobs SET heartbeat_at=now, lease_until=now+60s
    WHERE job_id=? AND worker_id=? AND status='running'

Worker B: recover_expired_jobs()
  → UPDATE jobs SET status='interrupted'
    WHERE status='running' AND lease_until < now
```

### 2.3 sync_thread 阶段

| 阶段 | 进度 | 说明 |
|------|------|------|
| `parse` | 1/6 | 解析 HTML → ThreadSnapshot |
| `staging` | 2/6 | 写入 staging/snapshot.json |
| `validate` | 3/6 | 校验快照完整性 |
| `download_images` | 4/6 | 下载图片到 staging |
| `db_commit` | 5/6 | 写入 SQLite（事务） |
| `materialize` | 6/6 | 物化到正式归档目录 |

### 2.4 错误处理

- 失败时写入 `staging/{job_id}/failure.json`
- 图片下载失败不阻断整体归档（标记为 `partial`）
- Worker 崩溃后，过期任务自动恢复为 `interrupted` 状态

---

## 3. 图片处理

### 3.1 下载流程

**文件**：`storage/images.py`

1. 遍历帖子所有楼层的图片 URL
2. 共享论坛资源（`/static/image/`）→ 下载到 `shared/` 目录
3. 非导出图片（表情、小图）→ 标记跳过
4. 内容图片 → 下载到 `staging/{job_id}/images/`
5. 图片格式检测：Content-Type → 文件头魔数 → URL 后缀

### 3.2 图片分类

| 类别 | 说明 | 导出包含 |
|------|------|---------|
| 内容图片 | 楼主发布的漫画图片 | 是 |
| 非导出图片 | 表情、分隔线、小图标（<=160x160） | 否 |
| 共享资源 | 论坛表情 `/static/image/` | 否 |
| 缺失图片 | 下载失败的 URL | 记录到 missing_image_urls |

### 3.3 格式检测优先级

1. HTTP Content-Type 头
2. 文件头魔数（JPEG: `\xff\xd8\xff`, PNG: `\x89PNG`, GIF: `GIF87a/89a`, WebP: `RIFF...WEBP`）
3. URL 路径后缀

---

## 4. 系列管理

### 4.1 自动聚合

**文件**：`yamibo/series_matcher.py` + `db/repositories/series.py`

规则：
1. 通过 `series_key`（`normalize_series_key(core_title_guess)`）匹配已有系列
2. 若存在同 series_key 但不同 `creator_key`（作者），自动添加后缀 `__author_{creator_key}` 并标记 `needs_review`
3. 系列的 `alias_keys` 和 `aliases` 随新帖子入归档自动累积

### 4.2 人工复核

通过 Web 控制台的标题复核页面：
- 修正标题解析结果
- 确认标题（清除 `needs_review` 标记）
- 合并系列（将源系列的所有帖子重定向到目标系列）
- 确认系列

---

## 5. 导出系统

### 5.1 导出策略

| 策略 | 行为 |
|------|------|
| `cache_only` | 直接使用本地已有的归档数据打包，不触发远程同步 |
| `sync_if_stale` | 若本地归档过旧（>24h）或不完整，先创建 sync 子任务 |
| `force_resync` | 强制创建 sync 子任务重新归档 |

### 5.2 ZIP 包结构

```
{series_name}/
└── {chapter_label}.zip
    └── {tid}/
        ├── context.md
        ├── metadata.json
        └── images/
            ├── floor_001_01.jpg
            └── ...
```

### 5.3 导出前检查

1. 帖子 `archive_status` 必须为 `complete`
2. `metadata.json` 中的 `missing_image_urls` 必须为空
3. 所有 `archived_images` 中的文件必须存在

---

## 6. 配置系统

### 6.1 配置优先级

```
环境变量 (YAMIBO_*)  >  yamibo.local.json  >  硬编码默认值
```

### 6.2 关键配置项

| 配置项 | 环境变量 | 默认值 | 说明 |
|--------|---------|--------|------|
| data_dir | YAMIBO_DATA_DIR | ./data | 数据目录 |
| db_path | YAMIBO_DB_PATH | data/forum.db | 数据库路径 |
| cookie_file | YAMIBO_COOKIE_FILE | .cookie | Cookie 文件 |
| export_dir | YAMIBO_EXPORT_DIR | data/exports | 导出目录 |
| web_host | YAMIBO_WEB_HOST | 127.0.0.1 | Web 监听地址 |
| web_port | YAMIBO_WEB_PORT | 8765 | Web 端口 |
| worker_poll_seconds | YAMIBO_WORKER_POLL_SECONDS | 2 | Worker 轮询间隔 |
| worker_lease_seconds | YAMIBO_WORKER_LEASE_SECONDS | 60 | 租约时长 |
| llm_base_url | YAMIBO_LLM_BASE_URL | https://api.openai.com/v1 | LLM API 地址 |
| llm_api_key | YAMIBO_LLM_API_KEY | - | LLM API Key |
| llm_model | YAMIBO_LLM_MODEL | gpt-4.1-mini | LLM 模型 |
| title_parse_use_llm | YAMIBO_TITLE_PARSE_USE_LLM | true | 是否始终使用 LLM 解析标题 |
| image_download_timeout_seconds | YAMIBO_IMAGE_DOWNLOAD_TIMEOUT_SECONDS | 45 | 图片下载超时 |
| image_download_retries | YAMIBO_IMAGE_DOWNLOAD_RETRIES | 2 | 图片下载重试次数 |
| backup_keep_count | YAMIBO_BACKUP_KEEP_COUNT | 20 | 备份保留数量 |
| cleanup_staging_older_than_hours | YAMIBO_CLEANUP_STAGING_OLDER_THAN_HOURS | 48 | staging 过期时间 |

### 6.3 配置文件格式

```json
{
  "yamibo": {
    "cookie_file": ".cookie",
    "use_system_proxy": false,
    "image_download_timeout_seconds": 45,
    "image_download_retries": 2
  },
  "login": {
    "username": "",
    "password": ""
  },
  "web": {
    "host": "127.0.0.1",
    "port": 8765
  },
  "worker": {
    "poll_seconds": 2,
    "lease_seconds": 60,
    "heartbeat_seconds": 15
  },
  "llm": {
    "base_url": "https://api.openai.com/v1",
    "api_key": "",
    "model": "gpt-4.1-mini"
  },
  "title": {
    "use_llm": true,
    "hints_path": "data/title_hints.json",
    "common_scanlation_groups": [],
    "common_authors": []
  },
  "export": {
    "dir": "data/exports",
    "default_strategy": "cache_only",
    "stale_after_hours": 24
  },
  "maintenance": {
    "backup_dir": "data/backups",
    "backup_keep_count": 20,
    "cleanup_staging_older_than_hours": 48
  }
}
```
