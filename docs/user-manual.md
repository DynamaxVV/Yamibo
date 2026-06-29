# 用户操作手册

> 版本：0.11.0 | 更新日期：2026-06-29

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

## 2. CLI 使用（推荐）

CLI 是主要操作方式，所有命令返回 JSON 格式结果，方便脚本处理。

### 2.1 浏览与搜索

```bash
# 浏览论坛
uv run yamibo-mcp-server browse-forum-page --page 1
uv run yamibo-mcp-server browse-forum-page --page 1 --forum-id 55
uv run yamibo-mcp-server browse-forum-page --page 1 --order dateline

# 搜索帖子
uv run yamibo-mcp-server search-threads --query "星灵感应"
```

### 2.2 归档与更新

```bash
# 创建归档任务
uv run yamibo-mcp-server create-thread-archive-job --tid 572313

# 批量归档
uv run yamibo-mcp-server create-sync-thread-batch-jobs --tid 572313 --tid 572314
uv run yamibo-mcp-server create-sync-forum-range-jobs --start-page 1 --end-page 5

# 批量探测（先查本地状态再决定是否补跑）
uv run yamibo-mcp-server probe-archived-threads --tid 572313 --tid 572314

# 检查轻小说更新
uv run yamibo-mcp-server check-thread-updates --tid 544422
uv run yamibo-mcp-server update-thread --tid 544422
```

### 2.3 导出

```bash
uv run yamibo-mcp-server create-export-thread-job --tid 572313
```

### 2.4 任务监控

```bash
uv run yamibo-mcp-server job-status <job_id>
```

### 2.5 本地检索

```bash
# 构建 RAG 索引
uv run yamibo-mcp-server create-rag-index-job --tid 572313
uv run yamibo-mcp-server create-rag-index-batch-jobs --tid 572313 --tid 572314

# 搜索归档内容
uv run yamibo-mcp-server search-archived-content --query "星空 告白" --mode hybrid --top-k 5
```

### 2.6 读取资源

```bash
uv run yamibo-mcp-server read-resource "yamibo://threads/572313/summary"
uv run yamibo-mcp-server read-resource "yamibo://threads/572313/diagnostics"
uv run yamibo-mcp-server read-resource "yamibo://forums/index"
```

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

### 3.8 知识库

点击导航栏「知识库」，可以进入本地检索管理页。

主要能力：

- 查看当前 RAG 配置、向量维度、chunker 版本和索引元数据
- 先看未索引队列，再看已索引内容
- 按板块筛选贴子、分页浏览已索引列表
- 按贴子查看 chunk 状态、失败数、最近索引时间
- 输入 TID 手动创建 `rag_index` 任务
- 对当前筛选结果或勾选贴子批量重建索引
- 用 keyword / vector / hybrid 模式直接调试本地检索结果
- 任务列表支持暂停 / 恢复，适合在高峰期临时让出并发

---

## 4. 通过 MCP 客户端使用

MCP 是为 LLM 客户端提供的辅助通道。在 Claude Desktop 或 Cursor 中配置后，可直接用自然语言操作。

### 4.1 浏览与搜索

> "帮我看一下百合会漫画区第 1 页有什么帖子"
> "搜索一下有没有 '星灵感应' 相关的帖子"

### 4.2 归档

> "帮我归档帖子 572313"

### 4.3 导出

> "把帖子 572313 导出成 ZIP"

支持三种策略：**cache_only**（默认，仅用本地数据）、**sync_if_stale**（本地过旧时先同步）、**force_resync**（强制重同步）。

### 4.4 检查更新

> "检查一下帖子 544422 是否有更新"

### 4.5 读取归档

MCP 资源 URI：
- `yamibo://threads/{tid}/summary` — 帖子摘要
- `yamibo://threads/{tid}/diagnostics` — 诊断信息
- `yamibo://threads/{tid}/posts` — 内容块
- `yamibo://threads/{tid}/assets` — 资产列表
- `yamibo://forums/index` — 分区列表
- `yamibo://jobs/{job_id}/events` — 任务事件

### 4.6 多分区

支持的分区：漫画区(30)、轻小说区(55)、动漫区(5)、水区(33)。

---

## 5. 典型工作流

### 5.1 单帖子归档并导出

```
1. create-sync-thread-job --tid 572313
                                     → 创建归档任务
2. 启动 Daemon 或等待现有 Daemon 消费
3. create-export-thread-job --tid 572313
                                     → 创建导出任务
4. job-status <job_id>              → 等待导出完成
5. 在 data/exports/ 中找到 ZIP 包
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
