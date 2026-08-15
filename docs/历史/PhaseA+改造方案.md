# Yamibo “1.0 核心 + Phase A+”改造与实施方案

> 文档状态：代码改造与本地 PostgreSQL 兼容迁移验证已完成；NAS 发布待维护窗口执行
> 目标版本：1.1 候选
> 编写日期：2026-08-08
> 决策：保留 Phase A 的 Agent 友好能力契约，移除尚未发布的 Phase B/C 内部 Runtime 设计

> 实施记录：lunamax 完成代码改造，独立 verifier 验收通过。全量 Python 测试 `1131 passed, 13 skipped, 3 warnings`；隔离 PostgreSQL 回归 `12 passed`；前端 build 和 `git diff --check` 通过。随后已对本地 Docker PostgreSQL 完成项目备份，并验证 `012 -> 013` 升级，旧 Agent 表已清理；未操作 NAS 生产库。

## 1. 管理摘要

本次改造不是把 Yamibo 退回 1.0.0，而是把当前工作区中真正有价值的 Agent 改进留下，并删除与实际部署重复的内部 Agent Runtime。

改造后的职责只有两层：

1. Hermes 是唯一负责理解自然语言、规划步骤和选择工具的 LLM Agent。
2. Yamibo MCP、Job 和 Daemon 负责提供明确能力、可靠执行、自动重试和结构化恢复建议。

不会继续保留第二个负责规划的 Yamibo Runtime，也不会保留 Agent Run、Step、Approval、Artifact、Citation 等专用数据库状态机。Web Chat 页面仍按 1.0 方式连接 Hermes，不接入 Agent Runtime。

本方案会实现两个可直接改善 Hermes 使用体验的能力：

- `yamibo://schema/capabilities`：告诉 Hermes 每个工具的参数、副作用、成本、幂等性和后续动作。
- `read_job.data.recovery`：告诉 Hermes 当前错误由谁处理、是否会自动恢复、何时再查、是否需要用户介入，以及下一步可安全调用什么。

## 2. 3W1H 决策

### 2.1 What：具体改什么

**保留：**

- PostgreSQL、pgvector 和现有业务数据。
- Job、Job Event、Daemon、lease 和已有反爬恢复。
- MCP 公开业务工具及 `AgentResult` 结构。
- Phase A Capability Manifest 及 `yamibo://schema/capabilities`。
- 现有 Web 管理页面和 Hermes 对话转发。
- 与 B/C 无关的浏览器回退、PostgreSQL URL 规范化、测试数据库隔离等改动。

**新增或增强：**

- Job 的结构化 `recovery` 对象。
- `retry_count`、`max_retries` 和 `next_retry_at` 可见性。
- 利用现有 `lease_until` 实现真正的延迟重试，避免瞬间耗尽重试次数。
- 基于真实失败状态的 Phase A+ 合同测试和 PostgreSQL 验证。
- 面向 Hermes 的固定操作规则和真实请求观测指标。

**删除：**

- Phase B 单步 Agent Run。
- Phase C 多步模型循环和 OpenAI-compatible Runtime Adapter。
- 9 个 Runtime 控制 MCP 工具。
- 6 张 Agent 专用表对应的代码和未发布迁移。
- Agent Run Web API、HMAC Gateway、详情页和 Chat Runtime 模式。
- Phase C transcript、Evaluator 中仅服务于 Runtime/Citation 的逻辑。
- 文档中“B/C 已完成或可使用”的产品声明。

### 2.2 Why：为什么这样改

当前部署已经有 Hermes 负责自然语言规划。Yamibo 再调用一个模型进行相同规划，会形成“Agent 调 Agent”的重复职责，并引入第二套模型配置、两套 Run 状态机和 6 张新表。

真实问题集中在 Job 恢复信息不足，而不是缺少另一个 LLM。当前 PostgreSQL 数据也支持这个判断：

| 当前本地 PostgreSQL 事实 | 数量 | 对方案的含义 |
|---|---:|---|
| Job 总数 | 705 | Job 已是稳定执行事实，不应另建 Run 承载相同事实 |
| `succeeded` | 574 | 主流程已能可靠完成 |
| `partial` | 77 | Hermes 需要知道“结果可读但不完整”，而不是重新规划整个 Run |
| `paused` | 46 | 需要明确区分自动探测恢复和人工处理 |
| `failed` | 8 | 当前全部为权限不足，应该升级给用户而不是盲目重试 |
| `REMOTE_SOFT_BLOCK` | 9 | 应由 Daemon/代理池恢复，Hermes 只需等待并复查 |
| `REMOTE_THREAD_PERMISSION_REQUIRED` | 8 | 必须由用户补充高权限账号，模型不能自行解决 |
| 6 张 Agent 表记录数 | 全部为 0 | 本地实验结构可在备份后清理，没有业务记录迁移价值 |

