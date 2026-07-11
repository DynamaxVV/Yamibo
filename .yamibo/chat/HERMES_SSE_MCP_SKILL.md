---
name: yamibo-hermes-sse
description: 面向 Hermes 使用的 Yamibo SSE MCP 使用手册。适用于已通过 SSE 连接到 Yamibo MCP 的场景，帮助你使用常用工具、理解常见流程、处理错误并提升回答质量。用户提到 Hermes、SSE、MCP、yamibo-mcp、百合会论坛检索、归档、更新、导出、本地检索、任务状态、趋势分析、topic evidence、forum evidence、report 时使用。
---

# Yamibo Hermes SSE MCP Skill

把 Yamibo 当作“百合会论坛检索、归档、任务状态读取、本地内容检索和历史讨论分析接口”来使用。你已经通过 SSE 连上了 MCP，因此这里专注于**怎么使用**，不包含连接方式、Docker 拉起方式或部署步骤。

本 skill 描述 1.0 已发布的 MCP tool/resource 契约。Yamibo 的 WebUI Chat 当前不内建 tool loop、审批、citation 或 Agent run；若 Hermes 能调用本 MCP，是 Hermes 侧已经完成了连接和编排。后续运行时规划不改变本 skill 的当前调用边界。

## 基本原则

- 先用工具取事实，再组织回答。
- 能返回结构化信息时，优先返回结构化字段。
- 先短后长：先读摘要，再按需深入到正文、资源或事件。
- 长内容优先通过 Resource 读取，不要一次性把整篇正文全塞给用户。
- 任务类操作优先返回 `job_id`，需要等待时再用等待类工具。
- 批量操作优先使用批量接口，不要用单帖接口循环模拟批量。
- Discussion Trend V1 仅支持 PostgreSQL；如果当前环境不是 PostgreSQL，不要承诺趋势查询、topic evidence、forum evidence pack 或 report artifact 一定可用。
- 所有公共 tool 都返回 `ok/data/error/resources/next_actions/warnings/side_effects`；先根据这些字段判断事实和后续动作，再生成自然语言回答。
- `side_effects` 非空或工具说明标记为 job 创建时，只能说“已创建/复用了任务”，不能说“已经归档/导出完成”。

## 工具 / 资源边界

- **Tool** 负责结构化操作：远端只读查询、创建后台 job、轮询 job、按参数读取本地归档视图、按游标分页读取大文本。
- **Resource** 负责稳定 URI 下的大文本、文件和只读快照：归档正文、帖子列表、诊断、资产、导出包、job status/events、guide 和 schema。
- **Resource** 只读取，不创建 job，不触发远端抓取。
- 如果要回答用户“有没有、是什么、哪里不对”，优先用结构化资源；如果要回答“请继续深入看”，再继续读更深层资源。
- 不确定工具参数或能力面时，先读取 `yamibo://schema/tools` 和 guide resources，不要猜测未公开 capability。

## 常用工具

### 远端检索与预览

#### `search_forum_threads`
用于按关键词搜索论坛帖子。

适用场景：
- 用户只记得作品名、章节名、作者名、汉化组名或标题片段
- 用户说“帮我找某部漫画/小说”
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
- 用户想按最近帖子筛选

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
- 自动化流程需要在一个回合里等到终态

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

## 常用命令与等价入口

以下命令只作为“使用参考”和“脚本等价入口”展示，不需要在 Hermes 中重新启动服务。

```bash
# 搜索帖子
uv run yamibo-archiver search-threads --query "星灵感应"

# 浏览论坛页
uv run yamibo-archiver browse-forum-page --page 1 --forum-id 30

# 只读预览远端帖子
uv run yamibo-archiver inspect-remote-thread --tid 572313

# 创建单帖归档任务
uv run yamibo-archiver create-thread-archive-job --tid 572313

# 批量归档
uv run yamibo-archiver create-sync-thread-batch-jobs --tid 572313 --tid 572314

# 检查轻小说更新
uv run yamibo-archiver check-thread-updates --tid 544422

# 创建轻小说追加更新任务
uv run yamibo-archiver update-thread --tid 544422

# 创建导出任务
uv run yamibo-archiver create-export-thread-job --tid 572313

# 构建单帖 RAG 索引
uv run yamibo-archiver create-rag-index-job --tid 572313

# 批量构建 RAG 索引
uv run yamibo-archiver create-rag-index-batch-jobs --tid 572313 --tid 572314

# 检索本地归档内容
uv run yamibo-archiver search-archived-content --query "星空 告白" --mode hybrid --top-k 5

# 读取任务状态
uv run yamibo-archiver job-status <job_id>

# 等待任务完成
uv run yamibo-archiver wait-for-job <job_id>

# 读取任务事件
uv run yamibo-archiver read-job-events <job_id>
```

