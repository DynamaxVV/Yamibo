# Agent 架构导航

> 版本：0.9.0 | 更新日期：2026-06-24

本文回答的是“AI 编码代理应该改哪里”。它不是产品说明，而是面向改代码、补测试、做集成的操作性文档。

### 新增接口边界

- 批量任务优先使用 `create_thread_archive_batch_jobs` 和 `create_rag_index_batch_jobs`
- RAG 检索优先使用 `search_archived_content`，这是本地只读路径，不会触发远端抓取
- 任务暂停 / 恢复属于后台调度能力，不应在 Agent 里被模拟成“重新创建同类任务”

## 1. 主数据流

### 1.1 读取远端论坛状态

```text
MCP 客户端
  -> server.mcp_registry / server.agent_tools
  -> application.search_use_cases 或 application.remote_queries
  -> yamibo.client + yamibo.parsers
  -> 紧凑 AgentResult
```

适用场景：论坛发现、帖子搜索、远端预览、更新检查。

约束：

- 允许读取本地 SQLite，为远端结果补充“是否已归档”等提示
- 不允许写入 thread/floor/content_blocks/assets
- 不允许下载图片、物化归档文件、伪装成本地归档读取

### 1.2 创建长任务

```text
MCP 客户端或 CLI
  -> server.agent_tools 或 server.cli
  -> application.archive_commands / application.update_commands
  -> db.repositories.jobs
  -> daemon.runner
  -> daemon.handlers/*
  -> yamibo.client、yamibo.parsers、storage、db.repositories
```

适用场景：归档、增量更新、导出、清理。

约束：

- 命令层只创建 SQLite job
- 真正执行和恢复由 daemon 负责
- 长任务结果通过 `read_job` / `read_job_events` 或资源读取回看

### 1.3 读取本地归档状态

```text
MCP 客户端
  -> server.agent_tools 或 server.resources
  -> application.archive_queries / application.job_queries
  -> db.repositories + storage paths
  -> 紧凑 AgentResult 或 MCP Resource
```

适用场景：读取 summary/content/assets/diagnostics/export/metadata、读取任务状态和事件。

约束：

- 本地归档读取绝不抓远端
- 大内容优先走 resource 或 `cursor/chunk_size` 分页

### 1.4 Web 控制台

```text
浏览器
  -> web.app
  -> web.api
  -> application queries/commands
  -> db.repositories / storage
```

前端源码位于 `frontend/`，包内静态产物位于 `src/yamibo_mcp/web/static/`。

## 2. 目录职责

