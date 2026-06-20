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
            <td className="truncate" title={t_.display_title || t_.raw_title || ''}><Link to={`/threads/${t_.tid}`}>{t_.display_title || t_.raw_title}</Link></td>
            <td><Badge status={t_.archive_status} /></td>
            <td className="truncate" title={t_.export_path || ''}>{t_.export_path || '-'}</td>
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
              <td className="truncate" style={{ textAlign: 'center' }} title={s.canonical_title || s.series_key || ''}><Link to={`/series/${s.series_id}`}>{s.canonical_title || s.series_key}</Link></td>
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
  const [mergeTargetId, setMergeTargetId] = useState('')
  const [confirmMerge, setConfirmMerge] = useState(false)
  const [mergeTargetInfo, setMergeTargetInfo] = useState<SeriesSummary | null>(null)
  const [editing, setEditing] = useState(false)
  const [editForm, setEditForm] = useState({ series_key: '', author_guess: '' })

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

  const handleMerge = async () => {
    if (!mergeTargetId.trim()) return
    const targetId = parseInt(mergeTargetId)
    if (isNaN(targetId) || targetId === id) return
    setLoading('merge')
    try {
      const detail = await api.seriesDetail(targetId)
      setMergeTargetInfo(detail.series)
      setConfirmMerge(true)
    } catch {
      setMergeTargetInfo(null)
      setConfirmMerge(true)
    }
    setLoading(null)
  }

  const executeMerge = async () => {
    const targetId = parseInt(mergeTargetId)
    if (isNaN(targetId)) return
    setLoading('merge')
    try {
      await api.mergeSeries(id, targetId)
      navigate(`/series/${targetId}`)
    } catch { /* ignore */ }
    setLoading(null)
    setConfirmMerge(false)
  }

  const handleEdit = () => {
    setEditForm({
      series_key: data?.series.series_key || '',
      author_guess: data?.series.author_guess || '',
    })
    setEditing(true)
  }

  const handleSaveEdit = async () => {
    setLoading('edit')
    try {
      await api.updateSeries({ series_id: id, ...editForm })
      const refreshed = await api.seriesDetail(id)
      setData(refreshed)
      setEditing(false)
    } catch { /* ignore */ }
    setLoading(null)
  }

  if (error) return <div className="panel" style={{ color: 'var(--status-error)' }}>{error}</div>
  if (!data) return <div className="panel" style={{ color: 'var(--text-tertiary)' }}>{t('loading')}</div>

  const isEmpty = data.threads.length === 0

  return (
    <>
      <div className="panel">
        <div className="row-actions">
          <Link to="/series" className="btn-subtle">← {t('series_list')}</Link>
          <div className="input-btn-group">
            <input placeholder={t('merge_target_id')} value={mergeTargetId}
              onChange={e => setMergeTargetId(e.target.value)} />
            <button className="btn-subtle" disabled={loading === 'merge' || !mergeTargetId.trim()} onClick={handleMerge}>{t('merge_action')}</button>
          </div>
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

      <h2 style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        {t('series_info')}
        {!editing && (
          <button onClick={handleEdit} style={{ background: 'none', border: 'none', cursor: 'pointer', padding: 0, fontSize: 11, lineHeight: 1 }} title={t('edit')}>
            🛠️
          </button>
        )}
      </h2>
      <div className="table-wrap"><table>
        <tbody>
          {([
            ['ID', String(data.series.series_id)],
            [t('series_key'), data.series.series_key || '-'],
            [t('author'), data.series.author_guess || '-'],
          ] as [string, string][]).map(([k, v], i) => <tr key={i}><th style={{ width: 100 }}>{k}</th><td style={{ textAlign: 'left' }}>{v}</td></tr>)}
        </tbody>
      </table></div>
      {editing && (
        <div className="inline-edit">
          <div className="inline-edit-grid">
            <label>{t('series_key')}<input value={editForm.series_key} onChange={e => setEditForm(f => ({ ...f, series_key: e.target.value }))} /></label>
            <label>{t('author')}<input value={editForm.author_guess} onChange={e => setEditForm(f => ({ ...f, author_guess: e.target.value }))} /></label>
          </div>
          <div className="inline-edit-actions">
            <button className="btn-primary" disabled={loading === 'edit'} onClick={handleSaveEdit}>{t('save')}</button>
            <button className="btn-subtle" onClick={() => setEditing(false)}>{t('cancel')}</button>
          </div>
        </div>
      )}

      <h2>{t('threads')}</h2>
      <div className="table-wrap"><table>
        <thead><tr><th>{t('tid')}</th><th>{t('title')}</th><th>{t('chapter')}</th><th>{t('archive')}</th></tr></thead>
        <tbody>
          {data.threads.map(t_ => (
            <tr key={t_.tid}>
              <td className="mono"><Link to={`/threads/${t_.tid}`} state={{ from: 'series', seriesId: id }}>{t_.tid}</Link></td>
              <td className="truncate" title={t_.display_title || t_.raw_title || ''}><Link to={`/threads/${t_.tid}`} state={{ from: 'series', seriesId: id }}>{t_.display_title || t_.raw_title}</Link></td>
              <td>{t_.chapter_name || '-'}</td>
              <td><Badge status={t_.archive_status} /></td>
            </tr>
          ))}
        </tbody>
      </table></div>

      {confirmMerge && (
        <div className="confirm-overlay" onClick={() => setConfirmMerge(false)}>
          <div className="confirm-dialog" onClick={e => e.stopPropagation()}>
            <h2 style={{ margin: '0 0 16px', fontSize: 15, color: 'var(--text-primary)', textTransform: 'none', letterSpacing: 0 }}>{t('merge_series')}</h2>
            <div className="confirm-compare">
              <div className="confirm-col">
                <div className="confirm-col-header">{t('source')}</div>
                <div className="confirm-row"><span className="confirm-key">ID</span><span className="confirm-val">{id}</span></div>
                <div className="confirm-row"><span className="confirm-key">{t('series_name')}</span><span className="confirm-val">{data.series.canonical_title || '-'}</span></div>
                <div className="confirm-row"><span className="confirm-key">{t('thread_count')}</span><span className="confirm-val">{data.series.thread_count}</span></div>
              </div>
              <div className="confirm-arrow">→</div>
              <div className="confirm-col">
                <div className="confirm-col-header">{t('target')}</div>
                <div className="confirm-row"><span className="confirm-key">ID</span><span className="confirm-val">{mergeTargetId}</span></div>
                <div className="confirm-row"><span className="confirm-key">{t('series_name')}</span><span className="confirm-val">{mergeTargetInfo?.canonical_title || t('unknown')}</span></div>
                <div className="confirm-row"><span className="confirm-key">{t('thread_count')}</span><span className="confirm-val">{mergeTargetInfo?.thread_count ?? '?'}</span></div>
              </div>
            </div>
            <p style={{ fontSize: 12, color: 'var(--text-tertiary)', margin: '0 0 16px' }}>
              {t('merge_desc')} {mergeTargetId}
            </p>
            <div className="confirm-actions">
              <button className="btn-subtle" onClick={() => setConfirmMerge(false)}>{t('cancel')}</button>
              <button className="btn-primary" disabled={loading === 'merge'} onClick={executeMerge}>{t('confirm_execute')}</button>
            </div>
          </div>
        </div>
      )}
    </>
  )
}
