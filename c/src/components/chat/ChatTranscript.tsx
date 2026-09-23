import { useEffect, useRef, useState } from 'react'
import { formatContent, visibleStreamingAssistant, type ChatEvent, type ChatMessage } from '../../types/chat'
import { ChatActivityIndicator, type ChatActivityKind } from './ChatActivityIndicator'
import { ChatMessageView } from './ChatMessageView'
import { ChatReasoning } from './ChatReasoning'
import { useI18n } from '../../context/I18nContext'

type Props = { messages: ChatMessage[]; events: ChatEvent[]; assistant: string; pendingUser?: string; streamingEnabled?: boolean; streaming?: boolean }
type Activity = { kind: ChatActivityKind; label: string; detail?: string }
type ToolEvent = Extract<ChatEvent, { type: 'tool.started' | 'tool.completed' }>
type AssistantGroup = { messages: ChatMessage[]; toolCallMessages: ChatMessage[]; toolMessages: ChatMessage[]; anchor?: ChatMessage }

function messageReasoning(message?: ChatMessage): string {
  if (!message) return ''
  const values = [message.reasoning, message.reasoning_content]
    .map(value => formatContent(value).trim())
    .filter(Boolean)
  return values.filter((value, index) => !values.slice(0, index).some(saved => saved === value || saved.includes(value) || value.includes(saved))).join('\n')
}

function mergeReasoning(values: string[]): string {
  const merged: string[] = []
  for (const value of values) {
    const normalized = value.replace(/\s+/g, ' ').trim()
    if (!normalized || merged.some(saved => {
      const savedNormalized = saved.replace(/\s+/g, ' ').trim()
      return savedNormalized === normalized || savedNormalized.includes(normalized) || normalized.includes(savedNormalized)
    })) continue
    merged.push(value)
  }
  return merged.join('\n')
}

function buildAssistantGroups(messages: ChatMessage[]): AssistantGroup[] {
  const groups: AssistantGroup[] = []
  let current: AssistantGroup | undefined
  let anchor: ChatMessage | undefined
  for (const message of messages) {
    if (message.role === 'user' || message.role === 'system') {
      current = undefined
      anchor = message
      continue
    }
    if (message.role === 'assistant') {
      if (!current || (current.toolCallMessages.length === 0 && current.toolMessages.length === 0)) {
        current = { messages: [], toolCallMessages: [], toolMessages: [], anchor }
        groups.push(current)
      }
      current.messages.push(message)
      if (Array.isArray(message.tool_calls) && message.tool_calls.length > 0) current.toolCallMessages.push(message)
    } else if (message.role === 'tool') {
      if (!current) {
        current = { messages: [], toolCallMessages: [], toolMessages: [], anchor }
        groups.push(current)
      }
      current.toolMessages.push(message)
    }
  }
  return groups
}

function groupHasPanel(group: AssistantGroup, eventGroup?: AssistantGroup): boolean {
  return group.toolCallMessages.length > 0 || group.toolMessages.length > 0 || group === eventGroup
}

function lastAssistant(group: AssistantGroup, visible: Set<ChatMessage>): ChatMessage | undefined {
  return [...group.messages].reverse().find(message => visible.has(message))
}

