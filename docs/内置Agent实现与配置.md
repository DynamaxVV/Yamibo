# 内置 Agent 实现与配置

日期：2026-09-12。当前已完成代码、隔离测试、页面重构及真实模型初步联调；未部署 NAS，未移除 Hermes。

## 已实现

- Pydantic AI 接入现有 OpenAI-compatible LLM 配置；启动和读取页面状态不发起模型请求。
- 本地 stdio `embedded-chat` MCP profile：工具、参数、资源独立白名单，MCP 子进程禁止执行 schema bootstrap，日志仅写 stderr。
- 本地会话与 Run 队列、事件回放、停止、中断记录、预算；单数据目录仅一个执行宿主。
- 宿主批准具体计划；明确的简短请求可以直接执行，复杂自然语言或不明确范围进入确认界面。
- 大批量先 `authorize_job_plan` 确认完整范围，再分批 `create_jobs`；重叠 tid 复用同一请求的回执。创建 Job 与操作回执同事务提交。
- `wait_for_jobs` 经 MCP 程序轮询，等待期间不调用模型；Job 终态和 `result_ready` 分开判断。
- 工作文件归属、只读导入、旧版本、软删除、路径/链接限制、修订冲突和未知结果阻止重放。
- 独立 AGENTS.md，仅按明确用户指令/本次批准更新，返回变更差异；新 Run 使用新指导。
- 对话页面整体重构为会话导航、消息与统一输入区、独立工作面板；支持排队、撤回、批准、操作记录、文件预览、移动端和明暗主题；设置页增加后端和预算。

## 配置步骤

1. 安装锁定依赖：`uv sync --extra dev`。
2. 在设置页“内置业务助手”选择“内置 Pydantic AI”，在现有 LLM 区域配置模型服务。模型地址是上游模型的 OpenAI-compatible 地址，不是 Hermes API 地址。
3. 远程访问设置对话访问令牌；在对话页输入令牌，只保存在当前标签页的 sessionStorage。生产环境仍应使用 HTTPS。
4. 保存后重启 Web 宿主；若 Web 嵌入 Daemon，则重启该进程。后台归档任务仍需要 Daemon。
5. 重启后按部署环境复验。本次真实模型使用临时测试配置，没有修改实际本地/生产配置。

也可以通过环境变量配置（不在文档中填写真实密钥）：

| 环境变量 | 默认值/说明 |
|---|---|
| `YAMIBO_CHAT_BACKEND` | `hermes`；启用本地模式设为 `embedded` |
| `YAMIBO_LLM_BASE_URL` / `YAMIBO_LLM_API_KEY` / `YAMIBO_LLM_MODEL` | 复用现有模型配置 |
| `YAMIBO_CHAT_ACCESS_TOKEN` | 无默认令牌；未设置时仅允许回环地址访问对话及设置 API |
| `YAMIBO_CHAT_MAX_REQUESTS` | 20；模型 SDK 内部网络自动重试关闭 |
| `YAMIBO_CHAT_MAX_TOOLS` | 50；业务和工作文件各有独立计数 |
| `YAMIBO_CHAT_TIMEOUT` | 900 秒，包含授权和 Job 等待 |
| `YAMIBO_CHAT_MAX_PARALLEL` | 2；同一会话串行 |
| `YAMIBO_CHAT_BATCH_LIMIT` | 20；未经额外确认的累计帖子数 |

`chat.backend` 等 JSON 设置遵循现有“环境变量 > 配置文件 > 默认值”顺序。后端切换只在重启后生效，不自动重发请求或迁移会话。回退时设回 `hermes` 并重启；本地记录保留，旧 Hermes 会话仍由 Hermes 提供。

## 数据与实现边界

新增迁移 `017_embedded_chat`。沿用项目 schema 初始化入口，不使用仓库不存在的顶层 alembic.ini，也不使用 `--force`。本次迁移只在隔离数据库中验证；部署前按原有运维规范执行备份和迁移门禁。

数据库新增六张表：`chat_sessions`、`chat_runs`、`chat_operations`、`chat_files`、`chat_requests`、`chat_events`。授权与文件版本索引合并在操作回执/文件记录中，完整模型消息保存在会话和 Run 的 JSON 数据内。

文件位于配置数据目录的 `agent/` 下：

- `guidance/AGENTS.md`：业务指导，不是仓库根目录开发指南。
- `workspace/`：平铺文本工作目录；支持 `.md`、`.txt`、`.json`、`.csv`、`.tsv`。
- `versions/`：按操作 ID 保存的旧版本。
- `trash/`：软删除内容；不提供永久删除工具。

工程默认限制：工作文件 2 MiB、指导文件 16 KiB、存储总量 100 MiB；文件读取按 16000 字符分段，业务输出最多 64 KiB。单次创建最多 200 帖，完整授权计划最多 1000 帖。同一会话最多 10 个活动/排队请求。文件大小限制当前为代码常量，执行预算为配置项。

