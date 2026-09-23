import { useEffect, useRef, useState } from 'react'
import { ChevronLeft, ChevronRight, ChevronsLeft, ChevronsRight } from 'lucide-react'
import { useI18n } from '../context/I18nContext'
import { cn } from '../lib/utils'

interface PaginationControlsProps {
  page: number
  totalPages: number
  onPageChange: (page: number) => void
  className?: string
  scrollTargetId?: string
  suppressScrollRef?: { current: boolean }
}

export function PaginationControls({
  page,
  totalPages,
  onPageChange,
  className,
  scrollTargetId,
  suppressScrollRef,
}: PaginationControlsProps) {
  const { t, tx } = useI18n()
  const [jumpPage, setJumpPage] = useState(String(page))
  const prevPageRef = useRef(page)

  useEffect(() => {
    setJumpPage(String(page))
  }, [page])

  useEffect(() => {
    if (!scrollTargetId) return
    if (prevPageRef.current === page) return
    prevPageRef.current = page
    if (suppressScrollRef?.current) {
      suppressScrollRef.current = false
      return
    }
    window.requestAnimationFrame(() => {
      const target = document.getElementById(scrollTargetId)
      target?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    })
  }, [page, scrollTargetId, suppressScrollRef])

  const changePage = (nextPage: number) => {
    if (nextPage === page) return
    onPageChange(nextPage)
  }

  const commitJump = () => {
    const parsed = parseInt(jumpPage, 10)
    if (!Number.isFinite(parsed)) {
      setJumpPage(String(page))
      return
    }
    const normalized = Math.min(Math.max(1, parsed), totalPages)
    setJumpPage(String(normalized))
    changePage(normalized)
  }

  if (totalPages <= 1) return null

  return (
    <div
      className={cn(
        'flex items-center justify-between gap-2 py-3 px-1 text-xs font-mono text-muted-foreground select-none',
        className
      )}
    >
      <div className="flex items-center gap-1">
        <button
          disabled={page <= 1}
          onClick={() => changePage(1)}
          className="p-1 rounded-sm border border-border bg-card hover:bg-muted text-foreground disabled:opacity-30 disabled:pointer-events-none transition-colors"
          title={tx('第一页', 'First page')}
        >
          <ChevronsLeft className="w-3.5 h-3.5" />
        </button>
        <button
          disabled={page <= 1}
          onClick={() => changePage(page - 1)}
          className="p-1 rounded-sm border border-border bg-card hover:bg-muted text-foreground disabled:opacity-30 disabled:pointer-events-none transition-colors"
          title={t('prev_page')}
        >
          <ChevronLeft className="w-3.5 h-3.5" />
        </button>

        <span className="px-2.5 py-1 text-xs font-medium text-foreground bg-muted/50 rounded-sm border border-border/60">
          <span className="font-semibold text-yamibo-burgundy dark:text-yamibo-coral">{page}</span> / {totalPages}
        </span>

        <button
          disabled={page >= totalPages}
          onClick={() => changePage(page + 1)}
          className="p-1 rounded-sm border border-border bg-card hover:bg-muted text-foreground disabled:opacity-30 disabled:pointer-events-none transition-colors"
          title={t('next_page')}
        >
          <ChevronRight className="w-3.5 h-3.5" />
        </button>
        <button
          disabled={page >= totalPages}
          onClick={() => changePage(totalPages)}
          className="p-1 rounded-sm border border-border bg-card hover:bg-muted text-foreground disabled:opacity-30 disabled:pointer-events-none transition-colors"
          title={tx('最后一页', 'Last page')}
        >
          <ChevronsRight className="w-3.5 h-3.5" />
        </button>
      </div>

      <div className="flex items-center gap-1.5">
        <span className="text-[11px] text-muted-foreground/80">{t('jump_to_page')}</span>
        <input
          className="w-12 text-center text-xs font-mono py-0.5 px-1 rounded-sm border border-border bg-card text-foreground focus:outline-none focus:ring-1 focus:ring-yamibo-burgundy"
          inputMode="numeric"
          value={jumpPage}
          onChange={e => setJumpPage(e.target.value.replace(/[^\d]/g, ''))}
          onKeyDown={e => {
            if (e.key === 'Enter') commitJump()
          }}
        />
        <button
          onClick={commitJump}
          className="px-2 py-0.5 rounded-sm border border-border bg-card hover:bg-muted text-foreground text-[11px] font-sans transition-colors press-feedback"
        >
          {t('go_page')}
        </button>
      </div>
    </div>
  )
}
