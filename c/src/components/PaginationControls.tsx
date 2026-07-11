import { useEffect, useRef, useState } from 'react'
import { useI18n } from '../context/I18nContext'

interface PaginationControlsProps {
  page: number
  totalPages: number
  onPageChange: (page: number) => void
  className?: string
  scrollTargetId?: string
  suppressScrollRef?: { current: boolean }
}

export function PaginationControls({ page, totalPages, onPageChange, className, scrollTargetId, suppressScrollRef }: PaginationControlsProps) {
  const { t } = useI18n()
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
    <div className={className || 'pagination-controls'}>
      <button className="btn-subtle" disabled={page <= 1} onClick={() => changePage(1)}>&laquo;</button>
      <button className="btn-subtle" disabled={page <= 1} onClick={() => changePage(page - 1)}>&lsaquo;</button>
      <span className="page-indicator">{page} / {totalPages}</span>
      <button className="btn-subtle" disabled={page >= totalPages} onClick={() => changePage(page + 1)}>&rsaquo;</button>
      <button className="btn-subtle" disabled={page >= totalPages} onClick={() => changePage(totalPages)}>&raquo;</button>
      <span className="page-jump-label">{t('jump_to_page')}</span>
      <input
        className="page-jump-input"
        inputMode="numeric"
        value={jumpPage}
        onChange={e => setJumpPage(e.target.value.replace(/[^\d]/g, ''))}
        onKeyDown={e => {
          if (e.key === 'Enter') commitJump()
        }}
      />
      <button className="btn-subtle" onClick={commitJump}>{t('go_page')}</button>
    </div>
  )
}
