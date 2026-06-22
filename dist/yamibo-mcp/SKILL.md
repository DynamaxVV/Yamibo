---
name: yamibo-mcp
description: 使用 YamiboMCP 检索、查看、归档、检查更新、追加更新和导出百合会论坛帖子。支持多分区（漫画区/轻小说区/动漫区/水区）。适用于按标题找帖子、浏览论坛某一页、按 tid 查看帖子、读取归档摘要与正文块、检查任务状态与事件时间线、人工复核标题信息、判断轻小说是否有新内容等场景。用户提到百合会、Yamibo、漫画帖子、轻小说、章节归档、导出 ZIP/TXT、标题解析、系列归并时使用。
---

# YamiboMCP 中文 Skill

把 YamiboMCP 当作"论坛帖子检索、归档与任务状态读取接口"来使用。

只通过公开 MCP Tools 和 Resources 完成任务，不描述内部实现，不假设底层数据来源。

## 基本原则

- 先用工具取事实，再组织回答。
- 能返回结构化信息时，优先返回结构化字段。
- 说明"发布者"时使用"发布者 / publisher"，不要误写成"作者"，除非明确在说漫画作者。
- 同名作品可能存在繁简、双语、空格、标点差异，判断系列时优先参考标题解析结果和系列信息。
- 长内容优先通过 Resource 读取，不把整篇正文一次性塞进简短回复。
- Agent 工作流优先读取紧凑资源（summary → diagnostics → posts/assets/context），降低 token 开销。
- 长任务先读 `read_job`，排障再读 `read_job_events` 或 `yamibo://jobs/{job_id}/events`。

## 可用接口

### `search_forum_threads`

用于搜索帖子、章节线索、作品名。

适用场景：
- 用户说"找某部漫画"或"找某部小说"
- 用户说"搜这个标题"
- 用户只记得部分作品名、章节名、作者名、汉化组名
- 需要先拿候选帖子列表

调用建议：
- `query`：填作品名、章节名、作者名、汉化组名或标题片段
- `forum_id`：指定论坛分区（30=漫画区, 55=轻小说区, 5=动漫区, 33=水区），默认 30
- 如果用户明确要论坛搜索结果，直接按论坛搜索理解
- 如果用户想找最近更新内容，再结合返回结果里的时间字段继续筛选

期望输出重点：
- `tid`
- 标题
- 系列名 / 章节信息
- 发布者
- 发帖时间
- 是否可继续查看
- `resources` 中包含 `summary`/`diagnostics`/`posts`/`assets` URI

### `browse_forum_page`

用于读取论坛某一页的帖子列表。

适用场景：
- 用户说"看漫画区第一页有什么"
- 用户说"检查轻小说区第 3 页最近有哪些帖子"
- 用户要按页面浏览而不是关键词搜索
- 用户要找"今天的帖子"，需要从前几页按时间筛选

调用建议：
- `page`：填页码
- `forum_id`：指定论坛分区（30=漫画区, 55=轻小说区, 5=动漫区, 33=水区），默认 30
- 这是短调用，直接返回该页帖子列表
- 如需读某一帖详情，再继续调用 `inspect_remote_thread`

期望输出重点：
- `tid`
- 标题
- 发布者
- 发帖时间
- 回复数
- 该页 URL

### `inspect_remote_thread`

用于查看单个帖子远端只读预览。

适用场景：
- 用户给出 `tid`
- 用户给出帖子 URL
- 已经从 `search_forum_threads` 或 `browse_forum_page` 拿到候选，准备查看其中一条

调用建议：
- 已知 `tid` 时直接传 `tid`
- 已知 URL 时传 URL 或先提取 `tid`
- 这是远端预览，不会写入本地归档
- 先给用户摘要，再按需继续读取本地资源

期望输出重点：
- `tid`
- 标题解析结果
- 发布者
- 发帖时间
- 楼层数
- 图片数量
- 可读取的资源引用（含 summary/diagnostics/posts/assets）

### `create_thread_archive_job`

