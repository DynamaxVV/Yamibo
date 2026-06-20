import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api, type ThreadSummary, type SeriesSummary } from '../api/client'
import { Badge } from '../components/Badge'
import { useI18n } from '../context/I18nContext'

export function Exports() {
  const { t } = useI18n()
  const [exports, setExports] = useState<ThreadSummary[]>([])
  useEffect(() => { api.exports().then(setExports) }, [])

  return (
    <div className="table-wrap"><table>
      <thead><tr><th>{t('tid')}</th><th>{t('title')}</th><th>{t('archive_status')}</th><th>{t('export_path')}</th></tr></thead>
      <tbody>
        {exports.map(t_ => (
          <tr key={t_.tid}>
            <td className="mono"><Link to={`/threads/${t_.tid}`}>{t_.tid}</Link></td>
            <td className="truncate"><Link to={`/threads/${t_.tid}`}>{t_.display_title || t_.raw_title}</Link></td>
            <td><Badge status={t_.archive_status} /></td>
            <td className="truncate">{t_.export_path || '-'}</td>
          </tr>
        ))}
      </tbody>
    </table></div>
  )
}

export function Series() {
  const { t } = useI18n()
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
          placeholder={t('search_series_placeholder')}
        />
        <div className="filter-group">
          <select value={reviewFilter} onChange={e => setReviewFilter(e.target.value as typeof reviewFilter)}>
            <option value="all">{t('all')}</option>
            <option value="review">{t('pending_review')}</option>
            <option value="confirmed">{t('confirmed')}</option>
          </select>
        </div>
      </div>

      <div className="table-wrap"><table>
        <thead><tr><th>{t('id')}</th><th>{t('title')}</th><th>{t('author')}</th><th>{t('series_key')}</th><th>{t('thread_count')}</th><th>{t('review')}</th></tr></thead>
        <tbody>
          {filtered.length === 0 ? (
            <tr><td colSpan={6} style={{ textAlign: 'center', color: 'var(--text-tertiary)', padding: 24 }}>{t('no_match')}</td></tr>
          ) : filtered.map(s => (
            <tr key={s.series_id}>
              <td className="mono"><Link to={`/series/${s.series_id}`}>{s.series_id}</Link></td>
              <td className="truncate" style={{ textAlign: 'center' }}><Link to={`/series/${s.series_id}`}>{s.canonical_title || s.series_key}</Link></td>
              <td>{s.author_guess || '-'}</td>
              <td className="mono">{s.series_key || '-'}</td>
              <td>{s.thread_count}</td>
              <td><Badge status={s.needs_review ? 'warn' : 'ok'}>{s.needs_review ? t('needs_review') : t('confirmed')}</Badge></td>
            </tr>
          ))}
        </tbody>
      </table></div>
    </>
  )
}

export function SeriesDetail() {
  const { t } = useI18n()
  const id = parseInt(window.location.pathname.split('/').pop() || '0')
  const navigate = useNavigate()
  const [data, setData] = useState<{ series: SeriesSummary; threads: ThreadSummary[] } | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [loading, setLoading] = useState<string | null>(null)

  useEffect(() => {
    api.seriesDetail(id).then(setData).catch(e => setError(e.message))
  }, [id])

  const handleDelete = async () => {
    if (!confirmDelete) { setConfirmDelete(true); return }
    setLoading('delete')
    try {
      await api.deleteSeries(id)
      navigate('/series')
    } catch { /* ignore */ }
    setLoading(null)
    setConfirmDelete(false)
  }

  if (error) return <div className="panel" style={{ color: 'var(--status-error)' }}>{error}</div>
  if (!data) return <div className="panel" style={{ color: 'var(--text-tertiary)' }}>{t('loading')}</div>

  const isEmpty = data.threads.length === 0

  return (
    <>
      <div className="panel">
        <div className="row-actions">
          <Link to="/series" className="btn-subtle">← {t('series_list')}</Link>
          {confirmDelete ? (
            <>
              <button className="btn-danger" disabled={loading === 'delete' || !isEmpty} onClick={handleDelete}>
                {isEmpty ? t('confirm_delete') : t('cannot_delete_nonempty')}
              </button>
              <button className="btn-subtle" onClick={() => setConfirmDelete(false)}>{t('cancel')}</button>
            </>
          ) : (
            <button className="btn-danger-outline" disabled={!isEmpty} onClick={() => setConfirmDelete(true)}>
              {t('delete_series')}
            </button>
          )}
          {!isEmpty && <span style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>{t('only_empty_deletable')}</span>}
        </div>
      </div>

      <h2>{t('series_info')}</h2>
      <div className="table-wrap"><table>
        <tbody>
          {([
            ['ID', String(data.series.series_id)],
            [t('series_key'), data.series.series_key || '-'],
            [t('author'), data.series.author_guess || '-'],
          ] as [string, string][]).map(([k, v], i) => <tr key={i}><th style={{ width: 100 }}>{k}</th><td style={{ textAlign: 'left' }}>{v}</td></tr>)}
        </tbody>
      </table></div>

      <h2>{t('threads')}</h2>
      <div className="table-wrap"><table>
        <thead><tr><th>{t('tid')}</th><th>{t('title')}</th><th>{t('chapter')}</th><th>{t('archive')}</th></tr></thead>
        <tbody>
          {data.threads.map(t_ => (
            <tr key={t_.tid}>
              <td className="mono"><Link to={`/threads/${t_.tid}`} state={{ from: 'series', seriesId: id }}>{t_.tid}</Link></td>
              <td className="truncate"><Link to={`/threads/${t_.tid}`} state={{ from: 'series', seriesId: id }}>{t_.display_title || t_.raw_title}</Link></td>
              <td>{t_.chapter_name || '-'}</td>
              <td><Badge status={t_.archive_status} /></td>
            </tr>
          ))}
        </tbody>
      </table></div>
    </>
  )
}
