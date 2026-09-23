import { useEffect, useState, useCallback, useRef } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import {
  Search,
  RotateCcw,
  Trash2,
  X,
  ArrowUpDown,
  ArrowUp,
  ArrowDown,
} from 'lucide-react'
import { api, type ThreadSummary, type Forum } from '../api/client'
import { ContentBadge } from '../components/Badge'
import { PaginationControls } from '../components/PaginationControls'
import { useI18n } from '../context/I18nContext'
import { formatDateTime } from '../utils/time'
import { formatThreadListTitle } from '../utils/threadTitle'
import { useTableLayout } from '../components/TableLayoutEditor'
import { cn } from '../lib/utils'

type SortKey = 'pub_time' | 'sync_time' | 'reply_count' | 'remote_last_reply_at'
type SortDir = 'asc' | 'desc'
const PAGE_SIZE_OPTIONS = [25, 50, 100] as const
const PAGE_SIZE_STORAGE_KEY = 'threads_page_size'

function formatDateTimeStacked(value: string | null) {
  const formatted = formatDateTime(value)
  if (formatted === '-') return formatted
  const [datePart, timePart, ...rest] = formatted.split(' ')
  if (!datePart || !timePart || rest.length > 0) return formatted
  return (
    <span className="flex flex-col items-center text-center text-[11px] font-mono leading-tight">
      <span className="text-foreground">{datePart}</span>
      <span className="text-muted-foreground/80">{timePart}</span>
    </span>
  )
}

function SortHeader({
  label,
  sortKey,
  currentKey,
  currentDir,
  onSort,
  width,
  className,
}: {
  label: string
  sortKey: SortKey
  currentKey: SortKey | null
  currentDir: SortDir
  onSort: (key: SortKey) => void
  width?: number
  className?: string
}) {
  const active = currentKey === sortKey
  return (
    <th
      className={cn(
        'px-3 py-2.5 cursor-pointer hover:bg-muted/70 transition-colors select-none group',
        className
      )}
      style={width ? { width } : undefined}
      onClick={() => onSort(sortKey)}
    >
      <div className="flex items-center justify-center gap-1">
        <span>{label}</span>
        {active ? (
          currentDir === 'asc' ? (
            <ArrowUp className="w-3 h-3 text-yamibo-burgundy dark:text-yamibo-coral" />
          ) : (
            <ArrowDown className="w-3 h-3 text-yamibo-burgundy dark:text-yamibo-coral" />
          )
        ) : (
          <ArrowUpDown className="w-3 h-3 opacity-0 group-hover:opacity-40 transition-opacity" />
        )}
      </div>
    </th>
  )
}