### 2.3 Who：每个组件负责什么

| 组件 | 改造后唯一职责 | 不再负责 |
|---|---|---|
| Hermes | 理解请求、读取 Manifest、选择 MCP 工具、根据 Recovery 决定下一步 | 把目标委托给 Yamibo 的第二个 Agent |
| Yamibo MCP | 暴露明确、细粒度、结构化的能力和状态 | 执行模型循环 |
| Job Repository | 去重、状态、重试次数、延迟重试、事件 | 保存模型计划 |
| Daemon | 确定性执行、lease 恢复、维护/反爬探测 | 调用规划模型推进 Run |
| PostgreSQL | 保存业务数据、Job 和 Job Event | 保存 Agent Run/Step/Approval |
| Web | 人工管理和现有业务页面 | Agent Runtime 控制台 |
| 用户 | 处理 Cookie、权限、配置和破坏性决策 | 观察每一次可自动恢复的短时错误 |

### 2.4 How：如何落地

```mermaid
flowchart LR
    User["唯一用户"] -->|自然语言| Hermes["Hermes：唯一规划 Agent"]
    Hermes -->|读取 Capability Manifest| MCP["Yamibo MCP"]
    Hermes -->|查询或创建 Job| MCP
    MCP --> PG[("PostgreSQL：业务数据 + Jobs")]
    Daemon["Daemon：执行 + 自动恢复"] --> PG
    Daemon --> Forum["Yamibo 论坛"]
    PG -->|状态、错误、Recovery| MCP
    MCP -->|明确下一步| Hermes
    User --> Web["Web 管理页面"]
    Web --> PG
```

实施采用“先证明保留面，再删除 B/C，最后增强 Job”的顺序。任何数据库删除都与代码改造分开执行，并要求先备份。

## 3. 最终产品边界

### 3.1 保留的 Phase A

Phase A+ 保留以下机器契约：

- `schema_version`
- capability 名称和描述
- `input_schema` 和通用 `output_schema`
- `effect`：`read_only`、`remote_read`、`enqueue_job`
- `risk`
- `idempotency`
- `requires`
- `cost_hints`
- `produces`
- `followups`
- `timeout_class`
- Job 工具的 `terminal_read`

继续保留 `yamibo://schema/tools` 作为兼容入口，但文档推荐 Hermes 优先读取 `yamibo://schema/capabilities`。

### 3.2 从 Phase A 中一并去掉的预埋设计

以下内容虽然随 Phase A 实现进入工作区，但只有 B/C Runtime 实际消费，因此不纳入 Phase A+：

- `AgentCitation` 和 `AgentArtifact` 数据类。
- Manifest 顶层 `citation_contract` 和 `artifact_contract`。
- 没有执行权限作用的多主体 capability profiles。
- Phase C transcript fixture。
- Citation hash 校验和 Runtime transcript evaluator。

暂不压缩 `agent_tools.py` 中现有 Capability 元数据写法。它虽然较长，但已经可用；在删除 B/C 的同一个改动中重写元数据机制会扩大回归范围。后续只有在新增工具维护成本明显上升时再单独简化。

### 3.3 明确不做

- 不新增 OpenAI-compatible 模型调用。
- 不新增 Agent 专用数据库表。
- 不新增审批框架、主体模型或 HMAC Agent Gateway。
- 不把 Web Chat 作为当前 Agent 操作入口。
- 不把数据库维护、重置或删除工具暴露给 Hermes。
- 不为了“以后可能需要”保留不可达代码。

## 4. Job Recovery 合同

### 4.1 返回位置

在现有 `read_job` 和 `wait_for_job` 返回的 `data` 中增加 `recovery`。这是向后兼容的字段增加，不改变现有 `status`、`result_ready`、`diagnostic_summary` 和资源 URI。

同时增加：

- `retry_count`
- `max_retries`
- `next_retry_at`：仅 `retrying` 时返回；由现有 `lease_until` 映射，不新增数据库列。

### 4.2 建议结构

```json
{
  "job_id": "job_xxx",
  "status": "paused",
  "error_code": "REMOTE_SOFT_BLOCK",
  "retry_count": 2,
  "max_retries": 3,
  "next_retry_at": null,
  "recovery": {
    "classification": "automatic_recovery",
    "owner": "daemon",
    "retryable": true,
    "requires_user_action": false,
    "reason_code": "REMOTE_SOFT_BLOCK",
    "poll_after_seconds": 600,
    "message": "Daemon 正在探测远端访问是否恢复，不要创建重复 Job。",
    "next_actions": [
      {
        "tool": "read_job",
        "args": {"job_id": "job_xxx"},
        "reason": "等待自动探测完成后复查原 Job"
      }
    ]
  }
}
```

