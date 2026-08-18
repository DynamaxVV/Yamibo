export type ChatRole = 'system' | 'user' | 'assistant' | 'tool' | 'unknown'
export type ChatRunStatus = 'preparing'|'submitting'|'queued'|'running'|'waiting_for_approval'|'stopping'|'reconciling'|'completed'|'failed'|'cancelled'|'unknown'
export type ApprovalChoice = 'once'|'session'|'always'|'deny'
export type ChatError = { code: string; message: string; retryable?: boolean; details?: unknown }
export type ChatContent = unknown
export type ChatMessage = { id?: string; role: ChatRole; content: ChatContent; tool_call_id?: string|null; tool_calls?: unknown[]; tool_name?: string|null; reasoning?: string|null; reasoning_content?: string|null; finish_reason?: string|null; timestamp?: string }
export type ChatSession = { id: string; title: string; preview?: string|null; message_count?: number; created_at?: string; updated_at?: string; last_active?: string|null; active_run_id?: string|null }
export type ChatRun = { run_id: string; session_id: string; status: ChatRunStatus; last_seq?: number; error?: ChatError|null; terminal_payload?: unknown; stop_requested?: boolean }
export type ChatContext = { ready: boolean; transport?: string; model?: string; streaming_enabled?: boolean; error?: ChatError|null; hermes?: { endpoint?: string; connected?: boolean; version?: string; has_api_key?: boolean; capabilities?: string[] } }
export type ChatSessionPage = { sessions?: ChatSession[]; items?: ChatSession[]; total?: number; has_more?: boolean }
export type StartRunResponse = { run_id: string; session_id: string; status: ChatRunStatus; events_url: string }
export type ChatEventBase = { seq: number; run_id: string; type: string; timestamp: number }
export type MessageDeltaEvent = ChatEventBase & { type: 'message.delta'; delta: unknown; role?: ChatRole }
export type ToolStartedEvent = ChatEventBase & { type: 'tool.started'; tool: string; preview?: unknown; call_id?: string }
export type ToolCompletedEvent = ChatEventBase & { type: 'tool.completed'; tool: string; duration?: number; error?: unknown; call_id?: string }
export type ReasoningAvailableEvent = ChatEventBase & { type: 'reasoning.available'; text: unknown }
export type ApprovalRequestEvent = ChatEventBase & { type: 'approval.request'; choices: ApprovalChoice[]; summary?: unknown; tool?: unknown }
export type ApprovalRespondedEvent = ChatEventBase & { type: 'approval.responded'; choice: ApprovalChoice; resolved: boolean }
export type RunCompletedEvent = ChatEventBase & { type: 'run.completed'; output?: unknown; usage?: unknown }
export type RunFailedEvent = ChatEventBase & { type: 'run.failed'; error: ChatError }
export type RunCancelledEvent = ChatEventBase & { type: 'run.cancelled' }
export type StreamGapEvent = ChatEventBase & { type: 'stream.gap'; after_seq: number; available_from: number }
export type StreamErrorEvent = ChatEventBase & { type: 'stream.error'; error: ChatError }
export type SessionReconciledEvent = ChatEventBase & { type: 'session.reconciled'; session_id: string; message_count: number }
export type ChatEvent = MessageDeltaEvent|ToolStartedEvent|ToolCompletedEvent|ReasoningAvailableEvent|ApprovalRequestEvent|ApprovalRespondedEvent|RunCompletedEvent|RunFailedEvent|RunCancelledEvent|StreamGapEvent|StreamErrorEvent|SessionReconciledEvent
export type ChatState = { status: ChatRunStatus; lastSeq: number; assistant: string; events: ChatEvent[]; terminal: boolean; startedAt?: number; connected?: boolean; lastEventAt?: number }
export const TERMINAL_STATUSES: readonly ChatRunStatus[] = ['completed','failed','cancelled','unknown']
export const isTerminalStatus = (s: ChatRunStatus) => TERMINAL_STATUSES.includes(s)
/** Transient deltas are visible only while their stream is still active. */
export function visibleStreamingAssistant(assistant: string, streaming: boolean, streamingEnabled: boolean): string {
  return streaming && streamingEnabled ? assistant : ''
}
const eventStatus: Partial<Record<ChatEvent['type'], ChatRunStatus>> = { 'approval.request':'waiting_for_approval', 'approval.responded':'running', 'stream.gap':'reconciling', 'stream.error':'reconciling', 'run.completed':'completed', 'run.failed':'failed', 'run.cancelled':'cancelled' }
const canTransition: Record<ChatRunStatus, readonly ChatRunStatus[]> = { preparing:['submitting','queued','unknown'], submitting:['queued','running','failed','unknown'], queued:['running','stopping','failed','cancelled','unknown'], running:['waiting_for_approval','stopping','completed','failed','cancelled','reconciling','unknown'], waiting_for_approval:['running','stopping','completed','failed','cancelled','reconciling','unknown'], stopping:['completed','failed','cancelled','reconciling','unknown'], reconciling:['queued','running','waiting_for_approval','stopping','completed','failed','cancelled','unknown'], completed:['completed','reconciling'], failed:['failed','reconciling'], cancelled:['cancelled','reconciling'], unknown:['unknown','reconciling','queued','running','completed','failed','cancelled'] }
export function reduceChatEvent(state: ChatState, event: ChatEvent): ChatState {
  if (!Number.isFinite(event.seq) || event.seq <= state.lastSeq) return state
  const requested = eventStatus[event.type] ?? state.status
  const status = isTerminalStatus(state.status) && (event.type === 'stream.error' || event.type === 'stream.gap' || event.type === 'session.reconciled') ? state.status : canTransition[state.status].includes(requested) ? requested : state.status
  // Hermes can stream tool messages through the same delta event as the
  // assistant reply.  Only assistant (or legacy role-less) deltas belong in
  // the transient reply; tool deltas are rendered from the reconciled
  // message timeline and must not be shown a second time here.
  const assistant = event.type === 'message.delta' && (!event.role || event.role === 'assistant')
    ? state.assistant + (typeof event.delta === 'string' ? event.delta : formatContent(event.delta))
    : state.assistant
  return { ...state, assistant, status, lastSeq:event.seq, events:[...state.events,event], terminal:isTerminalStatus(status) }
}
export function formatContent(content: unknown): string { if (typeof content === 'string') return content; if (content == null) return ''; try { return JSON.stringify(content, null, 2) } catch { return String(content) } }
