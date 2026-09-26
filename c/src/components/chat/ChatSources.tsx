import { Link } from 'react-router-dom'
import { useI18n } from '../../context/I18nContext'
import type { ChatCitation } from '../../types/chat'
import '../../styles/chat-sources.css'

/** Only server-provided receipts may become navigable sources. */
export function chatSources(citations: ChatCitation[] | undefined): ChatCitation[] {
  const seen = new Set<string>()
  return (citations || []).filter((citation) => {
    if (!/^[a-f0-9]{32}$/.test(citation.receipt_id) || seen.has(citation.receipt_id)
      || !Number.isSafeInteger(citation.tid) || citation.tid <= 0
      || !Number.isSafeInteger(citation.pid) || citation.pid <= 0
      || citation.source_url !== `/threads/${citation.tid}#pid-${citation.pid}`) return false
    seen.add(citation.receipt_id)
    return true
  })
}

export function formatCitationMarkers(content: string, citations: ChatCitation[], label: string): string {
  const numbers = new Map(citations.map((citation, index) => [citation.receipt_id, index + 1]))
  return content.replace(/\[来源:([a-f0-9]{32})\]/g, (marker, receiptId: string) => {
    const number = numbers.get(receiptId)
    return number === undefined ? marker : `[${label} ${number}]`
  })
}

export function ChatSources({ citations }: { citations: ChatCitation[] }) {
  const { tx } = useI18n()
  if (!citations.length) return null
  return (
    <section className="chat-sources" aria-label={tx('回答来源', 'Answer sources')}>
      <div className="chat-sources-title">{tx('回答来源', 'Answer sources')}</div>
      <ol className="chat-sources-list">
        {citations.map((citation, index) => (
          <li key={citation.receipt_id}>
            <Link to={citation.source_url}>
              {tx('来源', 'Source')} {index + 1} · {tx('帖子', 'Thread')} {citation.tid} · {tx('原回复', 'Reply')} {citation.pid}
            </Link>
            <details>
              <summary>{tx('查看已读片段', 'View read excerpt')} · {tx('段落', 'Paragraphs')} {citation.paragraph_range.start}–{citation.paragraph_range.end}</summary>
              {citation.truncated && <p className="chat-source-truncated">{tx('此来源只读取了部分内容；以下片段不代表完整回复。', 'Only part of this source was read; this excerpt is not the full reply.')}</p>}
              <pre>{citation.content}</pre>
            </details>
          </li>
        ))}
      </ol>
    </section>
  )
}