### 4.3 固定分类

`classification` 只允许以下值，避免 Hermes 从自由文本猜测：

| 分类 | 所有者 | 含义 |
|---|---|---|
| `completed` | system | 已成功，无需恢复 |
| `use_partial_result` | agent | 已有可读结果，但应提示不完整 |
| `continue_waiting` | daemon | 正常排队、执行或等待 lease 恢复 |
| `automatic_retry` | daemon | 已安排延迟重试，禁止重复建 Job |
| `automatic_recovery` | daemon | 处于维护/反爬暂停，Daemon 会探测恢复 |
| `user_action_required` | user | Cookie、权限或配置必须人工处理 |
| `inspect_failure` | agent | 无确定自动恢复路径，先读 Job Event |
| `stopped` | system | 已取消或被新 Job 取代，不应重试原 Job |

### 4.4 状态与错误映射

| 条件 | 分类 | 下一步 |
|---|---|---|
| `succeeded` | `completed` | 使用 Manifest 中创建工具声明的 followup 读取结果 |
| `partial` | `use_partial_result` | 允许读取已有结果；必要时读 `read_job_events` 查看缺失项 |
| `queued` / 正常 `running` | `continue_waiting` | 按 `poll_after_seconds` 再调 `read_job` |
| `retrying` | `automatic_retry` | 等到 `next_retry_at`，不要新建 Job |
| `interrupted` | `continue_waiting` | Daemon 会重新领取；持续超时才提示检查 Daemon |
| `paused` + 维护/444/soft block | `automatic_recovery` | 等待 Daemon 探测并复查原 Job |
| `failed` + 权限/Cookie/配置错误 | `user_action_required` | 向用户报告具体缺失条件，不自动重试 |
| 其他 `failed` | `inspect_failure` | 调 `read_job_events`，不盲目重复提交 |
| `cancelled` / `superseded` | `stopped` | 停止；superseded 时使用替代 Job（若事件中提供） |

第一版不提供通用 `retry_job` MCP 工具。当前失败样本主要是权限不足，自动暴露重试只会重复失败；短时错误已由 Daemon 负责。未来只有真实数据证明“人工修复后需要远程恢复原 Job”是高频操作时再增加受限工具。

## 5. 确定性自动恢复实现

### 5.1 复用现有 lease，不加新列

`JobsRepository.acquire_next()` 已经只领取 `lease_until` 为空或已过期的 `queued/retrying/interrupted` Job。因此 `retry_later()` 不再把 `lease_until` 清空，而是写入未来时间，即可得到延迟重试。

实现处需要留下明确的简化说明：

```python
# PONETAIL: retrying jobs reuse lease_until as a not-before timestamp;
# split it into next_retry_at only if scheduling semantics expand.
```

这是有意的最小实现：不新增迁移，不新增调度器，不新增队列依赖。

### 5.2 重试延迟

`retry_later()` 增加关键字参数 `delay_seconds`，默认由仓库根据下一次重试次数计算，最大不超过 60 秒：

| 错误 | 首次建议延迟 | 后续策略 |
|---|---:|---|
| `HTTP_429` | 10 秒 | 20、40 秒，受 `max_retries` 限制 |
| `HTTP_444` 节点级失败 | 5 秒 | 切换节点后 10、20 秒 |
| `REMOTE_SOFT_BLOCK` 未触发全局暂停 | 15 秒 | 30、60 秒 |
| 其他可重试远端错误 | 5 秒 | 10、20 秒 |
| 维护/全局反爬暂停 | 不使用 Job 重试延迟 | 继续使用现有系统级定时探测和批量恢复 |

Daemon 仍是唯一决定是否重试的组件。Hermes 只读取决定结果，不参与定时和重试计数。

### 5.3 不应自动恢复的错误

以下错误直接进入 `user_action_required` 或 `inspect_failure`：

- `REMOTE_LOGIN_REQUIRED`
- `REMOTE_THREAD_PERMISSION_REQUIRED`
- `REMOTE_ACCESS_PAUSED` 且没有活动探测计划
- 参数、导出前置条件、数据库配置和内部错误
- 达到 `max_retries` 的远端错误
- 任何删除、重置或清理操作

## 6. B/C 删除清单

### 6.1 整文件删除

