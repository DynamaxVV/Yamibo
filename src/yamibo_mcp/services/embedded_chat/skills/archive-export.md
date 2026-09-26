# 归档、更新与导出

适用：用户明确要求归档、补全、更新或导出指定帖子。

1. 确认确切 TID、目标板块和所需模式；模糊目标先检索并请用户选定。先用 `probe_archived_threads` 核对本地状态。
2. 选中范围优先用 `propose_operation_plan`；公开创建 Job 工具须经宿主对精确参数确认。文字归档用 `mode=text_only`，需要图片用 `mode=full`。
3. 保存每个 TID 对应的原始 `job_id`。创建或复用 Job 不等于完成；用 `read_job` 或 `wait_for_job` 观察原 Job，不重复提交。
4. `result_ready` 后读取归档或导出结果；部分完成时说明缺口。错误先看 `code`、`retryable`、`agent_hint` 和 `recovery`。

## 定时归档

只有用户明确要求按时间自动归档**已确定的单个 TID**时，才考虑 `create_archive_schedule`。先说明准确的 TID、归档模式、可选板块、时区、单次时间或五字段 Cron，以及周期性执行会在未来持续创建归档 Job；宿主会对创建规则做一次精确参数审批。Cron 的分钟位只能是单个分钟值，频率不能高于每小时一次。不要把自然语言提示、任意 MCP 工具、URL、文件路径或凭据写入定时规则。

修改启停或手动触发前，用 `read_scheduled_task` 取得当前参数与 `revision`，并向用户复述动作及影响。触发后读取返回的原始 Job ID，再用 `read_job` 验收；定时触发记录为 `queued` 不代表归档已完成。现有日报仍由日报规则管理，不能用归档定时任务替代。