| 路径 | 职责 | 代理修改建议 |
|------|------|-------------|
| `src/yamibo_mcp/server/app.py` | 轻量入口，导出 `build_mcp_server` 与 CLI `main` | 不要在这里写业务逻辑 |
| `src/yamibo_mcp/server/mcp_registry.py` | FastMCP tool/resource 注册与说明 | 新增公开 MCP 工具/资源时改这里 |
| `src/yamibo_mcp/server/agent_tools.py` | Agent-facing 薄包装 | 保持薄；逻辑下沉到 `application/*` |
| `src/yamibo_mcp/server/agent_adapter.py` | `AgentResult` 到 wire payload 的转换和异常映射 | 统一错误契约改这里 |
| `src/yamibo_mcp/server/resources.py` | MCP resource 主入口与静态 guide/schema | 放静态指导和本地资源，不放隐藏业务逻辑 |
| `src/yamibo_mcp/server/legacy_protocol.py` | 旧 JSON-RPC 协议兼容层 | 只保兼容，不再扩展主路径 |
| `src/yamibo_mcp/server/legacy_tools.py` | 旧工具名兼容包装 | 除兼容外不要继续堆新逻辑 |
| `src/yamibo_mcp/application/contracts.py` | Agent-facing 契约 | 保持小接口、稳定字段 |
| `src/yamibo_mcp/application/archive_commands.py` | 归档/导出/ensure 的命令侧逻辑 | 有副作用的“创建任务”放这里 |
| `src/yamibo_mcp/application/archive_queries.py` | 本地归档读取 | 禁止导入或调用 `YamiboClient` |
| `src/yamibo_mcp/application/remote_queries.py` | 远端 browse/search 与本地归档提示补充 | 可读本地库，不可持久化远端结果 |
| `src/yamibo_mcp/application/remote_inspection.py` | 远端帖子只读预览 | 只 fetch + parse，不写库不落盘 |
| `src/yamibo_mcp/application/update_queries.py` | 更新检测 | 可做远端比对，不创建 job |
| `src/yamibo_mcp/application/update_commands.py` | 增量更新任务创建 | 副作用仅是写入 queued job |
| `src/yamibo_mcp/application/job_queries.py` | 任务状态与事件读取 | 只读 SQLite |
| `src/yamibo_mcp/application/forum_queries.py` | 论坛 profile 与索引读取 | 本地元数据读取 |
| `src/yamibo_mcp/application/legacy_use_cases.py` | 历史行为聚合 | 过渡层，非新代码主入口 |
| `src/yamibo_mcp/daemon/runner.py` | 轮询、抢占、执行调度 | 任务生命周期变更改这里 |
| `src/yamibo_mcp/daemon/handlers/` | 具体任务实现 | 长任务实现放这里 |
| `src/yamibo_mcp/db/repositories/` | SQL 访问 | SQL 收敛在这里 |
| `src/yamibo_mcp/domain/` | 领域模型、枚举、校验 | 纯领域规则放这里 |
| `src/yamibo_mcp/yamibo/` | 论坛 HTTP、URL、页面分类、HTML 解析、标题解析 | 站点知识集中在这里 |
| `src/yamibo_mcp/storage/` | 文件路径、staging、归档物化、图片、导出 | 文件系统行为集中在这里 |
| `src/yamibo_mcp/services/` | LLM 客户端与标题辅助逻辑 | 内部能力，不是公共 Agent 接口 |
| `src/yamibo_mcp/web/` | 嵌入式 HTTP 服务与 packaged static | Python 侧 Web 路由改这里 |
| `frontend/` | React/Vite 前端源码 | UI 改这里，再构建到 `web/static/` |
| `src/yamibo_mcp/maintenance/` | 备份、清理、重置 | 运维脚本 |

## 3. 常见任务修改路径

| 任务 | 首选修改点 | 起手测试 |
|------|-----------|---------|
| 新增 Agent-facing MCP 工具 | `application/*`、`server/agent_tools.py`、`server/mcp_registry.py` | `uv run pytest tests/unit/test_server/test_agent_interface.py tests/unit/test_application/` |
| 修改 Agent 错误契约 | `application/contracts.py`、`server/agent_adapter.py` | `uv run pytest tests/unit/test_application/test_contracts.py tests/unit/test_server/test_agent_interface.py tests/unit/test_server/test_protocol_legacy.py` |
| 新增本地归档 view | `application/archive_queries.py`、相关 repository、必要时 `server/resources.py` | `uv run pytest tests/unit/test_application/test_archive_queries.py tests/unit/test_server/test_resources.py` |
| 修改远端搜索/浏览 | `application/search_use_cases.py`、`application/remote_queries.py`、`yamibo/client.py`、`yamibo/parsers/*` | `uv run pytest tests/unit/test_server/test_forum_id_tools.py tests/unit/test_parsers/` |
| 修改远端帖子预览 | `application/remote_inspection.py`、`yamibo/parsers/thread_detail.py` | `uv run pytest tests/unit/test_server/test_agent_interface.py tests/unit/test_parsers/test_thread_detail.py` |
| 修改归档任务行为 | `application/archive_commands.py`、`daemon/handlers/sync_thread.py`、`storage/*`、repositories | `uv run pytest tests/unit/test_application/test_thread_use_cases.py tests/unit/test_daemon/` |
| 修改更新检测/追加更新 | `application/update_queries.py`、`application/update_commands.py`、`daemon/handlers/update_thread.py` | `uv run pytest tests/unit/test_application/test_thread_update_use_cases.py tests/unit/test_daemon/test_update_thread_handler.py` |
| 修改任务状态/事件 | `application/job_queries.py`、`db/repositories/jobs.py`、`db/repositories/job_events.py` | `uv run pytest tests/unit/test_application/test_job_use_cases.py tests/unit/test_db/` |
| 修改 Web API | `web/api.py`、相关 `application/*` | `uv run pytest tests/unit/test_web/` |
| 修改 Web UI | `frontend/src/*`、`frontend/public/*` | `npm --prefix frontend run build` 后 `uv run pytest tests/unit/test_web/` |
| 修改静态资源路由 | `web/app.py`、`src/yamibo_mcp/web/static/README.md` | `uv run pytest tests/unit/test_web/test_app.py` |
| 修改迁移/Schema | `db/migrations.py`、repositories | `uv run pytest tests/unit/test_db/ tests/unit/test_application/` |

