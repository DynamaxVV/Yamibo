# Agent 友好型接口路线图：1.1 核心 + Phase A+

> 当前状态：Phase A+ 已实现，NAS 发布待执行
> 更新日期：2026-08-16
> 决策：Hermes 是唯一 LLM Agent；Yamibo 不再保留未发布的 Phase B/C 内部 Agent Runtime。

## 当前边界

Yamibo 的 Agent 面只提供三类能力：

1. `yamibo://schema/capabilities`：从 `PUBLIC_AGENT_TOOLS` 派生公开业务工具的参数、副作用、成本、幂等性、followup 和 Job 终态读取规则。
2. MCP 公开业务工具：远端只读、本地只读、创建后台 Job、读取 Job 和读取归档内容。
3. Job Recovery：`read_job` / `wait_for_job` 在 `data.recovery` 中返回结构化恢复建议。

不提供：

- 内建模型 tool loop。
- Agent Run / Step / Approval / Artifact / Citation 专用状态机。
- Runtime 控制 MCP 工具。
- Agent Run Web API、HMAC gateway、Run Detail 页面或 Web Chat Runtime 模式。

## Capability Manifest

保留 `yamibo://schema/capabilities`，继续兼容 `yamibo://schema/tools`。Manifest 覆盖全部公开业务工具，并保留：

- `schema_version`
- capability 名称、描述、`input_schema`、`output_schema`
- `effect`
- `risk`
- `idempotency`
- `requires`
- `cost_hints`
- `produces`
- `followups`
- `timeout_class`
- `terminal_read`（仅 `enqueue_job` 工具）

Manifest 不再包含 Phase B/C 才消费的 profiles、citation contract 或 artifact contract。

## Job Recovery

`read_job.data` 和 `wait_for_job.data` 增加：

- `retry_count`
- `max_retries`
- `next_retry_at`：仅 `retrying` 时由现有 `lease_until` 映射
- `recovery`

`recovery.classification` 固定为：

- `completed`
- `use_partial_result`
- `continue_waiting`
- `automatic_retry`
- `automatic_recovery`
- `user_action_required`
- `inspect_failure`
- `stopped`

Hermes 应优先执行 `recovery.next_actions`。当 `requires_user_action=true` 时，停止工具循环并向用户报告；当 `classification` 是 `automatic_retry` 或 `automatic_recovery` 时，等待并复查原 `job_id`，不要创建重复 Job。

## 延迟重试

Job Repository 复用现有 `lease_until` 作为 retry not-before 时间，不新增业务表或调度依赖。为兼容已经运行过 B/C 的数据库，迁移链保留 009-012 的空兼容节点，并由 013 清理旧 Runtime 表；这不恢复 B/C 运行时。Daemon 仍是唯一决定是否重试的组件；Hermes 只读取 `next_retry_at` 和 `recovery`。

## B/C 决策

Phase B/C 的单步 Run、多步 Runtime、审批、artifact/citation 和 Web 控制面已作为未发布过度设计移除。重新评估内建 Runtime 的触发条件是：至少 20 次真实 Hermes 请求后，仍有超过 20% 的请求因多步规划错误失败，或出现必须在 Hermes 断开后继续规划的长时间研究任务。

即使重新评估，也应重新设计一套单一 Runtime，不恢复已删除的双状态机实现。
