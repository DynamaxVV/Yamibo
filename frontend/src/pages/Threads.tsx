import { useEffect, useState, useCallback, useRef } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api, type ThreadSummary, type Forum } from '../api/client'
import { ContentBadge } from '../components/Badge'
import { PaginationControls } from '../components/PaginationControls'
import { useI18n } from '../context/I18nContext'
import { formatDateTime } from '../utils/time'

type SortKey = 'pub_time' | 'sync_time' | 'reply_count'
type SortDir = 'asc' | 'desc'
const PAGE_SIZE_OPTIONS = [25, 50, 100] as const
const PAGE_SIZE_STORAGE_KEY = 'threads_page_size'

function SortHeader({ label, sortKey, currentKey, currentDir, onSort, width, className }: {
  label: string; sortKey: SortKey; currentKey: SortKey | null; currentDir: SortDir; onSort: (key: SortKey) => void; width?: number; className?: string
}) {
  const active = currentKey === sortKey
  const arrow = active ? (currentDir === 'asc' ? ' ▲' : ' ▼') : ''
  return (
    <th className={`sortable${className ? ` ${className}` : ''}`} style={width ? { width } : undefined} onClick={() => onSort(sortKey)}>
      {label}<span className="sort-arrow">{arrow}</span>
    </th>
  )
}