## 4. Legacy 禁区

以下模块保留的主要原因是兼容，不是主开发入口：

- `src/yamibo_mcp/server/tools.py`
- `src/yamibo_mcp/server/protocol.py`
- `src/yamibo_mcp/server/resource_handlers.py`
- `src/yamibo_mcp/server/legacy_tools.py`
- `src/yamibo_mcp/server/legacy_protocol.py`
- `src/yamibo_mcp/application/thread_use_cases.py`
- `src/yamibo_mcp/application/thread_update_use_cases.py`
- `src/yamibo_mcp/application/job_use_cases.py`
- `src/yamibo_mcp/application/legacy_use_cases.py`

规则：

- 不要把新逻辑继续写进 compatibility re-export
- 不要重新暴露 `llm_transform_text` 或 `parse_thread_title` 为公共 Agent 工具
- 不要给公共 Agent 工具重新加 `limit`
- 不要让远端预览写 SQLite、下载图片、物化归档
- 不要让本地归档查询抓远端页面

## 5. 前端源码与静态产物

当前结构已经拆分清楚：

- `frontend/` 是可编辑前端源码
- `src/yamibo_mcp/web/static/` 是 Vite 构建产物，也是 Python 包内随 wheel 分发的静态资源

修改 UI 的正确流程：

1. 改 `frontend/src/` 或 `frontend/public/`
2. 运行 `npm --prefix frontend run build`
3. 检查 `src/yamibo_mcp/web/static/` 的生成 diff
4. 运行 `uv run pytest tests/unit/test_web/`

默认不要去读 `src/yamibo_mcp/web/static/assets/` 里的压缩 JS/CSS，除非任务就是静态路由、打包或 cache busting。

## 6. 测试命令矩阵

| 范围 | 命令 |
|------|------|
| Agent 接口契约 | `uv run pytest tests/unit/test_server/test_agent_interface.py tests/unit/test_application/test_contracts.py` |
| MCP Resources | `uv run pytest tests/unit/test_server/test_resources.py` |
| 应用层 | `uv run pytest tests/unit/test_application/` |
| Server 层 | `uv run pytest tests/unit/test_server/` |
| Web API / 静态路由 | `uv run pytest tests/unit/test_web/` |
| 数据库 / Repository | `uv run pytest tests/unit/test_db/` |
| Daemon handlers | `uv run pytest tests/unit/test_daemon/` |
| 解析器 | `uv run pytest tests/unit/test_parsers/` |
| 前端构建 | `npm --prefix frontend run build` |
| 全量 Python 测试 | `uv run pytest` |

当前 Python 包没有单独配置 lint/typecheck；前端构建会先跑 TypeScript 编译再执行 Vite build。

