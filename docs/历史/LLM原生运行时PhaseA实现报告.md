# LLM 原生运行时 Phase A 实现报告

> 状态：已完成
>
> 基线版本：1.0.0 之后的 Phase A 契约加固
>
> 完成日期：2026-07-14

## 1. 目标与边界

Phase A 的目标是让 MCP/LLM 客户端无需依赖项目 Markdown，即可从机器接口可靠判断公共能力的参数、效果、成本、重试语义、异步 Job 的终态读取方式，以及引用和 artifact 的最小数据形状。

Phase A 自身只增加契约、资源和测试基线，不依赖 `agent_runs`、`agent_steps`、内建 tool loop、planner 或多 Agent 才能成立；不会改变已有 CLI/MCP 工具的业务行为，也不会开放新的远程端口。后续阶段即使已在开发，也不计入本报告的完成证据。

路线图定义见 [LLM 原生运行时路线图](../LLM原生运行时路线图.md)。

## 2. 交付内容

### 2.1 Capability Manifest

新增 `yamibo://schema/capabilities`，由 `PUBLIC_AGENT_TOOLS` 在运行时生成。它不引入 YAML、Markdown 或第二份工具清单；工具名称、描述和函数签名仍以现有 MCP 注册源为准。

每个 capability 均包含：

- `name`、`version`、`description`
- `input_schema` 与稳定的 `AgentResult` 输出包络 schema
- `effect`、`risk`、`idempotency`、`requires`
- `cost_hints`、`produces`、`followups`、`timeout_class`
- 对 `enqueue_job` 能力，附带 `terminal_read`：Job ID 路径、`read_job`、`yamibo://jobs/{job_id}/status` 和 `data.result_ready`

当前公共工具覆盖率为 27/27：10 个 `enqueue_job`、4 个 `remote_read`、13 个 `read_only`。公开 effect 枚举为：

```text
read_only | remote_read | enqueue_job | mutating | destructive
```

风险等级为：

```text
read | remote_read | write | sensitive_write | destructive
```

`yamibo://schema/tools` 保留为兼容资源，但只提供简化的参数信息。客户端应优先消费 `yamibo://schema/capabilities`。

兼容策略：

- `schema_version` 使用整数主版本字符串。新增可选字段不升主版本；删除字段、修改字段类型或改变既有字段语义必须升主版本。
- capability 的 `version` 独立演进。新增可选参数或更精确的说明保持当前版本；修改 effect/risk、删除参数、改变必填条件或改变输出语义必须升 capability 版本。
- 客户端必须忽略未知字段，但必须拒绝不支持的 `schema_version` 或 capability `version`，不能猜测执行。
- `yamibo://schema/tools` 在 v1 兼容期继续保留；它不包含副作用和幂等语义，不得作为自主执行的唯一依据。

公共注册记录是名称、描述、handler 和 manifest 元数据的唯一代码来源。构建 manifest 时会拒绝缺失元数据、未知 followup、非法 effect/risk 组合、非法幂等模式、缺失 Job 终态读取面，以及 destructive 公共能力。

### 2.2 输入、重试与终态语义

输入 schema 由 Python 函数签名派生，保留必填参数、默认值、数组、对象和可空类型。额外表达已有业务约束：

- `create_thread_archive_job` 要求 `tid`、`url` 或 `html_path` 之一。
- `get_discussion_topic_evidence` 要求 `topic_id` 或 `topic_label` 之一。
- 批量 Job 的 `terminal_read.job_id_paths` 同时指出新建和复用 Job 的 ID 列表。
- 已有活跃 Job 的能力声明去重范围；讨论趋势和报告 Job 声明为 `not_deduplicated`，禁止自动重试；强制 RAG 索引也明确提示不会复用活跃 Job。

因此，客户端可以区分“安全重试”“活跃 Job 去重”和“不可自动重试”，而不会把一次 Job 创建误判为业务完成。

### 2.3 Citation 与 Artifact 最小契约

manifest 暴露版本化 citation 和 artifact contract，并新增对应的 application dataclass：

```json
{
  "resource_uri": "yamibo://threads/572313/posts",
  "snapshot_id": "archive-or-index-version",
  "locator": {"pid": 123456, "floor": 8},
  "excerpt_hash": "sha256 hex",
  "retrieved_at": "RFC 3339 timestamp"
}
```

该阶段定义契约，不在现有业务响应中伪造 citation。实际 run/step 持久化和证据写入仍属于后续阶段。

### 2.4 NAS 最小安全 Profile

manifest 提供声明性 profile，而不替代现有反向代理、VPN 或 identity-aware proxy：

| Profile | 主体 | 能力面 |
|---|---|---|
| `nas_web_admin` | `authenticated_web_admin` | 全部当前公共非破坏性能力 |
| `nas_remote_mcp` | `authenticated_mcp_client` | 全部当前公共非破坏性能力 |
| `nas_daemon` | `daemon_service_account` | 无 MCP capability；只消费已持久化 Job |

当前公共 catalog 不含 destructive capability；profile 是未来 policy/approval 的输入，不是本阶段新增的认证或授权执行层。Web、MCP 和 PostgreSQL 的默认 loopback 边界不变。

## 3. 实现位置

| 组件 | 位置 | 说明 |
|---|---|---|
| Manifest 生成 | `src/yamibo_mcp/server/capabilities.py` | 从 `PUBLIC_AGENT_TOOLS` 派生 schema 和元数据 |
| MCP resource | `src/yamibo_mcp/server/resources.py` | 读取 `yamibo://schema/capabilities` |
| URI 与 MCP 注册 | `src/yamibo_mcp/server/resource_uris.py`、`src/yamibo_mcp/server/mcp_registry.py` | 解析、内容类型和 MCP resource 暴露 |
| Fixture 与测试 | `tests/unit/test_server/test_capabilities.py` | manifest 覆盖、schema、followup 和 destructive 禁止回归 |

## 4. 评测基线

Phase A fixture 共 8 个场景：

1. 搜索 -> 创建 Job -> `read_job` 终态 -> 读取归档并引用。
2. 远端只读预览。
3. 远端失败但不产生写入。
4. 重复创建复用活跃 Job。
5. Job failed 后读取 `read_job_events`。
6. Job interrupted 后读取 `read_job_events`。
7. 分页归档资源使用 cursor 后续读取。
8. 本地搜索在零远端请求预算下执行。

离线 evaluator 会拒绝未知 capability、effect 与 manifest 不一致、未到 `result_ready` 就读取归档、failed/interrupted 缺少事件诊断、遗漏分页 cursor、超出远端预算，以及不符合最小契约的 citation。

## 5. 验收证据

实施完成时的验证结果：

```text
uv run pytest
1140 passed, 13 skipped
```

并额外验证：

- `uv run pytest tests/unit/test_server/test_capabilities.py tests/unit/test_server/test_resources.py tests/unit/test_server/test_agent_interface.py tests/integration/test_agent_workflows.py`：70 passed
- `git diff --check`：通过
- CLI 与直接资源入口均可读取 `yamibo://schema/capabilities`：返回 schema v1、27 个 capability（13 `read_only`、4 `remote_read`、10 `enqueue_job`）

## 6. 后续边界

Phase A 仅稳定外部 Agent 的操作面。持久化 run/step、policy decision、approval、单步 executor、Web 可观察性和自主 tool loop 属于 Phase B/C，必须按各自验收条件另行核查；本报告不对其完成度作背书。
