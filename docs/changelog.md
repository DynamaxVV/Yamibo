# 百合会归档助手 — 版本更新日志

## v0.11.1 (2026-07-01)

### 新增
- **远端论坛实时浏览**：WebUI 新增 `/forum` 与 `/forum/:tid`，可直接浏览远端分区列表、帖子详情，并在远端视图里发起归档或重同步
- **统一阅读器组件**：新增 `ThreadReader`，本地帖子与远端帖子共用阅读视图、分页与图片展示逻辑
- **多主题扩展**：前端新增主题选择器与一批新主题实现，主题上下文和样式变量同步扩展

### 改进
- **任务列表短 TTL 缓存**：`/jobs` 相关接口增加 3 秒内存缓存，并在删除、重试、暂停、恢复等写操作后主动失效
- **设置页可直接维护标题提示词**：`common_scanlation_groups` / `common_authors` 改为从 `title_hints.json` 读取和写回
- **代理池延迟探测收敛**：节点延迟测试支持更短单次超时、忽略 5xx 失效节点，并在达到截止时间后提前停止等待慢节点
- **数据库连接后端判定修正**：传入与默认值相同的 SQLite 路径时，不再错误覆盖 `db_backend`

### 修复
- **标题提示词写入范围收紧**：仅漫画区(30)和轻小说区(55)在同步时回写标题提示词，避免无关分区污染 hints
- **远端帖子标题解析更稳**：主题标题提取时忽略 `script` / `style`，不再依赖旧的 `ignore_js_op` 判断

## v0.11.0 (2026-06-29)

### 架构重构
- **Web API 路由拆分**：`web/api.py` (2166 行 → 184 行) 拆为 `web/routes/` 下 10 个路由模块，每模块单一职责
- **解析器模块化**：`parsers/thread_detail.py` (972 行) 拆分为 `_html_utils.py` + `_post_extract.py` 辅助模块
- **前端 CSS 拆分**：`styles.css` (1738 行) 按页面拆为 `reading.css` + `confirm.css` + `settings.css`
- **配置去中心化**：`MihomoProxyPoolConfig` 移至 `yamibo/proxy_pool.py`，含 `from_config_section()` 工厂方法
- **Domain 净化**：`render_context_by_profile` 从 `domain/content.py` 移至 `storage/markdown.py`
- **py.typed**：添加 PEP 561 类型标记

### 文档
- **CLI 优先**：所有文档以 CLI 命令为主要推荐方式，MCP 为辅助通道
- **PostgreSQL 优先**：数据库描述统一为 PostgreSQL-primary，SQLite-secondary
- **MCP 指南补全**：创建 `docs/mcp-guides/` 下 4 个指南文件（agent-workflows / archive-model / error-codes / agent-evaluation）
- 合并 `postgres-schema-decisions.md` 到 `database-design.md`
- 删除失效文档引用（pg-shadow-validation-report / phase6-7-status-summary / postgres-migration-runbook）
- README、user-manual、agent-interface 等全面更新至 v0.11.0

## v0.10.1 (2026-06-29)

### 新增
- **Mihomo 代理池**：支持 thread 级 best-effort 代理绑定，通过 mihomo controller API 自动发现节点、并行测延迟、按 tid 哈希稳定选择
- **Cookie 定时刷新**：`yamibo.cookie_refresh_interval_hours`（默认 12h），到期自动删除 cookie 文件触发重新登录
- **代理池健康检查**：`uv run yamibo-mcp-server check-proxy-pool` 一键诊断 controller 连通性、节点延迟、过滤规则效果
- **节点过滤规则**：`allowed_patterns` / `denied_patterns` / `max_delay_ms` 正则过滤和延迟上限

### 改进
- `test_node_delay` 修复：从传 group 名改为传节点名，使每个节点的延迟测试独立
- 节点延迟测试从串行改为 `ThreadPoolExecutor` 并行（最多 8 线程）
- 添加 30 秒内存缓存减少重复 discover + delay 测试

### 约束
- 默认关闭，不改变现有行为
- stdlib only，无新依赖
- 不改公开 MCP/CLI 契约
- 不持久化代理状态到数据库

