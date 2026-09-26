import { useEffect, useRef, useState } from 'react'
import { useI18n } from '../../context/I18nContext'
import { type ChatEvent, type ChatMessage } from '../../types/chat'
import { buildToolSteps, preparationTools, stepError } from './toolTimeline'
import { ChatToolEvent } from './ChatToolEvent'

type ToolEvent = Extract<ChatEvent, { type: 'tool.started' | 'tool.completed' }>

type Props = {
  content?: string
  toolCallMessages?: ChatMessage[]
  toolEvents?: ToolEvent[]
  toolMessages?: ChatMessage[]
  defaultOpen?: boolean
  streaming?: boolean
}

export function ChatReasoning({ content, toolCallMessages = [], toolEvents = [], toolMessages = [], defaultOpen = false, streaming = false }: Props) {
  const { t, tx } = useI18n()
  const hasToolContent = toolCallMessages.length > 0 || toolEvents.length > 0 || toolMessages.length > 0
  const hasContent = Boolean(content) || hasToolContent
  const steps = buildToolSteps(toolEvents, toolCallMessages, toolMessages)
  const preparation = steps.filter(step => preparationTools.has(step.name) && !stepError(step))
  const operations = steps.filter(step => !preparationTools.has(step.name) || Boolean(stepError(step)))
  const failures = steps.filter(step => Boolean(stepError(step))).length
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
      <span>{t('chat_reasoning')}</span><span className="chat-execution-count">{tx(`${operations.length} 项操作`, `${operations.length} operations`)}{failures > 0 && <span className="chat-error"> · {tx(`${failures} 项失败`, `${failures} failed`)}</span>}</span>
    </summary>
    <div className="chat-reasoning-scroll" ref={scrollRef}>
      {hasToolContent && <div className="chat-reasoning-tools">
        {operations.map(step => <ChatToolEvent key={step.key} step={step} streaming={streaming} />)}
        {preparation.length > 0 && <details className="chat-execution-preparation"><summary>{tx(`准备工作 · ${preparation.length} 次调用`, `Preparation · ${preparation.length} calls`)}</summary>{preparation.map(step => <ChatToolEvent key={step.key} step={step} streaming={streaming} />)}</details>}
      </div>}
      {content && <details className="chat-execution-preparation"><summary>{tx('模型思考', 'Model reasoning')}</summary><pre>{content}</pre></details>}
    </div>
  </details>
}
