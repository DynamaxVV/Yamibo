import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, type SeriesSummary } from '../api/client'
import { Badge } from '../components/Badge'

export function Series() {
  const [allSeries, setAllSeries] = useState<SeriesSummary[]>([])
  const [q, setQ] = useState('')
  const [reviewFilter, setReviewFilter] = useState<'all' | 'review' | 'confirmed'>('all')

  useEffect(() => { api.series().then(setAllSeries).catch(() => {}) }, [])

  const filtered = allSeries.filter(s => {
    if (reviewFilter === 'review' && !s.needs_review) return false
    if (reviewFilter === 'confirmed' && s.needs_review) return false
    if (q.trim()) {
      const needle = q.trim().toLowerCase()
      const haystack = [s.canonical_title, s.series_key, s.author_guess].filter(Boolean).join(' ').toLowerCase()
      if (!haystack.includes(needle)) return false
    }
    return true
  })

  return (
    <>
      <div className="filter-bar">
        <input
          className="filter-search"
          value={q}
          onChange={e => setQ(e.target.value)}
          placeholder="搜索系列名、系列键、作者"
        />
        <div className="filter-group">
          <select value={reviewFilter} onChange={e => setReviewFilter(e.target.value as typeof reviewFilter)}>
            <option value="all">全部</option>
            <option value="review">待复核</option>
            <option value="confirmed">已确认</option>
          </select>
        </div>
      </div>

      <div className="table-wrap"><table>
        <thead><tr><th>ID</th><th>标题</th><th>作者</th><th>系列键</th><th>贴子数</th><th>复核</th></tr></thead>
        <tbody>
          {filtered.length === 0 ? (
            <tr><td colSpan={6} style={{ textAlign: 'center', color: 'var(--text-tertiary)', padding: 24 }}>无匹配系列</td></tr>
          ) : filtered.map(s => (
            <tr key={s.series_id}>
              <td className="mono"><Link to={`/series/${s.series_id}`}>{s.series_id}</Link></td>
              <td className="truncate"><Link to={`/series/${s.series_id}`}>{s.canonical_title || s.series_key}</Link></td>
              <td>{s.author_guess || '-'}</td>
              <td className="mono">{s.series_key || '-'}</td>
              <td>{s.thread_count}</td>
              <td><Badge status={s.needs_review ? 'warn' : 'ok'}>{s.needs_review ? '需复核' : '已确认'}</Badge></td>
            </tr>
          ))}
        </tbody>
      </table></div>
    </>
  )
}
