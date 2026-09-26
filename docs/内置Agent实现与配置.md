# 内置 Agent 实现与配置

日期：2026-09-12，内容为当日实现记录。此后已增加讨论发现/来源回执、日报与定时规则、通用归档时间任务及知识库联动；当前代码入口和文档边界见[对话与知识库改造实现指南](对话与知识库改造实现指南.md)。本文早期测试数字、Hermes 过渡状态和截图结果不可直接视为当前部署状态。RK3588 NAS 与生产端点仍需独立验收。

## 已实现

- Pydantic AI 接入现有 OpenAI-compatible LLM 配置；启动和读取页面状态不发起模型请求。
- 本地 stdio `embedded-chat` MCP profile：工具、参数、资源独立白名单，MCP 子进程禁止执行 schema bootstrap，日志仅写 stderr；服务器构造不做数据库读取，Run 绑定在首次受控调用时校验，冷启动握手窗口为 60 秒。
- 本地会话与 Run 队列、事件回放、停止、中断记录、预算；单数据目录仅一个执行宿主。
- 宿主批准具体计划；明确的简短请求可以直接执行，复杂自然语言或不明确范围进入确认界面。
- 大批量先 `authorize_job_plan` 确认完整范围，再分批 `create_jobs`；重叠 tid 复用同一请求的回执。创建 Job 与操作回执同事务提交。
- `wait_for_jobs` 经 MCP 程序轮询，等待期间不调用模型；Job 终态和 `result_ready` 分开判断。
- 共享工作目录内文件的创建、分段读取、修改和软删除（含导入文件及其他会话文件，无需逐次确认）；保留旧版本、路径/链接隔离、修订冲突检查和未知结果阻止重放。工作文件不作为论坛原文的来源回执。
- 独立 AGENTS.md，仅按明确用户指令/本次批准更新，返回变更差异；新 Run 使用新指导。
- 对话页面整体重构为会话导航、消息与统一输入区、独立工作面板；支持排队、撤回、批准、操作记录、文件预览、移动端和明暗主题；设置页增加后端和预算。

## 配置步骤

1. 安装锁定依赖：`uv sync --extra dev`。
2. 在设置页“内置业务助手”选择“内置 Pydantic AI”，在现有 LLM 区域配置模型服务。模型地址是上游模型的 OpenAI-compatible 地址，不是 Hermes API 地址。
3. 远程访问设置对话访问令牌；在对话页输入令牌，只保存在当前标签页的 sessionStorage。生产环境仍应使用 HTTPS。
4. 在模型服务区先运行连通测试，再保存配置。模型地址、密钥和模型名保存后供新对话 Run 立即使用；正在执行的 Run 沿用启动时的配置。后台归档任务仍需要 Daemon。
5. 切换 Chat 后端等宿主级设置才需重启 Web；若 Web 嵌入 Daemon，则重启该进程。本次真实模型使用临时测试配置，没有修改实际本地/生产配置。

## 工具、指导与定时任务

内置 Agent 不再把全部公开 MCP 工具 schema 放进每次模型请求。需要业务能力时先调用 `discover_public_tools` 搜索目录，再用 `describe_public_tool` 读取参数和副作用，最后通过 `call_public_tool` 执行；原有论坛分区约束、危险参数屏蔽和写操作审批仍在服务端执行。项目 Skill 仍可用 `list_project_skills`、`read_project_skill` 按任务读取。Agent 只能提交 Skill 完整修订稿、理由和差异；设置页的人工审核通过后才影响后续读取，不赋予新权限。

设置页可以编辑数据目录中的 `agent/guidance/AGENTS.md`，带修订号冲突检测和旧版备份。它是助手的长期业务指导，不是仓库根目录的开发说明；保存后从后续 Run 生效。聊天中的“记住”仍需用户二次确认才写入同一文件。

