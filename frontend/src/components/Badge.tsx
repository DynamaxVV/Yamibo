import type { ReactNode } from 'react'
import { useI18n } from '../context/I18nContext'

const KIND_MAP: Record<string, string> = {
  succeeded: 'ok', complete: 'ok', valid: 'ok', downloaded: 'ok',
  running: 'accent', queued: 'accent', retrying: 'accent', pending: 'accent',
  partial: 'warn',
  failed: 'error', error: 'error', interrupted: 'error', missing: 'error',
}

const CONTENT_KIND_CLASSES: Record<string, string> = {
  comic: 'badge-comic',
  novel: 'badge-novel',
  discussion: 'badge-discussion',
  mixed: 'badge-mixed',
}

export function Badge({ status, children }: { status?: string | null; children?: ReactNode }) {
  const { t } = useI18n()
  const kind = status ? (KIND_MAP[status.toLowerCase()] || 'muted') : 'muted'
  const label = status ? t(status) : '-'
  return <span className={`badge badge-${kind}`}>{children || label}</span>
}

export function ContentBadge({ kind }: { kind: string | null }) {
  const { t } = useI18n()
  if (!kind) return <span className="badge badge-muted">-</span>
  const cls = CONTENT_KIND_CLASSES[kind]
  if (cls) {
    return <span className={`badge ${cls}`}>{t(kind)}</span>
  }
  return <span className="badge badge-accent">{kind}</span>
}