用于发起帖子归档任务。

适用场景：
- 用户要求"归档这个帖子"
- 用户要求"把这个帖子同步到本地"
- 用户要求"重新抓取一次最新内容"

调用建议：
- 这是长操作，发起后只关注返回的 `job_id`
- 不要等待长任务直接完成再回复
- 回复中说明任务已创建，并建议继续查询 `read_job`

### `ensure_thread_archived`

用于确保本地存在归档；缺失时创建归档任务。

适用场景：
- 用户明确要读本地归档，但当前还没有本地副本
- 想优先保证可读性，再由 daemon 异步补齐

### `read_archived_thread`

用于读取本地归档视图。

适用场景：
- 用户要摘要、正文块、资产、诊断或元数据
- 想读取已归档内容，但不想再触发远端抓取

调用建议：
- 常用视图按需选择：`summary` / `content` / `assets` / `diagnostics` / `export` / `metadata`
- `content` 视图支持 `cursor` / `chunk_size` 分页，读取大帖时按 `next_cursor` 继续
- 这不是远端抓取接口，结果来自本地 SQLite 和物化文件

### `create_thread_export_job`

用于导出帖子归档结果。

适用场景：
- 用户要求导出 ZIP 或轻小说 TXT
- 用户要离线阅读
- 用户要整理某一话到本地文件

调用建议：
- 这是长操作，优先返回 `job_id`
- 如果用户指定了某个帖子，按该帖子发起导出
- 如果用户需要批量导出，逐个创建任务并分别汇报状态

### `check_thread_updates`

用于检查已归档的轻小说贴子是否有新内容。

适用场景：
- 用户问“这个轻小说贴子有没有更新”
- 用户要在重新归档前先确认是否需要追加更新
- 需要只读地判断远端只看楼主页面与本地归档是否一致

调用建议：
- 输入 `tid`
- 这是只读操作，不创建任务
- 如果结果是 `updated`，再继续调用 `create_thread_update_job`

### `create_thread_update_job`

用于对轻小说贴子执行追加更新。

适用场景：
- `check_thread_updates` 返回 `updated`
- 用户要求“只补上新增楼层，不要全量重抓”
- 需要把远端新增内容追加到本地 TXT / 归档里

调用建议：
- 先确认 `check_thread_updates` 结果
- 输入 `tid`
- 这是长操作，发起后关注返回的 `job_id`

### `read_job`

用于查看归档、导出、清理等长任务进度。

适用场景：
- 用户问"任务好了没"
- 你刚刚创建了 `create_thread_archive_job` 或 `create_thread_export_job`
- 需要查看失败原因

调用建议：
- 输入 `job_id`
- 重点关注状态、阶段、错误信息、产物位置

### `read_job_events`

用于查看长任务事件时间线。

适用场景：
- 任务失败后排障
- 任务长时间停留在 running / retrying / interrupted
- 需要看阶段切换、错误上下文、恢复过程

### `cleanup_job`

用于清理任务残留或无效中间结果。

适用场景：
- 用户明确要求清理失败任务
- 某个任务失败后需要收尾
- 用户要求清理调试残留

调用建议：
- 先确认目标 `job_id`
- 清理前先告诉用户这是针对任务残留的操作

### `read_forum_profiles`

用于查看可用论坛分区信息。

适用场景：
- 想先确认 forum_id、分区名称、内容类型
- 在搜索或浏览前读取分区配置

### `parse_thread_title`

用于单独解析帖子标题。

适用场景：
- 用户问"这个标题里哪些是作者、汉化组、作品名、章节名"
- 标题解析明显不准，需要单独核验
- 需要先分析标题再决定是否归档

期望输出重点：
- 汉化组
- 漫画作者
- 系列名
- 章节名 / 章节号
- 其他补充信息
- 解析置信度

## Resources 的使用方式

Agent 工作流优先读取紧凑资源，按需深入：

### 推荐资源读取顺序

