import { useEffect, useState, useCallback } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api, type ThreadSummary, type Forum } from '../api/client'
import { ContentBadge } from '../components/Badge'

const FORUM_NAMES: Record<number, string> = {}

const DATE_OPTIONS = [
  { label: '全部时间', value: undefined },
  { label: '一天内', value: 1 },
  { label: '三天内', value: 3 },
  { label: '一周内', value: 7 },
  { label: '一月内', value: 30 },
  { label: '三月内', value: 90 },
]

type SortKey = 'pub_time' | 'sync_time'
type SortDir = 'asc' | 'desc'

function formatTime(iso: string | null): string {
  if (!iso) return '-'
  try {
    const d = new Date(iso)
    if (isNaN(d.getTime())) return iso
    const utc8 = new Date(d.getTime() + 8 * 60 * 60 * 1000)
    const y = utc8.getUTCFullYear()
    const m = String(utc8.getUTCMonth() + 1).padStart(2, '0')
    const day = String(utc8.getUTCDate()).padStart(2, '0')
    const h = String(utc8.getUTCHours()).padStart(2, '0')
    const min = String(utc8.getUTCMinutes()).padStart(2, '0')
    const s = String(utc8.getUTCSeconds()).padStart(2, '0')
    return `${y}-${m}-${day} ${h}:${min}:${s}`
  } catch {
    return iso
  }
}

function SortHeader({ label, sortKey, currentKey, currentDir, onSort }: {
  label: string; sortKey: SortKey; currentKey: SortKey | null; currentDir: SortDir; onSort: (key: SortKey) => void
}) {
  const active = currentKey === sortKey
  const arrow = active ? (currentDir === 'asc' ? ' ▲' : ' ▼') : ''
  return (
    <th className="sortable" onClick={() => onSort(sortKey)}>
      {label}<span className="sort-arrow">{arrow}</span>
    </th>
  )
}

export function Threads() {
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
    api.forums().then(fs => {
      setForums(fs)
      fs.forEach(f => { FORUM_NAMES[f.forum_id] = f.name })
    }).catch(() => {})
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

  return (
    <>
      <div className="filter-bar">
        <input
          className="filter-search"
          value={q}
          onChange={e => setQ(e.target.value)}
          placeholder="搜索标题、作者、汉化组、发布者"
          onKeyDown={e => e.key === 'Enter' && search()}
        />
        <div className="filter-group">
          <select value={forumId ?? ''} onChange={e => setForumId(e.target.value ? Number(e.target.value) : undefined)}>
            <option value="">全部版块</option>
            {forums.map(f => <option key={f.forum_id} value={f.forum_id}>{f.name}</option>)}
          </select>
          <select value={days ?? ''} onChange={e => setDays(e.target.value ? Number(e.target.value) : undefined)}>
            {DATE_OPTIONS.map(o => <option key={o.label} value={o.value ?? ''}>{o.label}</option>)}
          </select>
        </div>
        <button className="btn-primary" onClick={search}>搜索</button>
      </div>

      <div className="table-wrap"><table>
        <thead>
          <tr>
            <th>TID</th>
            <th>标题</th>
            <th>版块</th>
            <th>归档</th>
            <SortHeader label="发布时间" sortKey="pub_time" currentKey={sortKey} currentDir={sortDir} onSort={handleSort} />
            <SortHeader label="同步时间" sortKey="sync_time" currentKey={sortKey} currentDir={sortDir} onSort={handleSort} />
          </tr>
        </thead>
        <tbody>
          {sorted.map(t => (
            <tr key={t.tid}>
              <td className="mono"><Link to={`/threads/${t.tid}`}>{t.tid}</Link></td>
              <td className="truncate"><Link to={`/threads/${t.tid}`}>{t.display_title || t.raw_title}</Link></td>
              <td className="nowrap">{FORUM_NAMES[t.forum_id ?? 0] || t.forum_id || '-'}</td>
              <td><ContentBadge kind={t.content_kind} /></td>
              <td className="nowrap">{formatTime(t.pub_time)}</td>
              <td className="nowrap">{formatTime(t.sync_time)}</td>
            </tr>
          ))}
        </tbody>
      </table></div>
    </>
  )
}