## v0.9.3 (2026-06-26)

### 调整
- 帖子归档的图片下载改成帖子内小并发，保留每个 cookie 5 槽的全局限制和单图超时/重试
- 归档、RAG、任务管理和 WebUI 的既有改造继续收口到当前主线，文档与测试同步整理
- README、API、部署、开发、Agent 和产品文档版本统一推进至 `0.9.3`

### 测试
- 图片下载、同步任务和更新任务相关回归测试继续覆盖顺序回填、部分成功和超时场景

## v0.9.2 (2026-06-25)

### 调整
- 删掉 `server/app.py`、`server/legacy_protocol.py`、`server/legacy_tools.py`、`application/*_use_cases.py` 等兼容壳层，主路径收敛到 `cli.py + mcp_registry.py + agent_tools.py`
- `mcp_registry.py` 的 resource 注册改成表驱动循环，减少重复闭包
- README、API、部署、开发和 Agent 文档同步更新到 `0.9.2`

### 调试与稳定性
- 新增 `rag.debug_indexing` / `YAMIBO_RAG_DEBUG_INDEXING`，RAG 索引失败时可在 daemon 终端输出 embedding 请求与响应摘要
- `sync_forum_range`、`list_exports` 等仍保留的 CLI 能力已并回主模块，不再依赖单独兼容层

### 测试
- 删掉给兼容层续命的旧测试
- 解析器测试不再依赖外部 `html_sample/` 或本地 `data/threads/*` 运行态数据，改为仓库内 fixture 与内联样例

## v0.9.0 (2026-06-24)

### 新增
- 新增批量归档与批量 RAG 索引接口，支持一次提交多个 `tid`
- 新增 WebUI `/rag` 管理页，用于查看索引覆盖、创建索引任务和调试本地检索
- 新增任务暂停 / 恢复操作，任务列表按运行中、排队中、已完成排序

### 调整
- README、Agent 文档和各类使用文档同步更新到 `0.9.0`
- 版本号统一推进至 `0.9.0`

## v0.8.1 (2026-06-23)

### 调整
- 旧的 `yamibo-forum` skill 收口为弃用别名，统一引导到 `yamibo-mcp`
- Hermes MCP 导入切换到 `streamable-http` 的 `/mcp` 入口
- `wait_for_job` 与 `yamibo://jobs/{job_id}/status` 同步进 Skill / Agent 文档
- 版本号统一推进至 `0.8.1`

## v0.8.0 (2026-06-22)

### 新增
- 新增 Hermes 基准回归 harness：`scripts/run_hermes_benchmark.sh` / `scripts/run_hermes_benchmark.py`
- 新增 `yamibo_mcp.benchmark.hermes` transcript 解析与任务卡打分逻辑，可自动导出 session transcript、生成 Markdown/JSON 报告
- 新增多论坛只读、多线程 fanout、幂等复用、导出策略分流等 7 张标准 Hermes 任务卡

### 调整
- `read_job` 状态面新增 `execution_state`、`diagnostic_summary`、`needs_attention`、运行时长与最近更新时间
- 长时间 `running`、长时间 `queued`、`interrupted` 和图片下载阶段过慢的任务现在有统一的自动诊断摘要
- 文档版本统一推进至 `0.8.0`

## v0.7.1 (2026-06-22)

### 调整
- 数据库设计文档与实际 schema 对齐，修正 `jobs.tid` 类型并补充 `threads.category`
- dist 里的 skill 说明和 Agent 默认提示词同步到当前接口命名与工作流资源
- 版本号推进至 `0.7.1`

## v0.7.0 (2026-06-22)

### 新增
- 新增中文 `Agent 架构导航` 文档，明确 AI 编码代理的修改入口、legacy 禁区和测试矩阵
- 在 README 项目结构中补充 `frontend/` 前端源码目录，以及 `web/static` 为构建产物的说明
- 为 `web/static` 添加说明文件，并把构建产物作为 package data 纳入 wheel/sdist

### 调整
- 版本号推进至 `0.7.0`
- 收敛入口与壳层，后续主路径只保留 `cli.py + mcp_registry.py + agent_tools.py`
- 开发文档和 API 文档统一对齐当前 Agent-facing 结构

