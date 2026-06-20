# 百合会归档助手 — 版本更新日志

## v0.4.0 (2026-06-21)

### 新增
- **国际化 (i18n)**：中英文双语支持，~170 个翻译键，语言切换按钮（中/En），localStorage 持久化
- **暗黑模式**：4 套主题各含独立暗色配色，浮动按钮切换（☾/☀），localStorage 持久化
- **论坛版块自动识别**：从帖子 HTML 提取论坛链接和 typeid 分类标签（`forum.php?fid=XX&filter=typeid&typeid=YY`），支持 11 个论坛、100+ typeid 映射
- **扩展论坛注册**：从 4 个论坛扩展到 11 个（新增贴图区/管理版/资源交流區/遊戲區/文學區/使用指南/影視區），每个论坛含中文名和英文名
- **论坛级系列归类**：非漫画/轻小说论坛的帖子自动归入论坛级系列（如"动漫区全部"），通过 `resolve_for_forum()` 实现
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
