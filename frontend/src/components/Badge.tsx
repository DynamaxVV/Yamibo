import type { ReactNode } from 'react'

const KIND_MAP: Record<string, string> = {
  succeeded: 'ok', complete: 'ok', valid: 'ok', downloaded: 'ok',
  running: 'accent', queued: 'accent', retrying: 'accent', pending: 'accent',
  partial: 'warn',
  failed: 'error', error: 'error', interrupted: 'error', missing: 'error',
}

const CONTENT_KIND_MAP: Record<string, { label: string; className: string }> = {
  comic: { label: '漫画', className: 'badge-comic' },
  novel: { label: '小说', className: 'badge-novel' },
  discussion: { label: '讨论', className: 'badge-discussion' },
  mixed: { label: '混合', className: 'badge-mixed' },
}

export function Badge({ status, children }: { status?: string | null; children?: ReactNode }) {
  const kind = status ? (KIND_MAP[status.toLowerCase()] || 'muted') : 'muted'
  return <span className={`badge badge-${kind}`}>{children || status || '-'}</span>
}

export function ContentBadge({ kind }: { kind: string | null }) {
  if (!kind) return <span className="badge badge-muted">-</span>
  const mapped = CONTENT_KIND_MAP[kind]
  if (mapped) {
    return <span className={`badge ${mapped.className}`}>{mapped.label}</span>
  }
  return <span className="badge badge-accent">{kind}</span>
}
