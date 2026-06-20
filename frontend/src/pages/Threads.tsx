import { useEffect, useState, useCallback } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api, type ThreadSummary, type Forum } from '../api/client'
import { ContentBadge } from '../components/Badge'
import { useI18n } from '../context/I18nContext'
import { formatDateTime } from '../utils/time'

type SortKey = 'pub_time' | 'sync_time'
type SortDir = 'asc' | 'desc'

function SortHeader({ label, sortKey, currentKey, currentDir, onSort, width }: {
  label: string; sortKey: SortKey; currentKey: SortKey | null; currentDir: SortDir; onSort: (key: SortKey) => void; width?: number
}) {
  const active = currentKey === sortKey
  const arrow = active ? (currentDir === 'asc' ? ' ▲' : ' ▼') : ''
  return (
    <th className="sortable" style={width ? { width } : undefined} onClick={() => onSort(sortKey)}>
      {label}<span className="sort-arrow">{arrow}</span>
    </th>
  )
}

export function Threads() {
  const { t, lang } = useI18n()
  const [searchParams] = useSearchParams()
  const [threads, setThreads] = useState<ThreadSummary[]>([])
  const [forums, setForums] = useState<Forum[]>([])
  const [q, setQ] = useState('')
  const [forumId, setForumId] = useState<number | undefined>(() => {
    const fid = searchParams.get('forum_id')
    return fid ? Number(fid) : undefined
  })
  const [days, setDays] = useState<number | undefined>()
  const [sortKey, setSortKey] = useState<SortKey | null>(null)
  const [sortDir, setSortDir] = useState<SortDir>('desc')

  useEffect(() => {
    api.forums().then(fs => setForums(fs.filter(f => f.thread_count > 0))).catch(() => {})
  }, [])

  const search = useCallback(() => {
    api.threads({ q: q || undefined, forum_id: forumId, days }).then(setThreads)
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
        const av = a[sortKey] || ''
        const bv = b[sortKey] || ''
        const cmp = av < bv ? -1 : av > bv ? 1 : 0
        return sortDir === 'asc' ? cmp : -cmp
      })
    : threads

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
            <th style={{ width: 75 }}>{t('tid')}</th>
            <th>{t('title')}</th>
            <th style={{ width: 100 }}>{t('forum')}</th>
            <th style={{ width: 110 }}>{t('category')}</th>
            <th style={{ width: 90 }}>{t('archive')}</th>
            <SortHeader label={t('pub_time')} sortKey="pub_time" currentKey={sortKey} currentDir={sortDir} onSort={handleSort} width={200} />
            <SortHeader label={t('sync_time')} sortKey="sync_time" currentKey={sortKey} currentDir={sortDir} onSort={handleSort} width={200} />
          </tr>
        </thead>
        <tbody>
          {sorted.map(t_ => (
            <tr key={t_.tid}>
              <td className="mono"><Link to={`/threads/${t_.tid}`}>{t_.tid}</Link></td>
              <td className="truncate"><Link to={`/threads/${t_.tid}`}>{t_.display_title || t_.raw_title}</Link></td>
              <td className="nowrap">{forums.find(f => f.forum_id === t_.forum_id)?.[lang === 'en' ? 'name_en' : 'name'] || t_.forum_id || '-'}</td>
              <td className="nowrap">{t_.category || '-'}</td>
              <td><ContentBadge kind={t_.content_kind} /></td>
              <td className="nowrap">{formatDateTime(t_.pub_time)}</td>
              <td className="nowrap">{formatDateTime(t_.sync_time)}</td>
            </tr>
          ))}
        </tbody>
      </table></div>
    </>
  )
}