1. **summary**（`yamibo://threads/{tid}/summary`）— 紧凑摘要，不读完整 context.md，适合快速判断状态
2. **diagnostics**（`yamibo://threads/{tid}/diagnostics`）— 归档状态、缺失资产数、建议下一步操作
3. **update-check**（`yamibo://threads/{tid}/update-check`）— 轻小说更新检测结果
4. **posts**（`yamibo://threads/{tid}/posts`）— 有序内容块列表（text/image/quote/link），需要时读取
5. **assets**（`yamibo://threads/{tid}/assets`）— 资产详情（图片/附件/共享资源状态），需要时读取
6. **context**（`yamibo://threads/{tid}/context`）— 完整正文 Markdown，仅在需要完整内容时读取
7. **metadata**（`yamibo://threads/{tid}/metadata`）— 完整元数据 JSON
8. **job events**（`yamibo://jobs/{job_id}/events`）— 任务事件时间线，用于排障和恢复判断
9. **guide**（`yamibo://guide/agent-workflows` / `yamibo://guide/archive-model` / `yamibo://guide/error-codes`）— Agent 工作流、资源边界和错误码说明

### 其他资源

- **forums/index**（`yamibo://forums/index`）— 论坛分区列表
- **forums/{forum_id}/summary**（`yamibo://forums/{forum_id}/summary`）— 分区摘要
- **jobs/{job_id}/events**（`yamibo://jobs/{job_id}/events`）— 任务事件时间线（append-only）
- **series/index**（`yamibo://series/index`）— 系列索引
- **series/{series_id}/chapters**（`yamibo://series/{series_id}/chapters`）— 系列章节列表

使用原则：
- 先用 Tool 定位对象
- 再用 Resource 读取大内容
- 回答用户时做摘要，不机械转抄整段内容
- summary 和 diagnostics 是低 token 资源，适合 Agent 首选读取

## 推荐工作流

### 1. 查指定帖子

1. 调用 `search_forum_threads`（可指定 `forum_id`）
2. 从候选中选出最相关帖子
3. 读取 `yamibo://threads/{tid}/summary` 获取紧凑摘要
4. 如需更多信息，读取 `diagnostics` → `posts` → `context`

### 2. 按页浏览论坛

1. 调用 `browse_forum_page`（可指定 `forum_id`）
2. 从返回结果中按时间、标题或发布者筛选
3. 如需查看详情，读取对应帖子的 `summary` 资源

### 3. 看指定帖子

1. 用户给出 `tid` 或 URL
2. 调用 `inspect_remote_thread`
3. 按需读取 summary / diagnostics / posts / assets / context
4. 输出摘要、章节信息、发布者、时间、图片情况

### 4. 归档帖子

1. 调用 `create_thread_archive_job`
2. 向用户返回 `job_id`
3. 需要追踪时调用 `read_job`
4. 可读取 `yamibo://jobs/{job_id}/events` 查看事件时间线

### 5. 检查轻小说更新

1. 调用 `check_thread_updates`
2. 读取 `yamibo://threads/{tid}/update-check` 复核结果
3. 如果结果是 `updated`，再调用 `create_thread_update_job`
4. 需要追踪时调用 `read_job`

### 6. 导出帖子

1. 先确认帖子对象
2. 调用 `create_thread_export_job`
3. 向用户返回 `job_id`
4. 需要追踪时调用 `read_job`

### 7. 诊断归档问题

1. 读取 `yamibo://threads/{tid}/diagnostics`
2. 检查 `archive_status`、`missing_required_assets_count`、`warnings`、`next_actions`
3. 根据 `next_actions` 建议用户下一步操作

## 回答风格要求

- 先给结论，再给依据。
- 默认给出结构化关键信息：`tid`、标题、系列、章节、发布者、时间。
- 如果结果是候选集，说明"最相关 / 可能相关 / 待确认"。
- 如果任务仍在进行，不假装已经完成，明确返回 `job_id` 和当前状态。
- 如果帖子内容较长，只总结重点，并提示可继续读取完整资源。
- 诊断信息优先从 `diagnostics` 资源获取，不读完整 context.md。
