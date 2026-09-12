# 外部 Agent 接口边界与后续评估

> 当前状态：Capability Manifest + Job Recovery 已实现；内部 Agent Runtime 暂缓
> 更新日期：2026-09-08
> 适用范围：个人 NAS、单用户、外部 Codex/Hermes 等终端

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

## 内部 Runtime 暂缓

Phase B/C 的单步 Run、多步 Runtime、审批、artifact/citation 和 Web 控制面不属于当前产品范围。重新评估内建 Runtime 的触发条件是：实际使用中出现持续的多步任务失败，或确实需要外部终端断开后继续运行的长任务；在此之前不维护第二套规划状态机。

即使重新评估，也应重新设计一套单一 Runtime，不恢复已删除的双状态机实现。

## 个人 NAS 的接口方向

外部终端应优先使用现有 MCP tool/resource 或 JSON CLI。当前已提供只读的 `read_system_status`、`read-system-status` 和 `GET /api/system/status`，返回应用版本、数据库探测、带新鲜度的 Worker 心跳、Job 状态摘要、采集时间和明确的 `not_checked`；接口请求不隐含模型总结、全盘扫描、数据库迁移或远程抓取。应用启动是否自动应用待处理 schema migration，属于部署门禁，见部署运维指南。

知识库、RAG 和讨论趋势不参与基础状态检查。它们只在用户明确提出研究问题时使用，索引和分析失败不应阻塞归档、阅读、导出或 Job 恢复。
