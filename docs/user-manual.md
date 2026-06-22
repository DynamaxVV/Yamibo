# 用户操作手册

> 版本：0.7.0 | 更新日期：2026-06-22

## 1. 快速开始

### 1.1 首次安装

```bash
# 1. 克隆项目
git clone <repo-url> yamibo && cd yamibo

# 2. 安装依赖
uv sync --extra dev

# 3. 初始化数据库
uv run yamibo-init-db

# 4. 配置 Cookie（在浏览器登录 bbs.yamibo.com 后复制 Cookie）
#    创建 .cookie 文件，写入 cookie 内容

# 5. 启动 Daemon（后台任务消费 + Web 控制台）
uv run yamibo-daemon
```

### 1.2 验证安装

```bash
# 浏览论坛第 1 页
uv run yamibo-mcp-server browse-forum-page --page 1

# 读取论坛分区列表
uv run yamibo-mcp-server read-resource "yamibo://forums/index"
```

---

## 2. 通过 MCP 客户端使用

### 2.1 浏览论坛

在 LLM 客户端中，可以直接要求：

> "帮我看一下百合会漫画区第 1 页有什么帖子"

MCP Server 会调用 `browse_forum_page` 工具返回帖子列表。

### 2.2 搜索帖子

> "搜索一下有没有 '星灵感应' 相关的帖子"

MCP Server 会调用 `search_forum_threads` 工具，优先从论坛搜索，失败时降级到本地搜索。

### 2.3 归档帖子

> "帮我归档帖子 572313"

MCP Server 会调用 `create_thread_archive_job` 创建后台任务。返回 job_id 后，可以通过 `read_job` 轮询进度。

如果需要先确认本地是否已有归档，可调用 `ensure_thread_archived`；读取内容则使用 `read_archived_thread`。

### 2.4 导出帖子

> "把帖子 572313 导出成 ZIP"

MCP Server 会调用 `create_thread_export_job` 创建导出任务。支持三种策略：
- **cache_only**（默认）：仅使用本地已有数据
- **sync_if_stale**：本地数据过旧时先同步
- **force_resync**：强制重新同步

轻小说帖子会导出为独立 TXT 文件，默认输出到 `data/novel_exports`，可通过 `YAMIBO_NOVEL_TXT_EXPORT_DIR` 修改。

### 2.5 检查轻小说更新

> "检查一下帖子 544422 是否有更新"

MCP Server 会调用 `check_thread_updates` 只读接口，先比较本地归档快照和远端只看楼主页面。

如果结果显示有更新，再调用 `create_thread_update_job` 创建追加更新任务。

### 2.6 批量归档

> "帮我把漫画区前 5 页的帖子都归档"

MCP Server 会调用 `sync_forum_range` 创建批量同步任务。

### 2.7 读取归档内容

MCP 资源可以通过 URI 访问：

**帖子资源**：
- `yamibo://threads/{tid}/summary` — 帖子紧凑摘要（推荐 Agent 首选）
- `yamibo://threads/{tid}/diagnostics` — 诊断信息（缺失资产、建议操作）
- `yamibo://threads/{tid}/posts` — 内容块列表
- `yamibo://threads/{tid}/assets` — 资产列表
- `yamibo://threads/{tid}/update-check` — 轻小说更新检测结果
- `yamibo://threads/{tid}/context` — 帖子正文 Markdown
- `yamibo://threads/{tid}/metadata` — 完整元数据 JSON
- `yamibo://threads/{tid}/export` — 导出 ZIP 包

**论坛资源**：
- `yamibo://forums/index` — 论坛分区列表
- `yamibo://forums/{forum_id}/summary` — 分区摘要

**任务资源**：
- `yamibo://jobs/{job_id}/events` — 任务事件时间线

**系列资源**：
- `yamibo://series/index` — 系列索引
- `yamibo://series/{series_id}/chapters` — 系列章节列表

### 2.7 多论坛分区

支持浏览和搜索不同论坛分区：

> "帮我看一下轻小说区第 1 页有什么帖子"

MCP Server 会调用 `browse_forum_page(page=1, forum_id=55)`。