### 修复
- 修复打包产物未显式包含 `yamibo_mcp.web.static/*` 的问题，避免安装包内 Web 静态资源缺失

## v0.6.0 (2026-06-21)

### 新增
- **轻小说更新检测**：新增 `check_thread_updates` 只读接口，专门检查已归档轻小说帖是否有新内容
- **轻小说追加更新**：新增 `update_thread` 任务，可在更新检测通过后仅拉取新增楼层并追加到现有归档
- **轻小说 TXT 导出**：轻小说帖导出改为独立 TXT 文件，支持增量追加，输出目录可单独配置
- **富文本阅读还原**：轻小说/译文贴预览保留颜色、加粗、斜体、链接等富文本语义，导出仍保持纯文本
- **更新检查资源**：新增 `yamibo://threads/{tid}/update-check` 只读资源，方便 WebUI / MCP / CLI 统一读取检测结果

### 修复
- 轻小说阅读预览首行缩进、段落排版和图片展示问题
- 归档时过滤编辑提示、非正文链接和无关装饰性富文本
- 轻小说贴子板块识别误判问题，按实际面包屑和论坛路径优先判定
- 部分成功任务在详情页与列表页的原因展示不足问题

### UI/UX 改进
- 贴子详情信息编辑支持标题，并让标题单独占一行
- 任务详情支持部分成功明细折叠展示
- 阅读模式、字体大小和移动端预览布局优化
- 任务、贴子、系列列表继续保留可直接跳转页码的分页交互

### 后端
- 追加更新任务使用只看楼主 URL、尾页指纹和页数快照做增量判断
- 新增轻小说 TXT 导出目录与更新检测相关配置项

### 文档
- 版本号推进至 0.6.0

## v0.5.0 (2026-06-21)

### 新增
- **按发帖日期搜索**：`search_threads` 新增 `posted_on` 参数，使用指数跳转+二分查找策略高效定位目标日期页，与 `start_page`/`end_page` 互斥
- **`browse_forum_page` 按发帖时间排序**：`order="dateline"` 按发帖时间排序并返回 `total_pages`，默认 `order="default"` 按最后回复排序
- **任务管理**：支持单个任务删除和批量清除已成功任务，运行中任务禁止删除
- **贴子详情编辑表单**：漫画/轻小说帖子支持在线编辑章节号、章节名、作者、汉化组/译者
- **贴子详情作者/汉化组显示**：归档信息栏新增作者和汉化组（漫画）/译者（轻小说）字段
- **系列详情合并功能**：系列详情页支持输入目标系列 ID 进行合并，带二次确认
- **系列详情编辑功能**：支持编辑系列键和作者名
- **客户端分页**：帖子列表、系列列表、任务列表新增分页器（每页 50 条）
- **筛选状态记忆**：帖子/系列列表的搜索、筛选、排序状态通过 sessionStorage 持久化
- **帖子列表同步状态筛选**：新增同步状态过滤（全部/同步完成/部分同步/同步过期/未同步）
- **帖子列表回复数排序**：回复数列支持点击排序
- **阅读视图字体缩放**：新增 75%/100%/125%/150%/200% 字体大小控制
- **引用/回复解析**：解析 Discuz 引用 HTML，帖子详情展示引用文本和回复文本分离
- **请求限流**：可配置 `request_interval_seconds`（默认 1.0s）+ `request_interval_jitter_seconds`（默认 0.5s）防止论坛限流
- **导出限制**：仅漫画区和轻小说区帖子支持导出，前端隐藏按钮，后端返回错误
- **非漫画/轻小说帖子跳过 LLM 标题解析**：节省 API 调用，直接使用原始标题
- **帖子详情系列名称**：自动关联系列表显示系列标题，无需额外 API 调用

### 修复
- CLI `search-threads --posted-on` 参数传递语法错误
- 帖子详情 `chapter_name`/`chapter_index` 未从 `title_parse` 表关联显示
- 编辑按钮字体过大（14→11），与 h2 标签大小一致
- 章节编辑表单在非漫画/无内容块时不可见（条件渲染作用域问题）