| 文件或目录 | 原职责 | 处理 |
|---|---|---|
| `src/yamibo_mcp/agent_runtime/` | Phase C model adapter、context 和 orchestrator | 删除 |
| `src/yamibo_mcp/application/agent_runs.py` | Phase B Run/Step/Approval 和执行器 | 删除 |
| `src/yamibo_mcp/application/agent_runtime_commands.py` | Phase C 创建、审批、取消命令 | 删除 |
| `src/yamibo_mcp/db/repositories/agent_runs.py` | 6 张 Agent 表仓库 | 删除 |
| `src/yamibo_mcp/web_fastapi/routers/agent_runs.py` | Agent Run HTTP 控制面和 HMAC 校验 | 删除 |
| `c/src/pages/AgentRunDetail.tsx` | Agent Run 详情页 | 删除 |
| `alembic/versions/009_add_agent_runtime.py` | Agent 表 | 删除 |
| `alembic/versions/010_add_agent_step_execution_lease.py` | Step lease | 删除 |
| `alembic/versions/011_add_agent_runtime_orchestration_lease.py` | Runtime lease | 删除 |
| `alembic/versions/012_add_agent_artifact_hashes.py` | Artifact hash | 删除 |
| `tests/unit/test_agent_runtime/` | Phase C 测试 | 删除 |
| `tests/unit/test_application/test_agent_runs.py` | Phase B 测试 | 删除 |
| `tests/fixtures/agent_transcripts/phase_c.json` | Phase C 固定轨迹 | 删除 |
| `docs/history/llm-runtime-phase-b-implementation-report.md` | 未发布 B 报告 | 删除 |
| `docs/history/llm-runtime-phase-c-implementation-report.md` | 未发布 C 报告 | 删除 |

`docs/history/llm-runtime-phase-a-implementation-report.md` 可保留，但必须改写结论，删除把 Citation/Artifact 持久化或后续 B/C 当作当前能力的表述。

### 6.2 按代码块删除

| 文件 | 删除内容 | 保留内容 |
|---|---|---|
| `server/agent_tools.py` | Agent Run imports、9 个 Runtime 控制函数、`RUNTIME_CONTROL_TOOLS` | 27 个公开业务工具及 Capability 元数据 |
| `server/mcp_registry.py` | Runtime 控制工具注册 | 公开工具和 Capability Resource 注册 |
| `daemon/runner.py` | 5 条 Run 推进/过期调用 | Job 获取、lease、维护和反爬恢复 |
| `web_fastapi/app.py` | `agent_runs` router | 其他业务 router |
| `server/resources.py` | Agent Run/Artifact Resource 读取 | capability、job、thread 等 Resource |
| `server/resource_uris.py` | Agent Run/Artifact URI | `capabilities_schema_uri` 和 Job URI |
| `application/contracts.py` | `AgentCitation`、`AgentArtifact` | `AgentAction`、`AgentError`、`AgentResult` |
| `errors.py` | 仅由 Run 控制面使用的错误类型 | 业务和远端错误 |
| `db/migrations.py` | SQLite Agent 表 SQL | 1.0 业务表及其他当前改动 |
| `c/src/App.tsx` | `/agent-runs/:runId` route | 现有页面路由 |
| `c/src/api/client.ts` | Agent Run 类型和 API | 现有 API client |
| `c/src/pages/Chat.tsx` | Runtime mode、Run polling 和链接 | Hermes 对话模式 |
| `c/src/styles/chat.css` | Runtime mode 专用样式 | 原 Chat 样式 |
| `services/web_chat.py` | Runtime mode 分支 | OpenAI-compatible/Hermes 转发 |
| `tests/unit/test_web_fastapi/test_app.py` | Agent Run Gateway/API 测试 | 其他 FastAPI 测试 |
| `tests/integration/test_postgres_regression.py` | Agent 表 revision 012 断言 | PostgreSQL 隔离与其他回归修复 |
| `.github/workflows/ci.yml` | 重复的 Phase C transcript gate | 全量 pytest、隔离 PostgreSQL、前端构建 |

### 6.3 明确不得误删的混合改动

当前工作区不是纯 B/C diff。以下改动与本次删除目标无关，应原样保留并单独验证：

- `src/yamibo_mcp/yamibo/browser_fallback.py`
- `src/yamibo_mcp/yamibo/anti_bot.py` 中浏览器回退相关改动
- `src/yamibo_mcp/web_fastapi/routers/remote_forum.py`
- `tests/unit/test_yamibo/test_browser_fallback.py`
- `tests/unit/test_yamibo/test_anti_bot.py`
- `pyproject.toml` 和 `uv.lock` 中 Playwright 依赖
- `db/connection.py` 中 PostgreSQL URL 规范化
- `tests/conftest.py` 中隔离测试数据库
- `tests/integration/test_postgres_regression.py` 中与 Runtime 无关的数据隔离修复
- `db/repositories/discussion_trends.py` 的独立修复

实施时禁止对整个文件执行粗粒度回退；应使用精确 patch 删除 B/C 代码块。