## 常见场景示例

### 场景 1：用户问“帮我找《星灵感应》”

建议流程：
1. `search_forum_threads` 先找候选
2. 选最像的一条或几条
3. 读 `summary`
4. 如果用户要内容，再继续 `context` 或 `inspect_remote_thread`

### 场景 2：用户问“把这个 tid 归档下来”

建议流程：
1. `create_thread_archive_job`
2. 返回 `job_id`
3. 如果用户要求“顺便看结果”，继续 `wait_for_job`
4. 失败时读 `read_job_events`

### 场景 3：用户问“这个轻小说有没有更新”

建议流程：
1. `check_thread_updates`
2. 如果有更新，创建 `create_thread_update_job`
3. 之后可再读 `summary` 或 `context`

### 场景 4：用户问“只在本地库里搜一下这个词”

建议流程：
1. `search_archived_content`
2. 默认 `hybrid`
3. 如果用户要范围更窄，就加 `tid` / `forum_id`

### 场景 5：用户问“这个任务怎么失败了”

建议流程：
1. `read_job`
2. 重点看 `status`、`stage`、`diagnostic_summary`
3. 再看 `read_job_events`
4. 如果涉及资源/正文，再读对应 `diagnostics` 或 `context`

### 场景 6：用户问“做一份某个分区的趋势报告”

建议流程：
1. `create_discussion_trend_index_job`
2. 等待完成
3. `create_discussion_trend_report_job`
4. 等待完成
5. `get_discussion_report`

## 错误处理

### 1. 找不到帖子
表现：
- 搜索结果为空
- 远端预览返回未找到

处理：
- 换关键词
- 换标题别名、繁简、空格、标点
- 先用 `browse_forum_page` 从列表页筛

### 2. 本地没有归档
表现：
- `summary` / `context` / `assets` 为空或返回缺失

处理：
- 如果用户想读本地内容，先 `ensure_thread_archived`
- 如果用户只想看远端，改用 `inspect_remote_thread`

### 3. 归档任务失败
表现：
- `read_job` 显示失败
- `needs_attention=true`

处理：
1. 读 `read_job_events`
2. 看失败阶段
3. 检查是否是网络、反爬、帖子不存在、权限不足、资源写入失败
4. 仅当错误可恢复且没有 live job 时，才重新创建任务

### 4. 轻小说更新检查没有结果
表现：
- `check_thread_updates` 返回无更新或不确定

处理：
- 先确认该帖是否属于轻小说更新流
- 只在用户明确需要时才做追加更新
- 避免把“无更新”误说成“已更新”

### 5. 本地检索结果不理想
表现：
- `search_archived_content` 命中不准

处理：
- 改检索模式：`hybrid` / `keyword` / `vector`
- 缩小范围到 `tid`、`forum_id`、楼层区间
- 先看 `summary` 再深入 `context`

### 6. Discussion Trend V1 不可用
表现：
- 当前后端不是 PostgreSQL
- 相关查询报错或空结果

处理：
- 不承诺趋势能力可用
- 只给普通搜索 / 归档 / 导出 / 更新能力

## 回答风格要求

- 先给结论，再给依据。
- 默认给出结构化关键信息：`tid`、标题、系列、章节、发布者、时间。
- 如果结果是候选集，说明“最相关 / 可能相关 / 待确认”。
- 如果任务仍在进行，不假装已经完成，要明确返回 `job_id` 和当前状态。
- 如果帖子内容较长，只总结重点，并提示可继续读取完整资源。
- 诊断信息优先从 `diagnostics` 资源获取，不要直接读完整正文。

## 最后提醒

- Hermes 已经通过 SSE 连上 MCP 后，你只需要把它当成一个工具集来用。
- 不要把“连接”、“启动”、“部署”写进用户日常使用回答里。
- 重点是：找、看、归档、更新、导出、检索、查任务、做趋势分析。
- 未来 capability manifest、受控 tool loop、approval、citation 和 run/step trace 的设计见仓库 `docs/llm-native-runtime-roadmap.md`；不要把它们当作当前 MCP 返回字段或工具。