支持的分区：漫画区(30)、轻小说区(55)、动漫区(5)、水区(33)。

---

## 3. 通过 Web 控制台使用

启动 Daemon 后，访问 `http://127.0.0.1:8765/`。

### 3.1 Dashboard

首页展示：
- 帖子总数、系列数、导出数
- 最近任务列表
- Daemon 心跳状态
- 最近审计事件

### 3.2 任务管理

**查看任务**：点击导航栏「任务」，可按状态筛选（排队中/运行中/成功/失败/中断）。

**创建同步任务**：在任务页面输入 tid 或 URL，点击「创建同步任务」。

**查看任务详情**：点击任务 ID，可查看：
- 任务状态、阶段、进度
- 错误信息（如有）
- Staging 文件（snapshot.json、failure.json、title_parse_log.json）

### 3.3 帖子管理

**查看帖子**：点击导航栏「帖子归档」，可搜索帖子标题。

**帖子详情**：点击帖子标题，可查看：
- 基本信息（标题、发布者、时间）
- 标题解析结果（汉化组、作者、漫画名、章节）
- 楼层列表
- 阅读预览（合并正文和图片）
- 图片预览
- Metadata 和 Context 预览

**操作**：
- 重新同步
- 重新同步并导出
- 创建导出任务
- 删除归档

### 3.4 系列管理

**查看系列**：点击导航栏「作品系列」。

**系列详情**：点击系列标题，可查看：
- 系列信息（规范标题、作者、别名）
- 关联帖子列表（按章节排序）

**操作**：
- 确认系列（清除 needs_review 标记）
- 合并系列（将源系列帖子重定向到目标系列）
- 删除系列（仅限空系列）

### 3.5 标题复核

点击导航栏「标题复核」，可查看所有需要人工确认的标题解析结果。

**操作**：
- 手动修正标题字段（汉化组、作者、核心标题、章节等）
- 保存并确认
- 直接确认（使用当前解析结果）

### 3.6 导出管理

点击导航栏「导出」，可查看所有已导出的帖子。

### 3.7 语言切换

页面右上角可切换中文/英文界面。

---

## 4. 通过 CLI 使用

所有 CLI 命令返回 JSON 格式结果，方便脚本处理。

```bash
# 浏览论坛
uv run yamibo-mcp-server browse-forum-page --page 1

# 按发帖时间排序浏览
uv run yamibo-mcp-server browse-forum-page --page 1 --order dateline

# 浏览轻小说区
uv run yamibo-mcp-server browse-forum-page --page 1 --forum-id 55

# 搜索
uv run yamibo-mcp-server search-threads --query "关键词"

# 创建归档任务
uv run yamibo-mcp-server create-sync-thread-job --tid 572313

# 创建导出任务
uv run yamibo-mcp-server create-export-thread-job --tid 572313

# 批量同步
uv run yamibo-mcp-server create-sync-forum-range-jobs --start-page 1 --end-page 5

# 查看任务状态
uv run yamibo-mcp-server job-status <job_id>

# 列出导出包
uv run yamibo-mcp-server list-exports

# 读取资源
uv run yamibo-mcp-server read-resource "yamibo://threads/572313/summary"
uv run yamibo-mcp-server read-resource "yamibo://threads/572313/diagnostics"
uv run yamibo-mcp-server read-resource "yamibo://forums/index"
```

---

## 5. 典型工作流

### 5.1 单帖子归档并导出

```
1. get-thread --tid 572313          → 获取帖子详情（自动归档）
2. export-thread --tid 572313       → 创建导出任务
3. job-status <job_id>              → 等待导出完成
4. 在 data/exports/ 中找到 ZIP 包
```

### 5.2 批量归档漫画区

```
1. create-sync-forum-range-jobs --start-page 1 --end-page 10
                                     → 批量创建同步任务
2. 启动 Daemon                       → 自动消费任务
3. 在 Web 控制台监控进度
4. 所有任务完成后，批量导出
```

### 5.3 标题修正工作流

```
1. Web 控制台 → 标题复核
2. 查看待复核列表
3. 手动修正解析错误
4. 点击「保存并确认」
5. 如需合并系列：输入目标系列 ID，点击「合并系列」
```