export function Threads() {
  const { t, lang, tx } = useI18n()
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
  const [archiveFilter, setArchiveFilter] = useState<string>(
    () => sessionStorage.getItem('threads_archiveFilter') || ''
  )
  const [sortKey, setSortKey] = useState<SortKey | null>(
    () => (sessionStorage.getItem('threads_sortKey') as SortKey) || null
  )
  const [sortDir, setSortDir] = useState<SortDir>(
    () => (sessionStorage.getItem('threads_sortDir') as SortDir) || 'desc'
  )
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState<number>(() => {
    try {
      const saved = Number(sessionStorage.getItem(PAGE_SIZE_STORAGE_KEY) || 25)
      return PAGE_SIZE_OPTIONS.includes(saved as typeof PAGE_SIZE_OPTIONS[number]) ? saved : 25
    } catch {
      return 25
    }
  })
  const tableLayout = useTableLayout('threads')
  const column = (key: string) => tableLayout.find(item => item.key === key)
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
    return () => {
      active = false
    }
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
    try {
      sessionStorage.setItem(PAGE_SIZE_STORAGE_KEY, String(nextPageSize))
    } catch {}
  }

  const handleSort = (key: SortKey) => {
    if (sortKey === key) {
      setSortDir(d => (d === 'asc' ? 'desc' : 'asc'))
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
      if (next.has(tid)) next.delete(tid)
      else next.add(tid)
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
      setBulkMessage(
        tx(`已提交重新归档任务：目标 ${result.target_count}，新建 ${result.created_count}，复用 ${result.reused_count}`, `Archive tasks queued: ${result.target_count} requested, ${result.created_count} created, ${result.reused_count} reused`)
      )
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
    <div className="space-y-3">
      <header className="border-b border-border pb-3"><h1 className="text-3xl">{tx('帖子归档', 'Thread archive')}</h1><p className="mt-1 text-sm text-muted-foreground">{tx('搜索、筛选并管理已收录的帖子。', 'Search, filter, and manage archived threads.')}</p></header>
      {/* ─── Search & Dimension Filters ─── */}
      <div className="flex flex-wrap items-center gap-3">
        <div className="relative flex-1 min-w-[220px]">
          <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground pointer-events-none" />
          <input
            className="w-full pl-9 pr-3 py-1.5 rounded-sm border border-border bg-card text-foreground text-xs font-sans placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-yamibo-burgundy"
            value={q}
            onChange={e => {
              setQ(e.target.value)
              setPage(1)
            }}
            placeholder={t('search_placeholder')}
          />
        </div>

        <div className="flex items-center gap-2 text-xs font-mono">
          <select
            value={archiveFilter}
            onChange={e => {
              setArchiveFilter(e.target.value)
              setPage(1)
            }}
            className="px-2.5 py-1.5 rounded-sm border border-border bg-card text-foreground focus:outline-none focus:ring-1 focus:ring-yamibo-burgundy"
          >
            <option value="">{t('all_status')}</option>
            <option value="complete">{t('archive_complete')}</option>
            <option value="partial">{t('archive_partial')}</option>
            <option value="stale">{t('archive_stale')}</option>
            <option value="none">{t('archive_none')}</option>
          </select>

          <select
            value={days ?? ''}
            onChange={e => {
              setDays(e.target.value ? Number(e.target.value) : undefined)
              setPage(1)
            }}
            className="px-2.5 py-1.5 rounded-sm border border-border bg-card text-foreground focus:outline-none focus:ring-1 focus:ring-yamibo-burgundy"
          >
            {DATE_OPTIONS.map(o => (
              <option key={o.label} value={o.value ?? ''}>
                {o.label}
              </option>
            ))}
          </select>
        </div>
      </div>

      {/* ─── Forum Filter Tags Bar ─── */}
      <div className="flex flex-wrap items-center gap-1.5 pt-1">
        <button
          onClick={() => {
            setForumId(undefined)
            setPage(1)
          }}
          className={cn(
            'flex items-center gap-1.5 px-3 py-1 rounded-sm text-xs font-mono font-medium transition-all press-feedback select-none',
            forumId === undefined
              ? 'bg-yamibo-burgundy text-white dark:bg-yamibo-burgundy-light shadow-2xs font-semibold'
              : 'bg-card border border-border text-muted-foreground hover:text-foreground hover:bg-muted'
          )}
        >
          <span>{t('all_forums')}</span>
        </button>

        {forums.map(f => {
          const active = forumId === f.forum_id
          const name = lang === 'en' ? f.name_en || f.name : f.name

          return (
            <button
              key={f.forum_id}
              onClick={() => {
                setForumId(active ? undefined : f.forum_id)
                setPage(1)
              }}
              className={cn(
                'flex items-center gap-1.5 px-3 py-1 rounded-sm text-xs font-mono font-medium transition-all press-feedback select-none border',
                active
                  ? 'bg-yamibo-burgundy text-white border-yamibo-burgundy dark:bg-yamibo-burgundy-light shadow-2xs font-semibold'
                  : 'bg-card border-border text-muted-foreground hover:text-foreground hover:bg-muted'
              )}
            >
              <span>{name}</span>
              <span
                className={cn(
                  'text-[10px] px-1 rounded-full font-bold',
                  active ? 'bg-white/20 text-white' : 'bg-muted text-muted-foreground'
                )}
              >
                {f.thread_count}
              </span>
            </button>
          )
        })}
      </div>

      {bulkMessage && (
        <div className="p-3 rounded-md bg-muted/60 border border-border text-xs font-mono text-foreground">
          {bulkMessage}
        </div>
      )}

      {/* ─── Threads Data Table ─── */}
      <div id="threads-pagination-top" />
      <div className="flex min-h-10 w-full items-center gap-2 overflow-x-auto whitespace-nowrap border-b border-border/50 py-1">
        <p className="min-w-0 truncate text-xs text-muted-foreground max-md:hidden xl:hidden">{tx('表格可横向滚动，标题与操作均可在表格内查看。', 'Scroll the table horizontally to view titles and actions.')}</p>
        {selectedTids.size === 0 && <p className="text-xs text-muted-foreground md:hidden xl:block">{tx('勾选帖子以批量处理', 'Select threads for bulk actions')}</p>}
        {selectedTids.size > 0 && (
          <div role="toolbar" aria-label={tx('已选帖子操作', 'Selected thread actions')} className="ml-auto flex shrink-0 items-center gap-2 text-xs font-mono">
            <div className="flex shrink-0 items-center gap-1.5">
              <span className="h-1.5 w-1.5 rounded-full bg-yamibo-burgundy dark:bg-yamibo-coral" />
              <span className="text-xs font-mono font-medium text-foreground">
                {tx('已选中', 'Selected')} <span className="font-bold text-yamibo-burgundy dark:text-yamibo-coral">{selectedTids.size}</span> {tx('篇帖子', 'threads')}
              </span>
            </div>

            <div className="flex shrink-0 items-center gap-1.5">
              <button
                onClick={() => void handleResyncThreads(Array.from(selectedTids))}
                disabled={deleteSubmitting}
                className="flex items-center gap-1 px-2 py-1 rounded-sm bg-muted hover:bg-muted/80 text-foreground text-xs font-sans font-medium transition-colors press-feedback disabled:opacity-50"
              >
                <RotateCcw className="w-3 h-3" />
                <span>{t('sync_selected')}</span>
              </button>

              <button
                onClick={() => setConfirmDeleteTids(Array.from(selectedTids))}
                disabled={deleteSubmitting}
                className="flex items-center gap-1 px-2 py-1 rounded-sm bg-rose-50 hover:bg-rose-100 text-rose-800 border border-rose-200 dark:bg-rose-950/40 dark:border-rose-900/60 dark:text-rose-300 text-xs font-sans font-medium transition-colors press-feedback disabled:opacity-50"
              >
                <Trash2 className="w-3 h-3" />
                <span>{t('delete_selected')}</span>
              </button>

              <button
                onClick={() => setSelectedTids(new Set())}
                className="shrink-0 p-1 rounded-sm text-muted-foreground hover:text-foreground transition-colors"
                title={tx('取消选择', 'Clear selection')}
              >
                <X className="w-4 h-4" />
              </button>
            </div>
          </div>
        )}
      </div>
      <div className="space-y-2 md:hidden">
        {paged.length === 0 && <p className="rounded border border-border bg-card p-5 text-sm text-muted-foreground">{t('no_data')}</p>}
        {paged.map(item => {
          const title = formatThreadListTitle(item)
          const forumName = forums.find(f => f.forum_id === item.forum_id)?.[lang === 'en' ? 'name_en' : 'name'] || item.forum_id || '-'
          return <article key={item.tid} className="rounded border border-border bg-card p-3">
            <div className="flex items-start gap-2"><input type="checkbox" checked={selectedTids.has(item.tid)} onChange={() => toggleSelect(item.tid)} aria-label={`${tx('选择帖子', 'Select thread')} ${item.tid}`} className="mt-2 h-5 w-5 shrink-0" /><div className="min-w-0 flex-1"><Link to={`/threads/${item.tid}`} className="block break-words font-sans text-sm font-light leading-5 text-foreground">{title}</Link><div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground"><span>#{item.tid}</span><span>{forumName}</span><span>{item.sync_time?.slice(0, 10) || '-'}</span><ContentBadge kind={item.content_kind} /></div></div><button type="button" onClick={() => setConfirmDeleteTids([item.tid])} className="flex h-11 w-11 shrink-0 items-center justify-center rounded border border-border text-destructive" aria-label={`${t('delete')} ${title}`} title={t('delete')}><Trash2 className="h-4 w-4" /></button></div>
          </article>
        })}
      </div>
      <div className="relative hidden overflow-x-auto rounded-md border border-border bg-card shadow-2xs md:block">
        <table className="list-table w-full text-left text-[13px] divide-y divide-border">
          <thead className="bg-muted/50 text-[11px] font-mono text-muted-foreground uppercase tracking-wider">
            <tr>
              <th className="px-3 py-2.5 w-10">
                <input
                  type="checkbox"
                  checked={allPagedSelected}
                  onChange={toggleSelectAll}
                  className="rounded-xs border-border text-yamibo-burgundy focus:ring-yamibo-burgundy"
                />
              </th>
              {column('tid')?.visible && <th className="px-3 py-2.5 w-20">{t('tid')}</th>}
              {column('title')?.visible && <th className="min-w-80 px-3 py-2.5">{t('title')}</th>}
              {column('forum')?.visible && <th className="px-3 py-2.5 w-28">{t('forum')}</th>}
              {column('category')?.visible && (
                <th className="px-3 py-2.5 w-24 hidden md:table-cell">{t('category')}</th>
              )}
              {column('archive')?.visible && (
                <th className="px-3 py-2.5 w-24 hidden md:table-cell">{t('archive')}</th>
              )}
              {column('reply_count')?.visible && (
                <SortHeader
                  label={t('reply_count')}
                  sortKey="reply_count"
                  currentKey={sortKey}
                  currentDir={sortDir}
                  onSort={handleSort}
                  width={column('reply_count')?.width}
                />
              )}
              {column('pub_time')?.visible && (
                <SortHeader
                  label={t('pub_time')}
                  sortKey="pub_time"
                  currentKey={sortKey}
                  currentDir={sortDir}
                  onSort={handleSort}
                  width={column('pub_time')?.width}
                />
              )}
              {column('last_reply_time')?.visible && (
                <SortHeader
                  label={t('last_reply_time')}
                  sortKey="remote_last_reply_at"
                  currentKey={sortKey}
                  currentDir={sortDir}
                  onSort={handleSort}
                  width={column('last_reply_time')?.width}
                  className="hidden md:table-cell"
                />
              )}
              {column('sync_time')?.visible && (
                <SortHeader
                  label={t('sync_time')}
                  sortKey="sync_time"
                  currentKey={sortKey}
                  currentDir={sortDir}
                  onSort={handleSort}
                  width={column('sync_time')?.width}
                  className="hidden md:table-cell"
                />
              )}
              <th className="px-3 py-2.5 w-20">{t('action')}</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border/60">
            {paged.length === 0 ? (
              <tr>
                <td colSpan={11} className="p-8 text-center text-xs font-mono text-muted-foreground">
                  {t('no_data')}
                </td>
              </tr>
            ) : (
              paged.map(t_ => {
                const titleText = formatThreadListTitle(t_)
                const isSelected = selectedTids.has(t_.tid)
                const forumName =
                  forums.find(f => f.forum_id === t_.forum_id)?.[
                    lang === 'en' ? 'name_en' : 'name'
                  ] ||
                  t_.forum_id ||
                  '-'

                return (
                  <tr
                    key={t_.tid}
                    className={cn(
                      'hover:bg-muted/40 transition-colors press-feedback group',
                      isSelected && 'bg-muted/60'
                    )}
                  >
                    <td className="px-3 py-2">
                      <input
                        type="checkbox"
                        checked={isSelected}
                        onChange={() => toggleSelect(t_.tid)}
                        className="rounded-xs border-border text-yamibo-burgundy focus:ring-yamibo-burgundy"
                      />
                    </td>
                    {column('tid')?.visible && (
                      <td className="px-3 py-2 font-mono text-xs text-muted-foreground whitespace-nowrap">
                        <Link
                          to={`/threads/${t_.tid}`}
                          className="hover:text-yamibo-burgundy dark:hover:text-yamibo-coral font-medium"
                        >
                          #{t_.tid}
                        </Link>
                      </td>
                    )}
                    {column('title')?.visible && (
                      <td className="table-cell-long min-w-80 px-3 py-2">
                        <div className="flex items-center gap-2">
                          <Link
                            to={`/threads/${t_.tid}`}
                            className="font-sans font-light text-foreground hover:text-yamibo-burgundy dark:hover:text-yamibo-coral line-clamp-2 break-words leading-5 transition-colors"
                            title={titleText}
                          >
                            {titleText}
                          </Link>
                        </div>
                      </td>
                    )}
                    {column('forum')?.visible && (
                      <td className="px-3 py-2 text-xs text-muted-foreground whitespace-nowrap font-sans">
                        {forumName}
                      </td>
                    )}
                    {column('category')?.visible && (
                      <td className="px-3 py-2 text-xs text-muted-foreground whitespace-nowrap hidden md:table-cell">
                        {t_.category || '-'}
                      </td>
                    )}
                    {column('archive')?.visible && (
                      <td className="px-3 py-2 hidden md:table-cell">
                        <ContentBadge kind={t_.content_kind} />
                      </td>
                    )}
                    {column('reply_count')?.visible && (
                      <td className="px-3 py-2 text-center font-mono text-xs text-muted-foreground">
                        {t_.reply_count ?? '-'}
                      </td>
                    )}
                    {column('pub_time')?.visible && (
                      <td className="px-3 py-2 whitespace-nowrap text-center">{formatDateTimeStacked(t_.pub_time)}</td>
                    )}
                    {column('last_reply_time')?.visible && (
                      <td className="px-3 py-2 whitespace-nowrap text-center hidden md:table-cell">
                        {formatDateTimeStacked(t_.remote_last_reply_at)}
                      </td>
                    )}
                    {column('sync_time')?.visible && (
                      <td className="px-3 py-2 whitespace-nowrap hidden md:table-cell">
                        {formatDateTimeStacked(t_.sync_time)}
                      </td>
                    )}
                    <td className="px-3 py-2">
                      <button
                        onClick={() => setConfirmDeleteTids([t_.tid])}
                        className="p-1 rounded-sm border border-border bg-card hover:bg-rose-50 dark:hover:bg-rose-950/40 text-muted-foreground hover:text-rose-600 transition-colors press-feedback"
                        title={t('delete')}
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                    </td>
                  </tr>
                )
              })
            )}
          </tbody>
        </table>
      </div>

      {/* Pagination Controls */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <PaginationControls
          page={page}
          totalPages={totalPages}
          onPageChange={setPage}
          scrollTargetId="threads-pagination-top"
          className="flex-1"
        />

        <div className="flex items-center gap-1.5 text-xs font-mono text-muted-foreground">
          <span>{t('page_size')}:</span>
          <select
            value={String(pageSize)}
            onChange={e => setPageSizeAndRemember(Number(e.target.value))}
            className="px-2 py-0.5 rounded-sm border border-border bg-card text-foreground font-mono focus:outline-none focus:ring-1 focus:ring-yamibo-burgundy"
          >
            {PAGE_SIZE_OPTIONS.map(option => (
              <option key={option} value={option}>
                {option}
              </option>
            ))}
          </select>
        </div>
      </div>

      {/* ─── Delete Confirmation Modal ─── */}
      {confirmDeleteTids && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50 backdrop-blur-2xs">
          <div className="bg-card border border-border rounded-md shadow-lg max-w-md w-full p-5 space-y-3 animate-in fade-in-50 zoom-in-95 duration-150">
            <h2 className="text-base font-semibold text-foreground font-sans">
              {t('confirm_delete')}
            </h2>
            <p className="text-xs text-muted-foreground font-sans leading-relaxed">
              {t('delete_selected_threads_confirm', { n: String(confirmDeleteTids.length) })}
            </p>
            <div className="p-2.5 rounded-sm bg-muted/60 border border-border/70 text-xs font-mono text-foreground">
              {confirmDeleteTids.slice(0, 5).map(id => `#${id}`).join(', ')}
              {confirmDeleteTids.length > 5 ? tx(` ...等共 ${confirmDeleteTids.length} 篇`, ` … and ${confirmDeleteTids.length - 5} more`) : ''}
            </div>
            {deleteError && (
              <p className="text-xs font-mono text-rose-600 dark:text-rose-400">{deleteError}</p>
            )}
            <div className="flex justify-end gap-2 pt-2">
              <button
                onClick={() => {
                  setConfirmDeleteTids(null)
                  setDeleteError(null)
                }}
                className="px-3 py-1.5 rounded-sm border border-border bg-card hover:bg-muted text-xs font-sans font-medium transition-colors"
              >
                {t('cancel')}
              </button>
              <button
                onClick={() => void handleDeleteThreads(confirmDeleteTids)}
                disabled={deleteSubmitting}
                className="px-3 py-1.5 rounded-sm bg-rose-600 hover:bg-rose-700 text-white text-xs font-sans font-medium transition-colors press-feedback disabled:opacity-50"
              >
                {t('confirm_execute')}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
