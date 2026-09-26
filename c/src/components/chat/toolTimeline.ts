import type { ChatEvent, ChatMessage } from '../../types/chat'

export type ToolEvent = Extract<ChatEvent, { type: 'tool.started' | 'tool.completed' }>
export type ToolStep = { key: string; name: string; args?: unknown; result?: unknown; error?: unknown; finished: boolean; callId?: string; runId?: string }
export function record(value: unknown): Record<string, unknown> {
  if (typeof value === 'string') { try { return record(JSON.parse(value)) } catch { return {} } }
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}
}
export function stepError(step: ToolStep): unknown {
  const result = record(step.result)
  return step.error ?? (result.ok === false ? result.error || 'Tool returned ok: false' : undefined)
}
export const preparationTools = new Set(['discover_public_tools', 'describe_public_tool', 'list_project_skills', 'read_project_skill', 'read_forum_profiles'])
const labels: Record<string, [string, string]> = {
  browse_forum_page: ['浏览论坛板块', 'Browse forum'], search_threads: ['搜索远端帖子', 'Search remote threads'],
  find_discussions: ['查找讨论', 'Find discussions'], read_discussion_source: ['阅读讨论原文', 'Read discussion source'],
  search_archived_content: ['搜索归档内容', 'Search archives'], probe_archived_threads: ['核对归档状态', 'Check archives'],
  read_archived_thread: ['阅读归档帖子', 'Read archived thread'], create_thread_archive_job: ['创建归档任务', 'Create archive job'],
  read_job: ['查看任务状态', 'Read job status'], wait_for_job: ['等待任务结果', 'Wait for job'],
  discover_public_tools: ['查找可用工具', 'Discover tools'], describe_public_tool: ['读取工具说明', 'Read tool instructions'],
  list_project_skills: ['查找项目技能', 'Find project skills'], read_project_skill: ['读取技能指导', 'Read skill guidance'],
  read_forum_profiles: ['核对板块信息', 'Check forum profiles'],
}
export function toolLabel(name: string, tx: (zh: string, en: string) => string): string { return labels[name] ? tx(...labels[name]) : name }

/** Events and persisted messages are alternative representations of the same calls. */
export function buildToolSteps(events: ToolEvent[], calls: ChatMessage[], results: ChatMessage[]): ToolStep[] {
  const steps: ToolStep[] = []
  if (events.length) {
    for (const event of events) {
      if (event.type === 'tool.started') {
        steps.push({ key: `${event.run_id}-${event.seq}`, name: event.tool, args: event.preview, finished: false, callId: event.call_id, runId: event.run_id })
      } else {
        // Without IDs the event protocol only permits FIFO matching within a run/name.
        const step = steps.find(item => !item.finished && item.runId === event.run_id && (event.call_id ? item.callId === event.call_id : item.name === event.tool))
        if (step) Object.assign(step, { result: event.result, error: event.error, finished: true })
        else steps.push({ key: `${event.run_id}-${event.seq}`, name: event.tool, result: event.result, error: event.error, finished: true })
      }
    }
    return steps
  }
  for (const message of calls) for (const raw of message.tool_calls || []) {
    const call = record(raw), fn = record(call.function)
    const args = fn.arguments ?? call.arguments ?? call.args
    const wrapper = record(args)
    const name = String(fn.name || call.name || call.tool_name || 'tool')
    steps.push({ key: `call-${steps.length}`, callId: String(call.id || call.call_id || call.tool_call_id || ''), name: name === 'call_public_tool' ? String(wrapper.tool_name || wrapper.name || name) : name, args: name === 'call_public_tool' ? wrapper.arguments : args, finished: false })
  }
  for (const message of results) {
    const step = message.tool_call_id ? steps.find(item => !item.finished && item.callId === message.tool_call_id) : steps.find(item => !item.finished && item.name === message.tool_name)
    if (step) Object.assign(step, { result: message.content, finished: true })
    else steps.push({ key: `result-${steps.length}`, name: message.tool_name || 'tool', result: message.content, finished: true })
  }
  return steps
}
