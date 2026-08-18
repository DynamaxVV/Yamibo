import { useEffect, useRef, useState } from 'react'
import { useI18n } from '../../context/I18nContext'
import { formatContent, type ChatEvent, type ChatMessage } from '../../types/chat'
import { ChatMessageView } from './ChatMessageView'
import { ChatToolEvent } from './ChatToolEvent'

type ToolEvent = Extract<ChatEvent, { type: 'tool.started' | 'tool.completed' }>

function toolCallRows(messages: ChatMessage[]) {
  return messages.flatMap(message => (Array.isArray(message.tool_calls) ? message.tool_calls : [])).map((call, index) => {
    const record = call && typeof call === 'object' ? call as Record<string, unknown> : {}
    const fn = record.function && typeof record.function === 'object' ? record.function as Record<string, unknown> : {}
    const name = String(fn.name || record.name || record.tool_name || 'tool')
    const argumentValue = fn.arguments ?? record.arguments
    const preview = argumentValue === undefined ? '' : formatContent(argumentValue).replace(/\s+/g, ' ').trim().slice(0, 180)
    return { key: `${String(record.id || record.call_id || name)}-${index}`, name, preview }
  })
}

type Props = {
  content?: string
  toolCallMessages?: ChatMessage[]
  toolEvents?: ToolEvent[]
  toolMessages?: ChatMessage[]
  defaultOpen?: boolean
  streaming?: boolean
}

export function ChatReasoning({ content, toolCallMessages = [], toolEvents = [], toolMessages = [], defaultOpen = false, streaming = false }: Props) {
  const { t } = useI18n()
  const hasToolContent = toolCallMessages.length > 0 || toolEvents.length > 0 || toolMessages.length > 0
  const hasContent = Boolean(content) || hasToolContent
  const callRows = toolCallRows(toolCallMessages)
  const scrollRef = useRef<HTMLDivElement>(null)
  const [open, setOpen] = useState(defaultOpen)

  useEffect(() => {
    // As soon as the final assistant stream starts, the reasoning/tool panel
    // should give way to the answer.  Keep the historical/default behavior
    // for non-streaming panels.
    if (streaming && !defaultOpen) setOpen(false)
    else if (defaultOpen) setOpen(true)
  }, [defaultOpen, streaming])

  useEffect(() => {
    if (!streaming || !open || !scrollRef.current) return
    scrollRef.current.scrollTop = scrollRef.current.scrollHeight
  }, [content, toolCallMessages, toolEvents, toolMessages, open, streaming])

  if (!hasContent) return null
  return <details className="chat-reasoning" open={open} onToggle={event => setOpen(event.currentTarget.open)}>
    <summary className="chat-reasoning-summary">
      <span>{t('chat_reasoning')}</span>
    </summary>
    <div className="chat-reasoning-scroll" ref={scrollRef}>
      {content && <pre>{content}</pre>}
      {hasToolContent && <div className="chat-reasoning-tools">
        {callRows.map(row => <div className="chat-tool-call" key={row.key}><span className="chat-tool-call-bullet" aria-hidden="true">●</span><strong>{row.name}</strong>{row.preview && <code>{row.preview}</code>}</div>)}
        {toolMessages.map((message, index) => <ChatMessageView key={message.id || `tool-message-${index}`} message={message} />)}
        {toolEvents.map(event => <ChatToolEvent key={event.seq} event={event} />)}
      </div>}
    </div>
  </details>
}
