import { useEffect, useState, useCallback } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api, type ThreadSummary, type Forum } from '../api/client'
import { ContentBadge } from '../components/Badge'
import { useI18n } from '../context/I18nContext'
import { formatDateTime } from '../utils/time'

type SortKey = 'pub_time' | 'sync_time' | 'reply_count'
type SortDir = 'asc' | 'desc'

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
  const PAGE_SIZE = 50

  useEffect(() => {
    api.forums().then(fs => setForums(fs.filter(f => f.thread_count > 0))).catch(() => {})
  }, [])

  useEffect(() => {
    sessionStorage.setItem('threads_q', q)
    sessionStorage.setItem('threads_forumId', forumId != null ? String(forumId) : '')
    sessionStorage.setItem('threads_days', days != null ? String(days) : '')
    sessionStorage.setItem('threads_archiveFilter', archiveFilter)
    sessionStorage.setItem('threads_sortKey', sortKey || '')
    sessionStorage.setItem('threads_sortDir', sortDir)
  }, [q, forumId, days, archiveFilter, sortKey, sortDir])

  const search = useCallback(() => {
    api.threads({ q: q || undefined, forum_id: forumId, days }).then(setThreads)
    setPage(1)
  }, [q, forumId, days])

  useEffect(() => { search() }, [search])

  const handleSort = (key: SortKey) => {
    if (sortKey === key) {
      setSortDir(d => d === 'asc' ? 'desc' : 'asc')
    } else {
      setSortKey(key)
      setSortDir('desc')
    }
  }

  const sorted = sortKey
    ? [...threads].sort((a, b) => {
        const av = a[sortKey] ?? (sortKey === 'reply_count' ? 0 : '')
        const bv = b[sortKey] ?? (sortKey === 'reply_count' ? 0 : '')
        const cmp = av < bv ? -1 : av > bv ? 1 : 0
        return sortDir === 'asc' ? cmp : -cmp
      })
    : threads

  const filtered = archiveFilter
    ? sorted.filter(t_ => archiveFilter === 'none' ? !t_.archive_status : t_.archive_status === archiveFilter)
    : sorted

  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE))
  const paged = filtered.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE)

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
          onChange={e => setQ(e.target.value)}
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
          <select value={days ?? ''} onChange={e => setDays(e.target.value ? Number(e.target.value) : undefined)}>
            {DATE_OPTIONS.map(o => <option key={o.label} value={o.value ?? ''}>{o.label}</option>)}
          </select>
        </div>
      </div>

      <div className="forum-tags">
        <button className={`forum-tag ${forumId === undefined ? 'active' : ''}`}
          onClick={() => setForumId(undefined)}>
          {t('all_forums')}
        </button>
        {forums.map(f => (
          <button key={f.forum_id}
            className={`forum-tag ${forumId === f.forum_id ? 'active' : ''}`}
            onClick={() => setForumId(forumId === f.forum_id ? undefined : f.forum_id)}>
            {lang === 'en' ? (f.name_en || f.name) : f.name}
            <span className="forum-tag-count">{f.thread_count}</span>
          </button>
        ))}
      </div>

      <div className="table-wrap"><table style={{ tableLayout: 'fixed', width: '100%' }}>
        <thead>
          <tr>
            <th style={{ width: 60 }}>{t('tid')}</th>
            <th style={{ width: 300 }}>{t('title')}</th>
            <th style={{ width: 80 }}>{t('forum')}</th>
            <th style={{ width: 110 }} className="hide-mobile">{t('category')}</th>
            <th style={{ width: 90 }} className="hide-mobile">{t('archive')}</th>
            <SortHeader label={t('reply_count')} sortKey="reply_count" currentKey={sortKey} currentDir={sortDir} onSort={handleSort} width={55} />
            <SortHeader label={t('pub_time')} sortKey="pub_time" currentKey={sortKey} currentDir={sortDir} onSort={handleSort} width={130} />
            <SortHeader label={t('sync_time')} sortKey="sync_time" currentKey={sortKey} currentDir={sortDir} onSort={handleSort} width={160} className="hide-mobile" />
          </tr>
        </thead>
        <tbody>
          {paged.map(t_ => (
            <tr key={t_.tid}>
              <td className="mono"><Link to={`/threads/${t_.tid}`}>{t_.tid}</Link></td>
              <td className="truncate" title={t_.display_title || t_.raw_title}><Link to={`/threads/${t_.tid}`}>{t_.display_title || t_.raw_title}</Link></td>
              <td className="nowrap">{forums.find(f => f.forum_id === t_.forum_id)?.[lang === 'en' ? 'name_en' : 'name'] || t_.forum_id || '-'}</td>
              <td className="nowrap hide-mobile">{t_.category || '-'}</td>
              <td className="hide-mobile"><ContentBadge kind={t_.content_kind} /></td>
              <td>{t_.reply_count || '-'}</td>
              <td style={{ textAlign: 'center' }}>{formatDateTime(t_.pub_time)}</td>
              <td className="nowrap hide-mobile" style={{ textAlign: 'center' }}>{formatDateTime(t_.sync_time)}</td>
            </tr>
          ))}
        </tbody>
      </table></div>

      {totalPages > 1 && (
        <div className="row-actions" style={{ justifyContent: 'center', gap: 4 }}>
          <button className="btn-subtle" disabled={page <= 1} onClick={() => setPage(1)}>&laquo;</button>
          <button className="btn-subtle" disabled={page <= 1} onClick={() => setPage(p => p - 1)}>&lsaquo;</button>
          <span style={{ fontSize: 12, color: 'var(--text-tertiary)', padding: '4px 8px' }}>{page} / {totalPages}</span>
          <button className="btn-subtle" disabled={page >= totalPages} onClick={() => setPage(p => p + 1)}>&rsaquo;</button>
          <button className="btn-subtle" disabled={page >= totalPages} onClick={() => setPage(totalPages)}>&raquo;</button>
        </div>
      )}
    </>
  )
}