### UI/UX 改进
- Dashboard 分区展示：主要版块（漫画+轻小说）和其他版块分区，独立数量选择器
- 任务页面重构：显示任务描述列，支持单个/批量删除
- 日志页面布局优化：筛选控件换行排列
- 版块标签筛选器改为药丸按钮组
- 行操作按钮高度统一（26px）
- 复核页面表格居中优化，相似系列芯片独立行显示
- 输入+按钮组合控件（`.input-btn-group`）
- 移动端全面适配：响应式布局、列隐藏、字体调整、触摸滚动
- 归档信息属性显示顺序调整：标签→图片数→内容类型→归档状态→校验状态

### 后端
- `fetch_forum_threads_dateline()` 按发帖时间获取论坛帖子列表
- `_dateline_search()` 指数跳转+二分查找算法
- `extract_total_pages()` 解析 Discuz 分页总数
- `dateline_forum_page_url()` 生成按发帖时间排序的论坛 URL
- `FloorSnapshot` 新增 `quote_text`/`reply_text` 字段
- `floors` 表新增 `quote_text`/`reply_text` 列
- `_update_chapter` 支持更新 `author_guess`/`group_name`
- `_thread_detail` 返回 `group_name`/`author_guess`/`series_title`

### 文档
- 版本号推进至 0.5.0

---

## v0.4.0 (2026-06-21)

### 新增
- **国际化 (i18n)**：中英文双语支持，~170 个翻译键，语言切换按钮（中/En），localStorage 持久化
- **暗黑模式**：4 套主题各含独立暗色配色，浮动按钮切换（☾/☀），localStorage 持久化
- **论坛版块自动识别**：从帖子 HTML 提取论坛链接和 typeid 分类标签（`forum.php?fid=XX&filter=typeid&typeid=YY`），支持 11 个论坛、100+ typeid 映射
- **扩展论坛注册**：从 4 个论坛扩展到 11 个（新增贴图区/管理版/资源交流區/遊戲區/文學區/使用指南/影視區），每个论坛含中文名和英文名
- **论坛级系列归类**：非漫画/轻小说论坛的帖子自动归入论坛级系列（如"动漫区全部"），通过 `resolve_for_forum()` 实现
- **`browse_forum_page` 新增 `order` 参数**：`order="dateline"` 按发帖时间排序，返回 `total_pages`；默认 `order="default"` 按最后回复排序
- **分类标签 (category)**：从帖子 HTML 提取子分类（如 [長篇連載]、[短篇完結]），存入 `threads.category` 列，帖子列表新增 Category 列
- **多关键词搜索**：后端 AND 逻辑（空格分隔关键词），FTS 用 AND 连接，LIKE fallback 按关键词交叉匹配；系列页前端同步实现
- **版块标签筛选器**：帖子列表页版块筛选从下拉框改为标签/药丸按钮，显示各版块帖子数，0 贴版块隐藏
- **日志级别筛选**：日志页新增最低级别过滤（DEBUG/INFO/WARNING/ERROR/CRITICAL），支持暂停计数
- **Dashboard 分区展示**：统计行显示各版块帖子数，最近归档按"漫画·轻小说"和"其他板块"分区展示，各含独立数量选择器
- **自动删除空系列**：删除帖子后若系列无剩余帖子，自动删除该系列并返回 `deleted_series_id`
- **forum_id 透传到后端**：重新同步/导出帖子时传递 forum_id，确保后端任务使用正确的论坛
- **品牌 Logo 和 Favicon**：YamiboArchiveLogo.png → 多尺寸 favicon + apple-touch-icon
- **主题英文标签**：Theme 接口新增 `labelEn` 字段，英文模式下显示英文主题名

### 修复
- `thread_summary_payload()` 缺少 `forum_id`、`content_kind`、`category` 字段 — 已补齐
- `_thread_summary_dict()` 缺少 `category` 返回 — 已补齐
- 贴子详情返回导航：从系列进入点击"返回"现在正确回到 `/series/{id}` 而非 `/series`
- `_forums_list()` 中 `name_en` 访问未做安全检查 — 已加 fallback