文件系统与数据库无法原子提交：写文件前先记 `outcome_unknown`，成功后记录结果；中途崩溃时保留版本和文件，不自动重放或认领。用户可从操作记录判断哪些成果需要核查。原始归档、Job 导出、用户导入文件均不属于 Agent 可写文件。

长会话按完整请求裁剪模型上下文，保留完整数据库历史和近期操作事实；更早的事实通过 `read_operation_history` 分页读取。没有额外模型总结请求，也没有跨会话记忆库。遇到非常长的单次模型/工具结果仍应缩小任务范围。

stdio 子进程与宿主共享操作系统用户；这不是 OS 沙箱。限制由 MCP 服务端、路径/归属检查及宿主令牌实现。执行宿主要求本机共享同一数据目录，不支持多台主机各自使用不同目录却共享同一聊天数据库。

## 验证与待验收

自动化测试入口：

```bash
uv run pytest -q tests/unit/test_embedded_chat
uv run pytest -q tests/unit/test_server tests/unit/test_web_fastapi tests/unit/test_application/test_job_use_cases.py tests/unit/test_db/test_migrations.py
cd c && npm run build
```

`tests/unit/test_embedded_chat` 默认使用隔离 SQLite；设置 `YAMIBO_AGENT_TEST_PG_URL` 后同时运行 PostgreSQL。该 URL 必须指向允许创建/删除临时数据库的测试实例，测试仅删除自己创建的 UUID 数据库，不应填写生产地址。

初版完整自动化结果：**211 passed，1 skipped**。跳过项为 SQLite 参数下不适用的 PostgreSQL 专用 schema 检查；对应 PostgreSQL 参数已通过。前端 `npm run build` 通过，保留 Vite 的体积提示。

已执行的验证包含：真实 stdio + 确定性伪模型多步工具调用、请求去重、批量确认、Job 原子回执、停止/超时、事件重放、文件冲突/硬链接、指导更新、访问令牌，以及 PostgreSQL 完整迁移。浏览器使用隔离数据和伪模型验证关页恢复、单次提交、最终消息和文件预览，无页面 JavaScript 异常；已检查桌面与窄屏截图。

仍待验收：更多真实模型业务场景、真实论坛归档后总结、长期模型耗时/失败率与 Hermes 对比、实际 NAS 网络/认证/文件系统行为。没有通过这些验收前，不应下线 Hermes。

## 对话页面整体重构验证（2026-09-12）

已重构会话导航、空态、消息布局、输入区和操作确认区域；工作面板独立展示请求记录与工作文件，替代旧的底部折叠面板。支持统一排队、撤回、移动端面板切换与主题适配。

使用用户已启动的本地 PostgreSQL，在独立 `agent_test_*` 测试库执行内置 Agent 与 Web 回归：102 passed，1 skipped（SQLite 参数下的 PostgreSQL 专属测试）；测试库由 fixture 创建和清理，未修改业务库数据。

浏览器使用独立 `agent_ui_*` PostgreSQL 库、FunctionModel 与真实 stdio MCP，覆盖连续请求、排队撤回、关闭页面后继续、重开后历史恢复、文件预览，以及桌面 / 手机 / 深色主题。前端 `npm run build` 通过；仍有现有入口包超过 500 kB 的构建提示。后续真实模型初步联调见下一节；生产部署验收仍待进行。

## 真实模型初步联调（2026-09-12）

使用用户提供的本地 OpenAI 兼容服务与 `gemini-3.7-flash-low` 进行了真实模型联调，凭证仅注入临时测试进程，未写入仓库或变更业务配置。

隔离 PostgreSQL + 内置运行器 + 真实 stdio MCP 的三轮测试通过：普通中文回复 5.8 秒；创建工作文件并读取核对 8.2 秒；跨轮读取当前文件、修改到版本 2 并再次核对 9.8 秒。耗时为本次单次端到端观测，包含调度与工具调用，不代表吞吐或稳定性基准。

此项补充验证模型协议兼容、多步工具调用与上下文连续性。论坛实网操作、大批量任务、长期稳定性和生产部署仍未验收。

真实浏览器联调同样通过：页面发送创建文件请求后关闭，再打开恢复同一条请求；8.3 秒完成，工作面板可以读取并展示真实模型通过 MCP 创建的文件，无页面 JavaScript 异常。测试服务和独立数据库在验收后清理。

## 远程访问修复（2026-09-12）

设置页收到 `CHAT_AUTH_REQUIRED` 时直接显示令牌输入表单，验证成功后加载设置；错误令牌可重试，同标签页刷新保留授权。无需先绕行对话页。

反向代理终止 HTTPS、内部转发 HTTP 时，启用访问令牌的服务允许同一 Host 的 HTTPS Origin；不同 Host 仍拒绝，缺失或错误 Bearer 令牌仍拒绝。此修复需要部署新镜像后生效。
