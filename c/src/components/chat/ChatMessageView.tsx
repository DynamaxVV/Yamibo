import { Markdown } from '../Markdown'
import type { ReactNode } from 'react'
import { formatContent, type ChatMessage } from '../../types/chat'
import { useI18n } from '../../context/I18nContext'

function compactToolResult(content: unknown) {
  const formatted = formatContent(content).trim()
  return {
    formatted,
    preview: formatted.replace(/\s+/g, ' ').slice(0, 180),
    expandable: formatted.length > 180 || formatted.includes('\n'),
  }
}

function comparableReasoning(value: string): string {
  return value.replace(/\s+/g, ' ').trim()
}

function reasoningText(value: unknown): string {
  return value === undefined || value === null ? '' : formatContent(value).trim()
}

function reasoningParts(message: ChatMessage, t: (key: string) => string) {
  const reasoning = reasoningText(message.reasoning)
  const reasoningContent = reasoningText(message.reasoning_content)
  if (!reasoning && !reasoningContent) return []
  if (reasoning && reasoningContent && comparableReasoning(reasoning) === comparableReasoning(reasoningContent)) {
    return [{ label: t('chat_meta_reasoning'), value: reasoning }]
  }
  return [
    reasoning ? { label: t('chat_meta_reasoning'), value: reasoning } : null,
    reasoningContent ? { label: t('chat_meta_reasoning_content'), value: reasoningContent } : null,
  ].filter(Boolean) as Array<{ label: string; value: string }>
}

export function ChatMessageView({ message, streaming = false, extra, hideReasoning = false, hideToolCalls = false }: { message: ChatMessage; streaming?: boolean; extra?: ReactNode; hideReasoning?: boolean; hideToolCalls?: boolean }) {
  const { t } = useI18n()
  const roles: Record<string, string> = {
    user: t('chat_role_user'),
    assistant: t('chat_role_assistant'),
    system: t('chat_role_system'),
    tool: t('chat_role_tool'),
  }
  const messageReasoning = reasoningParts(message, t)
  const toolResult = message.role === 'tool' && message.content !== undefined ? compactToolResult(message.content) : null
  // `stop` is Hermes/OpenAI's normal generation-completed reason, not a user
  // stop request. Keep unusual provider reasons available for diagnostics but
  // do not make every successful assistant message look cancelled.
  const visibleFinishReason = message.finish_reason && !['stop', 'tool_calls'].includes(message.finish_reason) ? message.finish_reason : null

  return (
    <article className={`chat-message chat-message-${message.role}${streaming ? ' chat-message-streaming' : ''}`}>
      <div className="chat-message-head">
        <span className="chat-role">{roles[message.role] || t('chat_role_unknown')}</span>
        {streaming && <span className="chat-streaming-chip"><span className="chat-streaming-dot" aria-hidden="true" />{t('chat_activity_reply')}</span>}
      </div>
      {message.content !== undefined && toolResult ? (
        <div className="chat-tool-result">
          <span className="chat-tool-result-label">{t('chat_tool_result')}</span>
          {toolResult.expandable ? (
            <details>
              <summary><code>{toolResult.preview}{toolResult.formatted.length > 180 ? '…' : ''}</code></summary>
              <pre className="chat-structured">{toolResult.formatted}</pre>
            </details>
          ) : (
            <code className="chat-tool-result-value">{toolResult.formatted || '—'}</code>
          )}
        </div>
      ) : message.content !== undefined ? (
        <>
          <Markdown content={formatContent(message.content)} />
          {streaming && <span className="chat-stream-caret" aria-hidden="true" />}
        </>
      ) : null}
      {!hideReasoning && messageReasoning.length > 0 && (
        <details className="chat-message-details">
          <summary>{t('chat_reasoning')}</summary>
          {messageReasoning.map((part) => <pre className="chat-structured" key={part.label}>{part.label}: {part.value}</pre>)}
        </details>
      )}
      {extra}
      {(message.tool_name || message.tool_call_id || visibleFinishReason) && (
        <div className="chat-meta">
          {message.tool_name && <span>{t('chat_meta_tool_name')}: {message.tool_name}</span>}
          {message.tool_call_id && <span>{t('chat_meta_tool_call_id')}: {message.tool_call_id}</span>}
          {visibleFinishReason && <span>{t('chat_meta_finish_reason')}: {visibleFinishReason}</span>}
        </div>
      )}
      {!hideToolCalls && message.tool_calls && (
        <details className="chat-message-details">
          <summary>{t('chat_meta_tool_calls')}</summary>
          <pre className="chat-structured">{formatContent(message.tool_calls)}</pre>
        </details>
      )}
    </article>
  )
}
