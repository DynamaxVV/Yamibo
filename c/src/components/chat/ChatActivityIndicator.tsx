export type ChatActivityKind = 'connecting' | 'thinking' | 'tool' | 'reply' | 'stopping' | 'reconciling'

type Props = {
  kind: ChatActivityKind
  label: string
  detail?: string
}

export function ChatActivityIndicator({ kind, label, detail }: Props) {
  return <div className={`chat-activity chat-activity-${kind}`} role="status" aria-live="polite">
    <span className="chat-activity-mark" aria-hidden="true"><span /></span>
    <span className="chat-activity-copy">
      <span className="chat-activity-label">{label}</span>
      {detail && <span className="chat-activity-detail">{detail}</span>}
    </span>
    <span className="chat-activity-dots" aria-hidden="true"><i /><i /><i /></span>
    <span className="chat-activity-progress" aria-hidden="true" />
  </div>
}
