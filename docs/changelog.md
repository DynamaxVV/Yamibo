# 百合会归档助手 — 版本更新日志

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