通用定时任务当前支持一次执行和五字段 Cron，首个注册动作是**按固定 TID 归档**。在设置页可创建、暂停、启用、手动触发、查看触发记录及软归档规则；Agent 也可通过公开 MCP 工具管理，但创建和触发须按准确参数审批。Cron 的分钟位须是单个分钟值，频率至多每小时一次；时区使用 IANA 名称。调度器只负责“何时触发”，每次归档仍创建原有 Job，由 Daemon 负责租约、重试、进度与终态。触发记录的 `queued` 只说明 Job 已创建，不能代表归档完成。既有日报规则和日报期次继续独立运行，尚未迁移到通用调度器；任意自然语言提示、Shell、任意 MCP 工具和破坏性动作不能作为定时动作。

也可以通过环境变量配置（不在文档中填写真实密钥）：

| 环境变量 | 默认值/说明 |
|---|---|
| `YAMIBO_CHAT_BACKEND` | 默认 `embedded`；旧 Hermes 后端可显式设为 `hermes` |
| `YAMIBO_LLM_BASE_URL` | 外部 OpenAI 兼容模型 API 地址；默认 `https://api.openai.com/v1` |
| `YAMIBO_LLM_MODEL` | 外部 API 的模型名；默认 `gpt-4.1-mini` |
| `YAMIBO_LLM_API_KEY` | 外部 API 密钥；无默认值，未配置时对话不可用 |
| `YAMIBO_CHAT_ACCESS_TOKEN` | 无默认令牌；未设置时仅允许回环地址访问对话及设置 API |
| `YAMIBO_CHAT_TIMEOUT` | 900 秒，包含授权和 Job 等待 |
| `YAMIBO_CHAT_MAX_PARALLEL` | 2；同一会话串行 |

`chat.backend` 等 JSON 设置遵循现有“环境变量 > 配置文件 > 默认值”顺序。Compose 默认明确选择 `embedded`，并把外部模型 API 的地址、模型名和密钥作为部署环境变量传入。RK3588 NAS 只运行 Yamibo 客户端，不运行模型推理。通过设置页修改未被环境变量覆盖的 LLM 字段无需重启；容器环境变量本身发生变化仍需重新创建容器。后端切换只在重启后生效，不自动重发请求或迁移会话。需要旧 Hermes 服务时，显式设 `YAMIBO_CHAT_BACKEND=hermes` 并重启；本地记录保留，旧 Hermes 会话仍由 Hermes 提供。

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

## 运行故障诊断

内置运行器失败时，应用日志输出 `embedded_chat_failed run_id=... diagnostics=...`。包含异常类型、异常链/异常组、HTTP 状态码（如有）和调用位置；不记录异常原文、响应正文、源码行或完整路径，避免凭证和论坛内容泄露。前端继续返回通用错误，详细原因由管理员从日志的结构信息判断。

```bash
sudo docker compose logs --since=10m yamibo | grep embedded_chat_failed
```

如果日志显示 MCP handshake 超时，先在容器内测量受限服务器构造耗时：

```bash
sudo docker compose exec -T yamibo python - <<'PY'
import time
from yamibo_mcp.config import load_settings
from yamibo_mcp.services.embedded_chat.mcp import build_restricted_server

run_id = "替换为失败 Run ID"
started = time.monotonic()
build_restricted_server(load_settings(), run_id)
print("MCP server built", round(time.monotonic() - started, 1), "seconds")
PY
```

更新到包含延后 Run 校验的版本后，这一步只应包含受限工具 schema 注册，通常应在数秒内完成；若仍接近数据库连接超时，说明运行容器尚未使用新代码或存在其他启动阶段阻塞。

`TimeoutError` / `ConnectError` 等异常类型可帮助区分超时和连接问题，HTTP 状态可帮助定位认证或请求拒绝；并非所有提供商错误都包含状态码。日志只覆盖部署该修复之后的新请求，无法补回之前被丢弃的异常。

任务事件接口同时修复 PostgreSQL `datetime` 的 JSON 编码：时间字段及嵌套事件数据统一使用 FastAPI 标准编码器，不改变事件分页游标。本次无需数据库迁移。