### UI/UX 改进
- 表格默认居中对齐（`th, td { text-align: center }`），标题列通过 `.truncate` 保持左对齐
- Layout 顶栏重构：新增语言切换按钮、品牌 Logo 图片
- 复核页面区域顺序调整：系列复核移至标题复核上方
- 帖子详情归档信息表新增 Category 行
- Dashboard 统计行显示各版块帖子数明细
- 帖子列表 `tableLayout: 'fixed'` 防止筛选时列宽跳动
- Badge 颜色改用 CSS 变量（`--badge-*-text`），暗黑模式自适应
- 内容块表和图片宽度工具栏仅对漫画类型帖子显示

### 重构
- 新增 `utils/time.ts` 共享时间格式化函数（`formatDateTime`、`formatLogTime`），消除页面间重复代码
- `ContentBadge` 组件改用 `useI18n()` 翻译内容类型标签

### 文档
- 版本号推进至 0.4.0

---

## v0.3.0 (2026-06-21)

### 新增
- **React + Vite WebUI**：从服务端渲染 Python HTML 迁移为 React SPA
- **4 套可切换主题**：极简专业 / 理性数据 / 粗野 / 复古 1957
- **贴子详情两栏阅读布局**：左侧 30% 楼层元数据，右侧 70% 内容（文字 + 图片）
- **贴子详情图片宽度控制**：50% / 75% / 100% 三档，默认 75%
- **复核页面内联编辑**：标题和系列编辑展开在行下方，带二次确认对话框（修改前后对比）
- **合并系列推荐**：自动查找相似系列，显示为可点击标签
- **贴子列表排序**：发布时间和同步时间列支持点击升序/降序
- **贴子列表筛选**：版块下拉 + 日期范围（一天/三天/一周/一月/三月）
- **发布者搜索**：贴子搜索支持按发布者名称搜索
- **内容类型彩色 badge**：漫画蓝 / 小说绿 / 讨论橙 / 混合紫
- **实时日志页面**：2 秒轮询，暂停/继续/清空，自动滚动
- **版块页面**：显示所有分区，点击查看对应贴子
- **系列详情操作**：删除空系列（二次确认）
- **贴子详情操作**：重新同步、导出、删除（二次确认），原贴网址显示
- **返回按钮上下文感知**：从系列进入显示"返回系列列表"
- **发布者样式**：楼层发布者加粗 + 强调色
- **Space Grotesk 字体**：本地 woff2 加载

### 后端
- 新增 `web/api.py`：JSON API 层（30+ 端点）
- 新增 `web/log_buffer.py`：环形日志缓冲区
- `threads` 查询新增 `pub_time` 和 `publisher` 搜索支持
- `threads` 表新增 `forum_id`、`content_kind`、`primary_media_type` 列
- 新增 `forums`、`content_blocks`、`assets`、`job_events` 表
- `JobsRepository` 状态变更自动追加事件

### 文档
- 新增 `docs/webui-design.md` WebUI 设计文档
- 项目名更改为"百合会归档助手"

---

## v0.2.0 (2026-06-20)

### 新增
- 多分区支持：漫画区(30)、轻小说区(55)、动漫区(5)、水区(33)
- `forum_id` 参数：`browse_forum_page`、`search_threads`、`sync_forum_range`
- `ForumProfile` 领域模型
- 内容类型模型：`ContentBlock`、`AssetSnapshot`、`PostSnapshot`
- `classify_content_kind`、`validate_by_profile`
- `JobEventsRepository`：任务事件 outbox
- Agent Resources：summary / diagnostics / posts / assets / job events
- 应用层：`ensure_thread`、`archive_thread_job`、`get_job_status_payload`

---

## v0.1.0 (2026-06-19)

### 新增
- MCP Server（FastMCP stdio/SSE/HTTP）
- Daemon 后台任务消费 + 嵌入式 Web 控制台
- 智能标题解析（规则引擎 + LLM 辅助）
- 自动归档（HTML 解析 + 图片下载 + 结构化存档）
- 系列管理（自动聚合 + 合并 + 复核）
- 标准化导出（ZIP 打包）
- CLI 命令行工具
- 429 个测试用例