## 7. 工作流资源与任务状态机

### 7.1 工作流资源入口

Agent 初次接入时，建议优先阅读以下资源，而不是直接扫全量代码：

| 资源 | 作用 | 适用时机 |
|------|------|----------|
| `yamibo://guide/agent-workflows` | 给出远端搜索、本地读取、归档/更新/导出的推荐调用顺序 | 不确定先调用哪个 tool 时 |
| `yamibo://guide/archive-model` | 说明远端只读、本地只读、后台 job 三层边界 | 需要判断副作用边界时 |
| `yamibo://guide/error-codes` | 给出公共错误码、重试建议和前置修复方向 | 调用失败时 |
| `yamibo://schema/tools` | 暴露兼容层工具参数签名 | 需要动态生成调用参数时 |

推荐顺序是：先 workflow，再 archive-model，失败时读 error-codes，最后仅在需要动态探测参数时读 schema。

### 7.2 任务状态机

Yamibo 的长任务不是“tool 内同步执行”，而是“tool 创建 job，daemon 消费 job”。因此 Agent 必须把 `read_job` / `read_job_events` 视为主状态面，而不是把一次工具调用当成最终完成。

| 状态 | 语义 | Agent 应对 |
|------|------|-----------|
| `queued` | 任务已入库，尚未被 worker 抢占 | 继续轮询，不要并发创建重复任务 |
| `running` | 正在执行 handler | 主看 `read_job`，排障时看 `read_job_events` |
| `retrying` | handler 或 runner 正在内部重试 | 继续等待，不要抢着新建第二个 job |
| `succeeded` | 任务完成，产物可读 | 切到 `archive_queries`、资源读取或导出读取 |
| `partial` | 主体成功，但部分图片、附件或收尾步骤失败 | 允许继续读归档，同时看 `diagnostics` 和事件日志 |
| `failed` | 任务失败并结束 | 优先读错误码和事件，不要无条件重试 |
| `interrupted` | worker 中断，可由 recovery/其他 daemon 接手 | 观察是否恢复到 `running`，否则人工介入 |
| `cancelled` | 任务被取消 | 结束当前跟踪，需要时重新创建新任务 |

### 7.3 推荐轮询 / 恢复策略

对 Agent 最友好的调用节奏如下：

1. 调用命令工具创建任务，只拿到 `job_id`
2. 使用 `read_job(job_id)` 做轻量轮询
3. 只有出现 `failed`、`partial`、长期 `running` 或 `interrupted` 时，再读取 `read_job_events(job_id)`
4. 状态进入 `succeeded` 或 `partial` 后，切换到 `read_archived_thread` 或资源 URI 读取

这样设计的优点：

- 正常路径 token 成本低，因为多数轮询只读一个小 payload
- 排障路径信息充分，因为事件流保留阶段切换和错误上下文
- 工具职责清晰，避免 Agent 误以为“创建任务”等于“已经完成归档”

### 7.4 工具与资源的分工

在这个项目里，tool 和 resource 的分工是明确的：

- tool 负责“触发一次意图”或“读取一次结构化快照”
- resource 负责“按 URI 挂出稳定只读内容”

对 Agent 来说，最重要的分工边界是：

- `create_thread_archive_job` / `create_thread_update_job` / `create_thread_export_job` 只写 job，不执行主体
- `read_job` / `read_job_events` 读取任务过程，不替代归档内容本身
- `read_archived_thread` 读取应用层整理后的本地视图，适合结构化消费
- `yamibo://threads/{tid}/context` / `posts` / `assets` / `diagnostics` 提供面向 URI 的稳定只读入口
- `yamibo://jobs/{job_id}/events` 让“不方便继续调 tool 的客户端”也能读取任务时间线

这个边界对 OpenClaw、Hermes 这类会自己规划多步工具调用的 Agent 框架尤其重要，因为它减少了“隐式副作用”和“状态混叠”。
