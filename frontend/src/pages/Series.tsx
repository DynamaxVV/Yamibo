import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, type SeriesSummary } from '../api/client'
import { Badge } from '../components/Badge'
import { useI18n } from '../context/I18nContext'

export function Series() {
  const { t } = useI18n()
  const [allSeries, setAllSeries] = useState<SeriesSummary[]>([])
  const [q, setQ] = useState(() => sessionStorage.getItem('series_q') || '')
  const [reviewFilter, setReviewFilter] = useState<'all' | 'review' | 'confirmed'>(() => (sessionStorage.getItem('series_reviewFilter') as 'all' | 'review' | 'confirmed') || 'all')
  const [page, setPage] = useState(1)
  const PAGE_SIZE = 50

  useEffect(() => { api.series().then(setAllSeries).catch(() => {}) }, [])

  useEffect(() => {
    sessionStorage.setItem('series_q', q)
    sessionStorage.setItem('series_reviewFilter', reviewFilter)
  }, [q, reviewFilter])

  const filtered = allSeries.filter(s => {
    if (reviewFilter === 'review' && !s.needs_review) return false
    if (reviewFilter === 'confirmed' && s.needs_review) return false
    if (q.trim()) {
      const keywords = q.trim().toLowerCase().split(/\s+/).filter(Boolean)
      const haystack = [s.canonical_title, s.series_key, s.author_guess].filter(Boolean).join(' ').toLowerCase()
      if (!keywords.every(kw => haystack.includes(kw))) return false
    }
    return true
  })

  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE))
  const paged = filtered.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE)

  return (
    <>
      <div className="filter-bar">
        <input
          className="filter-search"
          value={q}
          onChange={e => { setQ(e.target.value); setPage(1) }}
          placeholder={t('search_series_placeholder')}
        />
        <div className="filter-group">
          <select value={reviewFilter} onChange={e => { setReviewFilter(e.target.value as typeof reviewFilter); setPage(1) }}>
            <option value="all">{t('all')}</option>
            <option value="review">{t('pending_review')}</option>
            <option value="confirmed">{t('confirmed')}</option>
          </select>
        </div>
      </div>

      <div className="table-wrap"><table>
        <thead><tr><th>{t('id')}</th><th>{t('title')}</th><th>{t('author')}</th><th>{t('series_key')}</th><th>{t('thread_count')}</th><th>{t('review')}</th></tr></thead>
        <tbody>
          {paged.length === 0 ? (
            <tr><td colSpan={6} style={{ textAlign: 'center', color: 'var(--text-tertiary)', padding: 24 }}>{t('no_match')}</td></tr>
          ) : paged.map(s => (
            <tr key={s.series_id}>
              <td className="mono"><Link to={`/series/${s.series_id}`}>{s.series_id}</Link></td>
              <td className="truncate" style={{ textAlign: 'center' }} title={s.canonical_title || s.series_key || ''}><Link to={`/series/${s.series_id}`}>{s.canonical_title || s.series_key}</Link></td>
              <td>{s.author_guess || '-'}</td>
              <td className="mono">{s.series_key || '-'}</td>
              <td>{s.thread_count}</td>
              <td><Badge status={s.needs_review ? 'warn' : 'ok'}>{s.needs_review ? t('needs_review') : t('confirmed')}</Badge></td>
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