export function ChatTranscript({ messages, events, assistant, pendingUser, streamingEnabled = true, streaming = false, activity }: Props & { activity?: Activity }) {
  const { tx } = useI18n()
  const ref = useRef<HTMLDivElement>(null)
  const previousMessages = useRef(messages)
  const [follow, setFollow] = useState(true)
  useEffect(() => { if (previousMessages.current !== messages && follow && ref.current) ref.current.scrollTop = ref.current.scrollHeight; previousMessages.current = messages }, [messages, follow])
  useEffect(() => { if (follow && ref.current) ref.current.scrollTop = ref.current.scrollHeight }, [events, assistant, pendingUser, follow])
  const persistedReasoning = messages.flatMap(message => [message.reasoning, message.reasoning_content]).map(value => formatContent(value).trim()).filter(Boolean).map(value => value.replace(/\s+/g, ' ').trim())
  const seenReasoning = new Set<string>()
  const reasoning = events
    .filter((event): event is Extract<ChatEvent, { type: 'reasoning.available' }> => event.type === 'reasoning.available')
    .map(event => formatContent(event.text).trim())
    .filter(value => {
      const key = value.replace(/\s+/g, ' ').trim()
      const alreadyPersisted = persistedReasoning.some(saved => saved === key || saved.includes(key) || key.includes(saved))
      if (!key || alreadyPersisted || seenReasoning.has(key)) return false
      seenReasoning.add(key)
      return true
    })
    .join('\n')
  const pendingAlreadyPersisted = Boolean(pendingUser && messages.some(message => message.role === 'user' && typeof message.content === 'string' && message.content === pendingUser))
  const visibleEvents = streamingEnabled ? events : []
  const toolEvents = visibleEvents.filter((event): event is ToolEvent => event.type === 'tool.started' || event.type === 'tool.completed')
  const toolMessages = messages.filter(message => message.role === 'tool')
  const groups = buildAssistantGroups(messages)
  const groupByMessage = new Map<ChatMessage, AssistantGroup>()
  groups.forEach(group => { group.messages.forEach(message => groupByMessage.set(message, group)); if (group.anchor) groupByMessage.set(group.anchor, group) })
  const visibleAssistant = visibleStreamingAssistant(assistant, streaming, streamingEnabled)
  // Persisted tool messages are authoritative after reconciliation; transient
  // rows would otherwise duplicate the same calls in the historical timeline.
  const historicalEvents = streaming || toolMessages.length > 0 ? [] : toolEvents
  const eventGroup = historicalEvents.length > 0 ? groups[groups.length - 1] : undefined
  const duplicateTerminalBlocks = new Set<ChatMessage>()
  groups.forEach(group => {
    // Hermes may leave multiple completed blocks in one user turn after a
    // parallel/re-entered run. Hide only earlier stop blocks in this group;
    // never dedupe across a new user message or alter the authoritative data.
    const completed = group.messages.filter(message => message.finish_reason === 'stop' && Boolean(formatContent(message.content).trim()))
    completed.slice(0, -1).forEach(message => duplicateTerminalBlocks.add(message))
  })
  const visibleMessages = messages.filter(message => {
    if (message.role === 'tool') return false
    if (duplicateTerminalBlocks.has(message)) return false
    const group = groupByMessage.get(message)
    return !(group && groupHasPanel(group, eventGroup) && message.role === 'assistant' && Array.isArray(message.tool_calls) && message.tool_calls.length > 0 && !formatContent(message.content).trim())
  })
  const visibleSet = new Set(visibleMessages)
  const historicalPanelVisible = groups.some(group => groupHasPanel(group, eventGroup)) || historicalEvents.length > 0
  const livePanelVisible = streaming && (Boolean(reasoning) || toolEvents.length > 0)
  const showActivity = Boolean(activity) && (!visibleAssistant || activity?.kind !== 'reply')
  const hasContent = visibleMessages.length > 0 || historicalPanelVisible || livePanelVisible || showActivity || Boolean(pendingUser && !pendingAlreadyPersisted) || Boolean(visibleAssistant) || visibleEvents.length > 0
  return <div className="chat-transcript" ref={ref} onScroll={e => { const el = e.currentTarget; setFollow(el.scrollHeight - el.scrollTop - el.clientHeight <= 80) }}>
    {!hasContent && <div className="chat-transcript-empty">{tx('暂无消息，发送内容开始对话', 'No messages yet. Send one to start the conversation.')}</div>}
    {visibleMessages.map((message, index) => {
      const group = groupByMessage.get(message)
      const groupLastAssistant = group ? lastAssistant(group, visibleSet) : undefined
      const attachPanel = !!group && groupHasPanel(group, eventGroup) && (groupLastAssistant === message || (!groupLastAssistant && group.anchor === message))
      const groupReasoning = group ? mergeReasoning(group.messages.map(item => messageReasoning(item)).filter(Boolean)) : ''
      const panel = attachPanel && group ? <ChatReasoning content={groupReasoning} toolCallMessages={group.toolCallMessages} toolEvents={group === eventGroup ? historicalEvents : []} toolMessages={group.toolMessages} /> : undefined
      return <ChatMessageView key={message.id || `message-${index}`} message={message} hideReasoning={!!group && groupHasPanel(group, eventGroup) && Boolean(messageReasoning(message))} hideToolCalls={!!group && groupHasPanel(group, eventGroup) && Boolean(message.tool_calls)} extra={panel} />
    })}
    {pendingUser && !pendingAlreadyPersisted && <ChatMessageView message={{ role: 'user', content: pendingUser }} />}
    {(visibleAssistant || livePanelVisible) && <ChatMessageView message={{ role: 'assistant', content: visibleAssistant }} streaming={streaming} extra={livePanelVisible ? <ChatReasoning content={reasoning} toolEvents={toolEvents} defaultOpen={!visibleAssistant} streaming /> : undefined} />}
    {showActivity && activity && <ChatActivityIndicator kind={activity.kind} label={activity.label} detail={activity.detail} />}
    {groups.filter(group => groupHasPanel(group, eventGroup) && !lastAssistant(group, visibleSet) && !group.anchor).map((group, index) => <ChatReasoning key={`orphan-reasoning-${index}`} content={mergeReasoning(group.messages.map(message => messageReasoning(message)).filter(Boolean))} toolCallMessages={group.toolCallMessages} toolEvents={group === eventGroup ? historicalEvents : []} toolMessages={group.toolMessages} />)}
    {historicalEvents.length > 0 && !eventGroup && <ChatReasoning toolEvents={historicalEvents} />}
  </div>
}