## 7. 数据库处理方案

### 7.1 为什么不能直接执行 downgrade

`009_add_agent_runtime.py` 的 `downgrade()` 为空，且当前本地 PostgreSQL 的 Alembic revision 已是 `012_add_agent_artifact_hashes`。直接删除迁移文件后，Alembic 将无法解析数据库中的当前 revision。

### 7.2 环境分流

```mermaid
flowchart TD
    Start["部署前读取 alembic_version"] --> Old{"revision 是否 <= 008?"}
    Old -->|是| Normal["无需数据库清理，直接部署 Phase A+"]
    Old -->|否| Rows{"6 张 Agent 表是否全为空?"}
    Rows -->|否| Stop["停止：导出 Agent 数据并人工决定保留策略"]
    Rows -->|是| Backup["完成 PostgreSQL 备份"]
    Backup --> Clean["按外键顺序删除 Agent 表"]
    Clean --> Stamp["将 alembic_version 设回 008"]
    Stamp --> Verify["验证业务表数量和抽样读取"]
```

**场景 A：NAS 生产仍是 1.0/008 或更早。**

- 这是预期场景。
- 不运行任何 Agent 表清理 SQL。
- 删除未发布的 009-012 后，当前迁移头回到 008。
- 部署只运行正常的非破坏性迁移检查。

**场景 B：某持久环境已经运行 009-012。**

- 先运行 `yamibo-backup-db` 并验证备份文件存在。
- 查询 6 张表记录数；任意一张非零则停止自动清理。
- 全部为空时，在维护窗口内停止 Yamibo App、MCP 和 Daemon。
- 按 `agent_citations`、`agent_artifacts`、`agent_approvals`、`agent_run_events`、`agent_steps`、`agent_runs` 顺序删除。
- 将该 schema 的 `alembic_version.version_num` 更新为 `008_add_rag_chunk_traceability`。
- 重启服务并执行只读验收。

数据库清理属于破坏性操作，不写入应用启动逻辑，也不在普通迁移中自动执行。当前本地 Docker 的 6 张 Agent 表均为 0 行，可在实际实施阶段经用户确认后清理；本方案文档阶段不执行删除。

## 8. 分步实施计划

### 步骤 0：建立可回退基线

1. 保存 `git status --short`、`git diff --name-status` 和当前测试结果。
2. 记录本地及 NAS 的 `alembic_version`、Agent 表行数、Job 状态计数。
3. 对 NAS PostgreSQL 执行一次备份，但不修改数据。
4. 将浏览器回退和其他非 B/C 改动列为保护清单。

**通过标准：** 能明确指出每个未提交文件属于 Phase A、B/C、Phase A+、浏览器回退或通用数据库修复中的哪一类。

### 步骤 1：删除 B/C 后端可达路径

1. 删除 Runtime、Run Application、Repository 和 Web Router 文件。
2. 从 `agent_tools.py` 删除 Runtime imports、控制函数和注册表。
3. 从 MCP registry、Resource registry 和 FastAPI app 删除控制面入口。
4. 从 Daemon 删除所有 Agent Run 推进调用。
5. 从 SQLite schema 删除 Agent 表定义。

**通过标准：**

- `rg -n "agent_runtime|agent_runs|create_agent_run|create_agent_runtime_run|approve_agent" src/yamibo_mcp` 不再发现可执行代码。
- MCP 工具列表仍只包含 27 个业务工具，不包含 Runtime 控制工具。
- Daemon `run_once()` 只推进业务 Job 和现有维护探测。

### 步骤 2：删除 B/C 前端和文档声明

1. Chat 恢复为 Hermes 单模式。
2. 删除 Agent Run route、API client 和详情页。
3. 重建前端静态资源，不能手工选择旧 hash 文件。
4. 从接口、Chat、部署、工作流和 README 文档删除 B/C 可用性声明。
5. 将路线图改为“Phase A+ 当前目标；B/C 已否决并归档为历史方案”。

**通过标准：**

- 页面不存在 Agent Runtime 模式和 `/agent-runs/:runId` 路由。
- `npm run build` 成功。
- 文档搜索不再把 B/C 描述为当前能力。

### 步骤 3：收紧 Phase A

1. 保留 Capability Manifest 和 27 个工具元数据。
2. 删除 Citation/Artifact 数据类和 Manifest 顶层合同。
3. 删除静态多主体 profiles。
4. 删除 Phase C fixture 和 Runtime evaluator；保留 Manifest 覆盖、schema、followup、幂等性和 destructive 禁止测试。

**通过标准：**

- Manifest 恰好覆盖 `PUBLIC_AGENT_TOOLS`，无重复和遗漏。
- 所有 `enqueue_job` 工具均声明 `terminal_read`。
- Manifest 中无 Runtime、Run、Approval、Citation 持久化概念。

