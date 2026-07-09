---
name: yamibo-archiver
description: 使用 YamiboArchiver 检索、浏览、归档、批量归档、检查轻小说更新、追加更新、构建 RAG 索引、读取本地归档，以及执行 PostgreSQL-only 的 Discussion Trend V1 趋势查询、evidence 检索和报告工作流。用户提到百合会、Yamibo、漫画区、轻小说区、动漫区、海域区、tid、归档、导出、RAG、本地检索、论坛趋势、topic 证据、forum evidence、trend report、research report 时使用。
---

# YamiboArchiver 中文 Skill

把 YamiboArchiver 当作“百合会论坛检索、归档、任务状态读取和历史讨论分析接口”来使用。

作为 LLM skill 执行任务时，只通过公开 MCP Tools 和 Resources 完成任务，不描述内部实现，不假设底层数据来源。CLI 示例只用于给用户或脚本说明等价入口。

## 基本原则

- 先用工具取事实，再组织回答。
- 能返回结构化信息时，优先返回结构化字段。
- 说明“发布者”时使用“发布者 / publisher”，不要误写成“作者”，除非明确在说漫画作者。
- 同名作品可能存在繁简、双语、空格、标点差异，判断系列时优先参考标题解析结果和系列信息。
- 长内容优先通过 Resource 读取，不把整篇正文一次性塞进简短回复。
- Agent 工作流优先读取紧凑资源：`summary -> diagnostics -> posts/assets/context`。
- 长任务优先读 `read_job`；需要阻塞等待时直接用 `wait_for_job`，排障再读 `read_job_events` 或 `yamibo://jobs/{job_id}/events`。
- 批量任务优先使用批量接口，不要循环创建单贴任务模拟批量提交。
- Discussion Trend V1 仅支持 PostgreSQL；如果后端不是 PostgreSQL，不要承诺趋势查询、topic evidence、forum evidence pack 或 report artifact 一定可用。

## Tool / Resource / CLI 边界

- MCP Tool 负责结构化操作：远端只读查询、创建后台 job、轮询 job、按参数读取本地归档视图，以及 `read_archived_thread(content)` 的 `cursor` / `chunk_size` 分页。
- MCP Resource 负责稳定 URI 下的大文本、文件和只读快照：归档正文、帖子列表、诊断、资产、导出包、job status/events、guide 和 schema。
- Resource 只读取稳定内容，不创建 job，不触发远程抓取。
- CLI 用于人类操作和脚本；MCP Tool / Resource 用于 LLM 客户端。两者共享 application 层，但返回面和适用场景不同。

当前 CLI 对齐入口：

```bash
uv run yamibo-archiver create-thread-archive-job --tid <tid>
uv run yamibo-archiver inspect-remote-thread --tid <tid>
uv run yamibo-archiver probe-archived-threads --tid <tid1> --tid <tid2>
uv run yamibo-archiver ensure-thread-archived --tid <tid>
uv run yamibo-archiver wait-for-job <job_id>
uv run yamibo-archiver read-job-events <job_id>
```

`create-sync-thread-job` / `create-sync-thread-batch-jobs` 仍可作为兼容入口使用；单帖归档文档和新工作流优先写 `create-thread-archive-job`。

## 当前公共 MCP Tools

以下是当前版本公开的 Agent-facing 能力面。不要调用未列出的内部或历史接口。

### 远端检索与预览

#### `search_forum_threads`

用于按关键词搜索论坛帖子。

适用场景：
- 用户说“找某部漫画/小说”
- 用户只记得作品名、章节名、作者名、汉化组名或标题片段
- 需要先拿候选帖子列表

输出重点：
- `tid`
- 标题
- 系列 / 章节信息
- 发布者
- 发帖时间
- 本地归档状态
- `resources.summary` / `resources.diagnostics`

#### `browse_forum_page`

用于读取论坛某一页的帖子列表。

适用场景：
- 用户说“看漫画区第一页有什么”
- 用户要按页面浏览而不是关键词搜索
- 用户要从前几页筛“最近帖子”

输出重点：
- `tid`
- 标题
- 发布者
- 发帖时间
- 回复数
- 该页 URL

#### `inspect_remote_thread`

用于查看单个帖子远端只读预览。

适用场景：
- 用户给出 `tid`
- 用户给出帖子 URL
- 已经从搜索或浏览结果里拿到候选，准备查看单贴摘要

注意：
- 这是远端预览，不会写入本地归档
- 先给摘要，再按需转向本地 resource 或创建任务

