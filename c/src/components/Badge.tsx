import type { ReactNode } from 'react'
import { useI18n } from '../context/I18nContext'
import { cn } from '../lib/utils'

type BadgeKind = 'ok' | 'warn' | 'error' | 'accent' | 'muted'

const KIND_MAP: Record<string, BadgeKind> = {
  succeeded: 'ok',
  complete: 'ok',
  valid: 'ok',
  downloaded: 'ok',
  running: 'accent',
  queued: 'warn',
  retrying: 'accent',
  pending: 'accent',
  paused: 'warn',
  indexing: 'accent',
  partial: 'warn',
  excluded: 'warn',
  failed: 'error',
  error: 'error',
  interrupted: 'error',
  missing: 'error',
}

const BADGE_STYLES: Record<BadgeKind, { container: string; dot: string }> = {
  ok: {
    container:
      'bg-emerald-50 text-emerald-800 border-emerald-200/80 dark:bg-emerald-950/40 dark:text-emerald-300 dark:border-emerald-800/50',
    dot: 'bg-emerald-500',
  },
  warn: {
    container:
      'bg-amber-50 text-amber-800 border-amber-200/80 dark:bg-amber-950/40 dark:text-amber-300 dark:border-amber-800/50',
    dot: 'bg-amber-500',
  },
  error: {
    container:
      'bg-rose-50 text-rose-800 border-rose-200/80 dark:bg-rose-950/40 dark:text-rose-300 dark:border-rose-800/50',
    dot: 'bg-rose-500',
  },
  accent: {
    container:
      'bg-yamibo-burgundy/10 text-yamibo-burgundy border-yamibo-burgundy/25 dark:bg-yamibo-coral/15 dark:text-yamibo-coral dark:border-yamibo-coral/30',
    dot: 'bg-yamibo-burgundy dark:bg-yamibo-coral animate-pulse',
  },
  muted: {
    container:
      'bg-muted/70 text-muted-foreground border-border/80 dark:bg-muted/40',
    dot: 'bg-muted-foreground/60',
  },
}

const CONTENT_KIND_STYLES: Record<string, string> = {
  comic: 'bg-indigo-50 text-indigo-700 border-indigo-200/80 dark:bg-indigo-950/40 dark:text-indigo-300 dark:border-indigo-800/40',
  novel: 'bg-amber-50 text-amber-800 border-amber-200/80 dark:bg-amber-950/40 dark:text-amber-300 dark:border-amber-800/40',
  discussion: 'bg-teal-50 text-teal-800 border-teal-200/80 dark:bg-teal-950/40 dark:text-teal-300 dark:border-teal-800/40',
  mixed: 'bg-purple-50 text-purple-800 border-purple-200/80 dark:bg-purple-950/40 dark:text-purple-300 dark:border-purple-800/40',
}

export function Badge({
  status,
  children,
  className,
}: {
  status?: string | null
  children?: ReactNode
  className?: string
}) {
  const { t } = useI18n()
  const kind = status ? (KIND_MAP[status.toLowerCase()] || 'muted') : 'muted'
  const label = status ? t(status) : '-'
  const style = BADGE_STYLES[kind]

  return (
    <span
      className={cn(
        'inline-flex items-center gap-1.5 px-2 py-0.5 rounded-sm text-[11px] font-mono font-medium border select-none',
        'stamp-badge',
        style.container,
        className
      )}
    >
      <span className={cn('w-1.5 h-1.5 rounded-full shrink-0', style.dot)} />
      <span className="truncate">{children || label}</span>
    </span>
  )
}

export function ContentBadge({
  kind,
  className,
}: {
  kind: string | null
  className?: string
}) {
  const { t } = useI18n()
  if (!kind) {
    return (
      <span
        className={cn(
          'inline-flex items-center px-1.5 py-0.5 rounded-sm text-[11px] font-mono border bg-muted/60 text-muted-foreground border-border/80',
          className
        )}
      >
        -
      </span>
    )
  }

  const cls = CONTENT_KIND_STYLES[kind] || 'bg-muted/70 text-foreground border-border'
  return (
    <span
      className={cn(
        'inline-flex items-center px-2 py-0.5 rounded-sm text-[11px] font-sans font-medium border select-none',
        'stamp-badge',
        cls,
        className
      )}
    >
      {t(kind)}
    </span>
  )
}