### 步骤 4：实现 Job Recovery

1. 在 `server/schemas.py` 中增加表驱动的 Recovery 映射函数。
2. 在 Job payload 增加重试计数、`next_retry_at` 和 `recovery`。
3. 保证 Recovery 函数不超过 50 行；复杂映射放在模块级常量，不新建框架。
4. 为成功、partial、活跃、retrying、interrupted、paused、权限失败、一般失败和停止状态各增加最小测试。

**通过标准：**

- 任一 Job 状态都返回已知 `classification`。
- `next_actions` 中的工具必须存在于公开 MCP 工具表。
- `user_action_required` 不返回自动重试动作。
- `automatic_retry` 和 `automatic_recovery` 明确禁止创建重复 Job。

### 步骤 5：实现延迟重试

1. 扩展 `JobsRepository.retry_later(delay_seconds=...)`。
2. 复用 `lease_until` 保存 retry not-before 时间。
3. 调整 Daemon 的 429、444、soft block 和一般远端失败调用。
4. 在 payload 中把 retrying Job 的 `lease_until` 暴露为 `next_retry_at`。
5. 增加 Repository 测试：到期前不能 acquire，到期后可以 acquire，达到 `max_retries` 后进入 failed。

**通过标准：** 快速连续运行 Daemon 时，同一 retrying Job 不会在延迟到期前再次执行。

### 步骤 6：处理未发布迁移

1. 保留 009-012 的兼容 revision 占位文件，使已经运行过 B/C 的数据库仍可被 Alembic 解析。
2. 由 013 迁移按依赖顺序删除旧 Agent Runtime 表，并将数据库升级到当前 head。
3. 对新建测试数据库和已在 012 的数据库分别验证升级路径。
4. NAS 发布前仍需备份；若 NAS 当前为 008，013 会在升级时安全地跳过不存在的旧表。

**通过标准：** 新库、现有 008 库和经批准清理的本地 012 库都能启动，且业务表记录数不变。

### 步骤 7：更新 Hermes 使用合同

在 `docs/MCP指南/智能体工作流.md` 中固定以下顺序：

1. 会话首次使用先读取 Capability Manifest。
2. 调用工具前检查 `effect`、`idempotency` 和 `terminal_read`。
3. 创建 Job 后保存并复用原 `job_id`。
4. `result_ready=false` 时不读取最终业务结果。
5. 优先执行 `recovery.next_actions`，不根据错误文本猜测。
6. `requires_user_action=true` 时停止工具循环并向用户报告。
7. `partial` 先使用已有结果并明确缺失，不默认重跑整个任务。

**通过标准：** 文档示例覆盖归档成功、反爬暂停、权限失败和 partial 四条真实路径。

### 步骤 8：验证和发布候选

按第 10 节执行测试。通过后生成候选镜像，但不直接覆盖 NAS 的 1.0.0。先在连接备份数据库或隔离 PostgreSQL 的环境运行 smoke，再部署生产。

## 9. 文件级实现索引

| 目标 | 主要文件 |
|---|---|
| Capability Manifest | `src/yamibo_mcp/server/capabilities.py` |
| Capability 元数据 | `src/yamibo_mcp/server/agent_tools.py` |
| Recovery 生成 | `src/yamibo_mcp/server/schemas.py` |
| Job 查询 | `src/yamibo_mcp/application/job_queries.py` |
| 延迟重试与 acquire | `src/yamibo_mcp/db/repositories/jobs.py` |
| 远端错误决策 | `src/yamibo_mcp/daemon/runner.py` |
| MCP Resource | `src/yamibo_mcp/server/resources.py`、`resource_uris.py` |
| Manifest 测试 | `tests/unit/test_server/test_capabilities.py` |
| Recovery 测试 | `tests/unit/test_server/test_agent_interface.py` 或独立最小测试文件 |
| Repository 重试测试 | `tests/unit/test_db/` 下现有 Jobs Repository 测试文件 |
| PostgreSQL 回归 | `tests/integration/test_postgres_regression.py` |
| Hermes 工作流 | `docs/MCP指南/智能体工作流.md` |
| 路线图 | `docs/LLM原生运行时路线图.md` |

## 10. 验证方案

### 10.1 静态边界检查

```bash
rg -n "agent_runtime|create_agent_run|create_agent_runtime_run|AgentRunDetail" src c tests
rg -n "Agent Runtime 模式|Phase B.*已完成|Phase C.*已完成" docs
git diff --check
```

预期：前两条只允许命中明确标注为历史/否决方案的文档，不命中生产代码。

### 10.2 定向测试