### 本地归档与批量探测

#### `ensure_thread_archived`

用于保证本地有归档；若缺失则创建归档任务。

适用场景：
- 用户要读本地归档，但当前可能还没有本地副本
- 你希望优先保证后续可读性

#### `read_archived_thread`

用于读取本地归档视图。

常用视图：
- `summary`
- `content`
- `assets`
- `diagnostics`
- `export`
- `metadata`

注意：
- `content` 支持 `cursor` / `chunk_size` 分页
- 这是本地只读接口，不会触发远端抓取

#### `probe_archived_threads`

用于批量读取本地归档事实，不创建任务，不抓远端。

适用场景：
- 大批量归档前先判断哪些 `tid` 已有本地归档
- 想把本地尾部时间和远端 `last_reply_at` 对照

输出重点：
- `archived`
- `archive_status`
- `sync_time`
- `local_last_pid`
- `local_last_floor_no`
- `local_last_floor_pub_time`
- `local_last_reply_at`

### 任务创建

#### `create_thread_archive_job`

用于发起单贴归档任务。

适用场景：
- 用户要求“归档这个帖子”
- 用户要求“把这个帖子同步到本地”
- 用户要求“重新抓一次最新内容”

#### `create_thread_archive_batch_jobs`

用于一次性为多个帖子创建归档任务。

适用场景：
- 用户明确要求“批量归档”
- 你已经拿到一组 `tid`

#### `create_thread_update_job`

用于对轻小说贴子执行追加更新。

适用场景：
- `check_thread_updates` 结果表明有更新
- 用户要求“只补新增楼层，不要全量重抓”

#### `create_thread_export_job`

用于导出帖子归档结果。

适用场景：
- 用户要求导出 ZIP 或轻小说 TXT
- 用户要离线阅读

#### `create_rag_index_job`

用于为单个已归档帖子创建 RAG 索引任务。

#### `create_rag_index_batch_jobs`

用于一次性为多个已归档帖子创建 RAG 索引任务。

### 轻小说更新与本地检索

#### `check_thread_updates`

用于检查已归档的轻小说贴子是否有新内容。

适用场景：
- 用户问“这个轻小说贴子有没有更新”
- 想在追加更新前先做只读判断

注意：
- 这是只读操作，不创建任务
- 如果结果是 `updated`，再考虑 `create_thread_update_job`

#### `search_archived_content`

用于检索本地已归档内容。

适用场景：
- 用户要求“只在本地知识库里搜”
- 用户要 keyword / vector / hybrid 检索结果
- 用户明确不要远端抓取

调用建议：
- 默认使用 `hybrid`
- 如需限定范围，使用 `tid` / `forum_id`

### 任务状态与分区信息

#### `read_job`

用于查看后台任务主状态。

重点关注：
- `status`
- `stage`
- `execution_state`
- `diagnostic_summary`
- `needs_attention`
- `artifacts`

#### `wait_for_job`

用于阻塞等待后台任务到达终态。

适用场景：
- 用户要求“创建后顺便等结果”
- benchmark / 自动化流程需要在一个回合里等到终态

#### `read_job_events`

用于查看任务事件时间线。

适用场景：
- 任务失败后排障
- 任务长时间停留在 `running` / `retrying` / `interrupted`

#### `read_forum_profiles`

用于读取可用论坛分区、名称和内容类型提示。

适用场景：
- 想先确认 `forum_id`
- 搜索或浏览前先读分区配置

## Discussion Trend V1

Discussion Trend V1 是 PostgreSQL-only 的历史讨论分析能力面。

### 公开 Tools

任务创建：
- `create_discussion_trend_index_job`
- `create_discussion_trend_report_job`
- `create_forum_research_report_job`

只读查询：
- `get_discussion_partition_trends`
- `get_discussion_topic_trends`
- `get_discussion_user_trends`
- `get_discussion_topic_evidence`
- `get_forum_evidence_pack`
- `get_discussion_report`

### 适用场景

- 用户要看某个分区在一段时间内的活跃趋势
- 用户要看 topic / user 排名
- 用户要抽 topic 证据或论坛黑话 / 氛围证据
- 用户要拿 trend report 或 forum research report artifact

### 使用边界

- 仅 PostgreSQL 支持
- 同一 `(forum_id, start_date, end_date, version)` 可以重复重跑
- current run 由 `discussion_current_indexes` 维护
- `get_discussion_topic_evidence` 支持 `mode=auto|sql|rag`
- `get_forum_evidence_pack` 在 `require_current_run=false` 时，即使没有 current trend run 也可用于非趋势研究