export function Threads() {
  const { t, lang } = useI18n()
  const [searchParams] = useSearchParams()
  const [threads, setThreads] = useState<ThreadSummary[]>([])
  const [totalPages, setTotalPages] = useState(1)
  const [forums, setForums] = useState<Forum[]>([])
  const [q, setQ] = useState(() => sessionStorage.getItem('threads_q') || '')
  const [forumId, setForumId] = useState<number | undefined>(() => {
    const fid = searchParams.get('forum_id') || sessionStorage.getItem('threads_forumId')
    return fid ? Number(fid) : undefined
  })
  const [days, setDays] = useState<number | undefined>(() => {
    const d = sessionStorage.getItem('threads_days')
    return d ? Number(d) : undefined
  })
  const [archiveFilter, setArchiveFilter] = useState<string>(() => sessionStorage.getItem('threads_archiveFilter') || '')
  const [sortKey, setSortKey] = useState<SortKey | null>(() => (sessionStorage.getItem('threads_sortKey') as SortKey) || null)
  const [sortDir, setSortDir] = useState<SortDir>(() => (sessionStorage.getItem('threads_sortDir') as SortDir) || 'desc')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState<number>(() => {
    try {
      const saved = Number(sessionStorage.getItem(PAGE_SIZE_STORAGE_KEY) || 25)
      return PAGE_SIZE_OPTIONS.includes(saved as typeof PAGE_SIZE_OPTIONS[number]) ? saved : 25
    } catch {
      return 25
    }
  })
  const [selectedTids, setSelectedTids] = useState<Set<number>>(new Set())
  const [confirmDeleteTids, setConfirmDeleteTids] = useState<number[] | null>(null)
  const [deleteError, setDeleteError] = useState<string | null>(null)
  const [deleteSubmitting, setDeleteSubmitting] = useState(false)
  const [bulkMessage, setBulkMessage] = useState<string | null>(null)
  const bulkMessageTimerRef = useRef<ReturnType<typeof window.setTimeout> | null>(null)
  const refreshForums = useCallback(async () => {
    const fs = await api.forums()
    setForums(fs.filter(f => f.thread_count > 0))
  }, [])

  const loadThreads = useCallback(async () => {
    const response = await api.threads({
      q: q || undefined,
      forum_id: forumId,
      days,
      archive_status: archiveFilter || undefined,
      sort_key: sortKey || undefined,
      sort_dir: sortKey ? sortDir : undefined,
      page,
      page_size: pageSize,
    })
    setThreads(response.items)
    setTotalPages(response.total_pages)
    if (response.page !== page) setPage(response.page)
  }, [q, forumId, days, archiveFilter, sortKey, sortDir, page, pageSize])

  useEffect(() => {
    void refreshForums()
  }, [refreshForums])

  useEffect(() => {
    sessionStorage.setItem('threads_q', q)
    sessionStorage.setItem('threads_forumId', forumId != null ? String(forumId) : '')
    sessionStorage.setItem('threads_days', days != null ? String(days) : '')
    sessionStorage.setItem('threads_archiveFilter', archiveFilter)
    sessionStorage.setItem('threads_sortKey', sortKey || '')
    sessionStorage.setItem('threads_sortDir', sortDir)
    sessionStorage.setItem(PAGE_SIZE_STORAGE_KEY, String(pageSize))
  }, [q, forumId, days, archiveFilter, sortKey, sortDir, pageSize])

  useEffect(() => {
    let active = true
    setThreads([])
    void loadThreads().catch(() => {
      if (!active) return
      setThreads([])
      setTotalPages(1)
    })
    return () => { active = false }
  }, [loadThreads])

  useEffect(() => {
    return () => {
      if (bulkMessageTimerRef.current) window.clearTimeout(bulkMessageTimerRef.current)
    }
  }, [])

  useEffect(() => {
    setSelectedTids(new Set())
  }, [threads])

  const setPageSizeAndRemember = (nextPageSize: number) => {
    setPageSize(nextPageSize)
    setPage(1)
    setSelectedTids(new Set())
    try { sessionStorage.setItem(PAGE_SIZE_STORAGE_KEY, String(nextPageSize)) } catch {}
  }

  const handleSort = (key: SortKey) => {
    if (sortKey === key) {
      setSortDir(d => d === 'asc' ? 'desc' : 'asc')
    } else {
      setSortKey(key)
      setSortDir('desc')
    }
    setPage(1)
  }
  const paged = threads
  const pagedIds = paged.map(t_ => t_.tid)
  const allPagedSelected = pagedIds.length > 0 && pagedIds.every(id => selectedTids.has(id))

  const toggleSelect = (tid: number) => {
    setSelectedTids(prev => {
      const next = new Set(prev)
      next.has(tid) ? next.delete(tid) : next.add(tid)
      return next
    })
  }

  const toggleSelectAll = () => {
    setSelectedTids(prev => {
      const next = new Set(prev)
      if (allPagedSelected) {
        pagedIds.forEach(id => next.delete(id))
      } else {
        pagedIds.forEach(id => next.add(id))
      }
      return next
    })
  }

  const handleDeleteThreads = async (tids: number[]) => {
    if (tids.length === 0) return
    setDeleteSubmitting(true)
    try {
      const result = await api.deleteThreads(tids)
      const deletedIds = new Set(result.tids.length > 0 ? result.tids : tids)
      await Promise.all([refreshForums(), loadThreads()])
      setBulkMessage(null)
      setSelectedTids(prev => {
        const next = new Set(prev)
        deletedIds.forEach(id => next.delete(id))
        return next
      })
      setConfirmDeleteTids(null)
      setDeleteError(null)
    } catch (e: any) {
      setDeleteError(e.message || String(e))
    } finally {
      setDeleteSubmitting(false)
    }
  }

  const handleResyncThreads = async (tids: number[]) => {
    if (tids.length === 0) return
    setDeleteSubmitting(true)
    try {
      const result = await api.resyncThreads(tids)
      await Promise.all([refreshForums(), loadThreads()])
      if (bulkMessageTimerRef.current) window.clearTimeout(bulkMessageTimerRef.current)
      setBulkMessage(`已提交重新归档任务：目标 ${result.target_count}，新建 ${result.created_count}，复用 ${result.reused_count}`)
      bulkMessageTimerRef.current = window.setTimeout(() => {
        setBulkMessage(null)
        bulkMessageTimerRef.current = null
      }, 4000)
      setSelectedTids(prev => {
        const next = new Set(prev)
        tids.forEach(id => next.delete(id))
        return next
      })
      setConfirmDeleteTids(null)
      setDeleteError(null)
    } catch (e: any) {
      setDeleteError(e.message || String(e))
    } finally {
      setDeleteSubmitting(false)
    }
  }

  const DATE_OPTIONS = [
    { label: t('time_all'), value: undefined },
    { label: t('time_1day'), value: 1 },
    { label: t('time_3days'), value: 3 },
    { label: t('time_1week'), value: 7 },
    { label: t('time_1month'), value: 30 },
    { label: t('time_3months'), value: 90 },
  ]

  return (
    <>
      <div className="filter-bar">
        <input
          className="filter-search"
          value={q}
          onChange={e => { setQ(e.target.value); setPage(1) }}
          placeholder={t('search_placeholder')}
        />
        <div className="filter-group">
          <select value={archiveFilter} onChange={e => { setArchiveFilter(e.target.value); setPage(1) }}>
            <option value="">{t('all_status')}</option>
            <option value="complete">{t('archive_complete')}</option>
            <option value="partial">{t('archive_partial')}</option>
            <option value="stale">{t('archive_stale')}</option>
            <option value="none">{t('archive_none')}</option>
          </select>
          <select value={days ?? ''} onChange={e => { setDays(e.target.value ? Number(e.target.value) : undefined); setPage(1) }}>
            {DATE_OPTIONS.map(o => <option key={o.label} value={o.value ?? ''}>{o.label}</option>)}
          </select>
        </div>
      </div>

      <div className="threads-filter-row">
        <div className="forum-tags">
          <button className={`forum-tag ${forumId === undefined ? 'active' : ''}`}
            onClick={() => { setForumId(undefined); setPage(1) }}>
            {t('all_forums')}
          </button>
          {forums.map(f => (
            <button key={f.forum_id}
              className={`forum-tag ${forumId === f.forum_id ? 'active' : ''}`}
              onClick={() => { setForumId(forumId === f.forum_id ? undefined : f.forum_id); setPage(1) }}>
              {lang === 'en' ? (f.name_en || f.name) : f.name}
              <span className="forum-tag-count">{f.thread_count}</span>
            </button>
          ))}
        </div>
        <div className="threads-filter-actions">
          {bulkMessage && <span className="threads-inline-message">{bulkMessage}</span>}
          {selectedTids.size > 0 && (
            <>
              <button className="btn-subtle" onClick={() => void handleResyncThreads(Array.from(selectedTids))} disabled={deleteSubmitting}>
                {t('sync_selected')} ({selectedTids.size})
              </button>
              <button className="btn-danger-outline" onClick={() => setConfirmDeleteTids(Array.from(selectedTids))}>
                {t('delete_selected')} ({selectedTids.size})
              </button>
            </>
          )}
          <label className="job-page-size">
            <span>{t('page_size')}</span>
            <select value={String(pageSize)} onChange={e => setPageSizeAndRemember(Number(e.target.value))}>
              {PAGE_SIZE_OPTIONS.map(option => <option key={option} value={option}>{option}</option>)}
            </select>
          </label>
        </div>
      </div>

      <div id="threads-pagination-top" />
      <div className="table-wrap"><table style={{ tableLayout: 'fixed', width: '100%' }}>
        <thead>
          <tr>
            <th style={{ width: 32 }}><input type="checkbox" checked={allPagedSelected} onChange={toggleSelectAll} /></th>
            <th style={{ width: 60 }}>{t('tid')}</th>
            <th style={{ width: 300 }}>{t('title')}</th>
            <th style={{ width: 80 }}>{t('forum')}</th>
            <th style={{ width: 110 }} className="hide-mobile">{t('category')}</th>
            <th style={{ width: 90 }} className="hide-mobile">{t('archive')}</th>
            <SortHeader label={t('reply_count')} sortKey="reply_count" currentKey={sortKey} currentDir={sortDir} onSort={handleSort} width={55} />
            <SortHeader label={t('pub_time')} sortKey="pub_time" currentKey={sortKey} currentDir={sortDir} onSort={handleSort} width={130} />
            <SortHeader label={t('sync_time')} sortKey="sync_time" currentKey={sortKey} currentDir={sortDir} onSort={handleSort} width={160} className="hide-mobile" />
            <th style={{ width: 84 }}>{t('action')}</th>
          </tr>
        </thead>
        <tbody>
          {paged.map(t_ => (
            <tr key={t_.tid}>
              <td><input type="checkbox" checked={selectedTids.has(t_.tid)} onChange={() => toggleSelect(t_.tid)} /></td>
              <td className="mono"><Link to={`/threads/${t_.tid}`}>{t_.tid}</Link></td>
              <td className="truncate" title={t_.display_title || t_.raw_title}><Link to={`/threads/${t_.tid}`}>{t_.display_title || t_.raw_title}</Link></td>
              <td className="nowrap">{forums.find(f => f.forum_id === t_.forum_id)?.[lang === 'en' ? 'name_en' : 'name'] || t_.forum_id || '-'}</td>
              <td className="nowrap hide-mobile">{t_.category || '-'}</td>
              <td className="hide-mobile"><ContentBadge kind={t_.content_kind} /></td>
              <td>{t_.reply_count || '-'}</td>
              <td style={{ textAlign: 'center' }}>{formatDateTime(t_.pub_time)}</td>
              <td className="nowrap hide-mobile" style={{ textAlign: 'center' }}>{formatDateTime(t_.sync_time)}</td>
              <td><button className="btn-danger-outline" onClick={() => setConfirmDeleteTids([t_.tid])} style={{ fontSize: 11, padding: '2px 6px' }}>{t('delete')}</button></td>
            </tr>
          ))}
        </tbody>
      </table></div>

      <PaginationControls page={page} totalPages={totalPages} onPageChange={setPage} scrollTargetId="threads-pagination-top" />

      {confirmDeleteTids && (
        <div className="confirm-overlay" onClick={() => { setConfirmDeleteTids(null); setDeleteError(null) }}>
          <div className="confirm-dialog" onClick={e => e.stopPropagation()}>
            <p style={{ fontSize: 13, color: 'var(--text-secondary)', lineHeight: 1.6, margin: '0 0 16px' }}>
              {t('delete_selected_threads_confirm', { n: String(confirmDeleteTids.length) })}
            </p>
            <p style={{ fontSize: 12, color: 'var(--text-tertiary)', margin: '0 0 16px' }}>
              {confirmDeleteTids.slice(0, 5).map(id => `TID ${id}`).join(', ')}{confirmDeleteTids.length > 5 ? ` ... +${confirmDeleteTids.length - 5}` : ''}
            </p>
            {deleteError && <p style={{ fontSize: 12, color: 'var(--status-error)', margin: '0 0 12px' }}>{deleteError}</p>}
            <div className="confirm-actions">
              <button className="btn-subtle" onClick={() => { setConfirmDeleteTids(null); setDeleteError(null) }}>{t('cancel')}</button>
              <button className="btn-danger" onClick={() => void handleDeleteThreads(confirmDeleteTids)} disabled={deleteSubmitting}>{t('confirm_execute')}</button>
            </div>
          </div>
        </div>
      )}
    </>
  )
}