```bash
uv run pytest -q tests/unit/test_server/test_capabilities.py
uv run pytest -q tests/unit/test_server/test_agent_interface.py
uv run pytest -q tests/unit/test_db/ -k "job and (retry or acquire)"
```

### 10.3 全量测试和前端

```bash
uv run pytest
cd c && npm run build
```

### 10.4 PostgreSQL 验证

```bash
YAMIBO_TEST_PG_URL=postgresql://yamibo:yamibo@localhost:5432/yamibo \
YAMIBO_TEST_PG_ISOLATED=1 \
uv run pytest -q tests/integration/test_postgres_regression.py
```

必须使用隔离测试数据库，不允许测试直接清理已有 705 条 Job 的当前数据库。

### 10.5 真实数据只读 smoke

从现有数据库各选择一个 `succeeded`、`partial`、`paused/REMOTE_SOFT_BLOCK` 和 `failed/REMOTE_THREAD_PERMISSION_REQUIRED` Job，只调用 `read_job`，验证：

- `succeeded -> completed`
- `partial -> use_partial_result`
- `paused + REMOTE_SOFT_BLOCK -> automatic_recovery`
- `failed + REMOTE_THREAD_PERMISSION_REQUIRED -> user_action_required`

该 smoke 不创建、不重试、不恢复、不删除任何 Job。

### 10.6 Hermes 端到端验收

至少执行以下四条自然语言请求并保存完整 MCP 轨迹：

1. 查询一个已归档主题并读取内容。
2. 创建一个归档 Job，等待成功后读取结果。
3. 查询一个现有反爬暂停 Job，确认 Hermes 不创建重复任务。
4. 查询一个权限失败 Job，确认 Hermes停止并准确要求高权限账号。

## 11. 可量化验收标准

| 指标 | 验收目标 |
|---|---:|
| LLM 规划服务 | 仅 Hermes 1 个 |
| 业务执行状态机 | 仅 Job 1 套 |
| Agent 专用运行表 | 0 张 |
| Runtime 控制 MCP 工具 | 0 个 |
| 公开业务 MCP 工具 | 保持 27 个，除非代码现状另有明确业务变更 |
| Job 状态 Recovery 覆盖 | 所有枚举状态 100% |
| Recovery 动作有效性 | 100% 指向已注册公开工具 |
| B/C 生产代码引用 | 0 个 |
| 全量 Python 测试 | 通过 |
| 前端 TypeScript/Vite build | 通过 |
| 现有业务数据行数 | 改造前后不减少 |
| Hermes 重复创建已存在 Job | 4 条 E2E 中为 0 |

生产观察期为至少 20 次真实 Hermes 请求，并记录：一次完成率、人工介入率、重复 Job 数、Recovery 动作遵循率和错误分类分布。样本不足前不重新讨论内部 Runtime。

## 12. 风险与缓解

| 风险 | 后果 | 缓解 |
|---|---|---|
| 粗粒度回退混合文件 | 浏览器回退或 PG 修复被误删 | 只用精确 patch；按保护清单逐文件复核 |
| 删除迁移但数据库仍在 012 | 服务启动时无法解析 revision | 部署前强制 preflight；按环境分流处理 |
| 直接清理 Agent 表 | 误删实验记录或操作错误 schema | 备份、停服务、计数为 0、人工确认后执行 |
| Recovery 给出错误动作 | Hermes 自动扩大错误 | 只允许公开工具；高风险错误不返回自动动作 |
| 重试延迟过短 | 429/反爬被放大 | 指数延迟并受 `max_retries` 限制 |
| 重试延迟过长 | 用户感知任务卡住 | 暴露 `next_retry_at` 和 poll 时间 |
| Hermes 不自动读取 Resource | Manifest 无法直接改善选择 | 在 Hermes 系统提示或 MCP 初始化流程中显式要求读取；E2E 验证 |
| 删除 Runtime 后未来真有长任务需求 | 需要重新设计 | 保留历史文档和 Git 历史，不保留不可达生产代码 |

## 13. 回滚方案

### 13.1 代码回滚

改造应形成一个独立 Conventional Commit。若候选版本失败，回退该提交即可恢复改造前工作区代码；不得用 `git reset --hard` 处理包含其他用户改动的工作区。

### 13.2 数据库回滚

- NAS 若从未运行 009-012：无数据库回滚动作。
- 若已清理空 Agent 表：恢复到 B/C 代码前，需要先恢复包含 009-012 的代码并重新执行迁移；这些表原本为空，不恢复表数据。
- 业务表发生任何非预期变化：停止服务并使用改造前备份恢复，不继续自动修复。

### 13.3 功能降级