### 推荐工作流

#### 1. 构建趋势底座

1. 调用 `create_discussion_trend_index_job`
2. 用 `read_job` 或 `wait_for_job` 等到终态

#### 2. 看趋势

1. `get_discussion_partition_trends`
2. `get_discussion_topic_trends`
3. `get_discussion_user_trends`

#### 3. 做 topic 调查

1. 先看 `get_discussion_topic_trends`
2. 再用 `get_discussion_topic_evidence`

#### 4. 做论坛研究

1. 直接用 `get_forum_evidence_pack`
2. 若需要持久化 artifact，再创建 `create_forum_research_report_job`

#### 5. 读取报告

1. 创建 `create_discussion_trend_report_job` 或 `create_forum_research_report_job`
2. 等待 job 完成
3. 用 `get_discussion_report` 读取 artifact

## Resources 的使用方式

Agent 工作流优先读取紧凑资源，按需深入。

### 推荐资源读取顺序

1. `yamibo://threads/{tid}/summary`：紧凑摘要
2. `yamibo://threads/{tid}/diagnostics`：归档状态、缺失资产、建议动作
3. `yamibo://threads/{tid}/update-check`：轻小说更新检测结果
4. `yamibo://threads/{tid}/posts`：有序内容块
5. `yamibo://threads/{tid}/assets`：图片/附件状态
6. `yamibo://threads/{tid}/context`：完整正文 Markdown
7. `yamibo://threads/{tid}/metadata`：完整元数据 JSON
8. `yamibo://jobs/{job_id}/status`：任务主状态快照
9. `yamibo://jobs/{job_id}/events`：任务事件时间线
10. `yamibo://guide/agent-workflows` / `yamibo://guide/archive-model` / `yamibo://guide/error-codes` / `yamibo://guide/agent-evaluation`
11. `yamibo://schema/tools`：当前工具参数签名

### 其他资源

- `yamibo://forums/index`
- `yamibo://forums/{forum_id}/summary`
- `yamibo://series/index`
- `yamibo://series/{series_id}/chapters`

使用原则：
- 先用 Tool 定位对象
- 再用 Resource 读取大内容
- 回答用户时做摘要，不机械转抄整段内容

## 推荐工作流

### 1. 查指定帖子

1. `search_forum_threads`
2. 从候选里选最相关项
3. 读 `yamibo://threads/{tid}/summary`
4. 按需继续读 `diagnostics -> posts -> context`

### 2. 按页浏览论坛

1. `browse_forum_page`
2. 从返回结果按标题、时间或发布者筛选
3. 按需转 `inspect_remote_thread` 或读取本地资源

### 3. 看指定帖子

1. 用户给出 `tid` 或 URL
2. `inspect_remote_thread`
3. 按需读取 `summary / diagnostics / posts / assets / context`

### 4. 归档帖子

1. `create_thread_archive_job`
2. 返回 `job_id`
3. 用 `read_job` 或 `wait_for_job` 追踪
4. 需要排障时读 `read_job_events` 或 `yamibo://jobs/{job_id}/events`

### 5. 轻小说更新

1. `check_thread_updates`
2. 读 `yamibo://threads/{tid}/update-check` 复核
3. 若 `updated`，再用 `create_thread_update_job`
4. 用 `read_job` 或 `wait_for_job` 追踪

### 6. 大批量归档前探测

1. `probe_archived_threads`
2. 结合远端列表页的 `last_reply_at` 判断是否需要补跑

### 7. 导出帖子

1. 确认帖子对象
2. `create_thread_export_job`
3. 返回 `job_id`
4. 用 `read_job` 或 `wait_for_job` 追踪

### 8. 诊断归档问题

1. 读 `yamibo://threads/{tid}/diagnostics`
2. 检查 `archive_status`、`missing_required_assets_count`、`warnings`、`next_actions`
3. 再决定是否补跑任务或读取事件

## 回答风格要求

- 先给结论，再给依据。
- 默认给出结构化关键信息：`tid`、标题、系列、章节、发布者、时间。
- 如果结果是候选集，说明“最相关 / 可能相关 / 待确认”。
- 如果任务仍在进行，不假装已经完成，要明确返回 `job_id` 和当前状态。
- 如果帖子内容较长，只总结重点，并提示可继续读取完整资源。
- 诊断信息优先从 `diagnostics` 资源获取，不读完整 `context.md`。