若 Job Recovery 导致 Hermes 行为异常，可暂时保留新增字段但从 Hermes 提示中停用自动动作；旧客户端会忽略新增字段，Web 和 Job 执行不受影响。

## 14. 决策记录

**Decision：** 采用“1.0 核心 + Phase A+”，Hermes 作为唯一 LLM Agent，Yamibo 只提供 Capability、Job 执行和结构化 Recovery。

**Drivers：**

1. 单用户 NAS 项目需要低维护成本。
2. 当前主要失败来自反爬暂停和权限条件，确定性恢复优于第二个模型。
3. 现有 Job/Daemon 已覆盖持久化、lease、事件和大多数自动恢复基础。

**Alternatives considered：**

- 保留完整 B/C：功能最完整，但重复 Hermes 规划并增加大量维护面，不采用。
- 仅关闭 Runtime feature flag 但保留代码和表：短期容易，长期仍需维护迁移、依赖和安全面，不采用。
- 删除全部 A/B/C 回到 1.0：最简单，但无法改善 Hermes 对副作用、幂等性和错误恢复的理解，不采用。

**Consequences：**

- 不再需要为 Yamibo Runtime 配置第二个 OpenAI-compatible 模型。
- 不提供离线多步目标 Run、审批和模型轨迹审计。
- Hermes 的可靠性取决于 Capability/Recovery 合同质量和 Hermes 是否遵循合同。
- 代码和部署复杂度显著下降，真实故障恢复能力反而更贴近当前问题。

**重新评估 Runtime 的触发条件：** 观察至少 20 次真实请求后，仍有超过 20% 的请求因为多步规划错误而失败，或出现必须在 Hermes 断开后继续规划的长时间研究任务。即使触发，也只设计一套多步 Runtime，不恢复单步 Run。

## 15. 预期交付拆分

为降低审查和回滚风险，建议拆成三个提交：

1. `refactor: 移除未发布的 Agent Runtime B/C`
2. `feat: 增加 Job 结构化恢复与延迟重试`
3. `docs: 同步 Phase A+ 架构与 Hermes 工作流`

数据库本地清理不放入代码提交，不自动作用于 NAS。每个提交都必须保持可测试；第二个提交完成前，第一个提交仍应保留 1.0 原有 `read_job` 行为。

## 16. 当前代码证据索引

以下位置用于实施前复核；行号以 2026-08-08 当前工作区为准，改造后会变化：

| 结论 | 当前证据 |
|---|---|
| Daemon 同时推进五条 Agent Run 路径 | `src/yamibo_mcp/daemon/runner.py:77-91` |
| MCP 同时注册公开工具和 Runtime 控制工具 | `src/yamibo_mcp/server/mcp_registry.py:69-71` |
| MCP Resource 同时暴露 Capability 和 Agent Run | `src/yamibo_mcp/server/mcp_registry.py:100-108` |
| Resource dispatch 存在 Agent Run 分支 | `src/yamibo_mcp/server/resources.py:57-66` |
| Phase A Capability URI 已独立存在 | `src/yamibo_mcp/server/resource_uris.py:78-87` |
| Manifest 由公开工具注册构建 | `src/yamibo_mcp/server/capabilities.py:31-54` |
| Manifest 当前包含未被 Phase A+ 需要的 profile/citation/artifact | `src/yamibo_mcp/server/capabilities.py:46-87` |
| Capability 元数据和 Job terminal read 已实现 | `src/yamibo_mcp/server/agent_tools.py:533-610` |
| Runtime 控制工具与公开 Capability 分离但仍可调用 | `src/yamibo_mcp/server/agent_tools.py:901-1003` |
| Job payload 已有状态、错误和诊断，但没有结构化 Recovery | `src/yamibo_mcp/server/schemas.py:62-202` |
| `retry_later()` 当前立即清空 lease，无法延迟 | `src/yamibo_mcp/db/repositories/jobs.py:637-695` |
| acquire 已天然支持 lease not-before 判断 | `src/yamibo_mcp/db/repositories/jobs.py:387-418` |
| 过期 running Job 已能转 interrupted 后重新领取 | `src/yamibo_mcp/db/repositories/jobs.py:863-901` |
| 009-012 历史 revision | 由兼容占位迁移保留地址，不再创建或维护 Runtime 功能 |
| 当前本地数据库 revision | 备份后已由 `012_add_agent_artifact_hashes` 升级到 `013_remove_legacy_agent_runtime` |
| 当前 Agent 表数据 | 迁移前六张表逐表 `COUNT(*)` 均为 0，迁移后六张表已不存在 |

这些证据说明：删除 B/C 的主要风险在混合工作区和迁移 revision，而 Phase A+ 的最低成本实现点已经存在于 Job payload、lease 和 Daemon 错误分类中，无需引入新的运行时框架。
