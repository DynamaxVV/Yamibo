import { useEffect, useState, useCallback } from 'react'
import { Link } from 'react-router-dom'
import { api, type ThreadSummary, type SeriesSummary } from '../api/client'
import { useI18n } from '../context/I18nContext'

interface ConfirmAction {
  type: 'title' | 'series' | 'merge' | 'confirmTitle' | 'confirmSeries' | 'rebuild'
  label: string
  before: Record<string, string>
  after: Record<string, string>
  execute: () => Promise<void>
}

export function Review() {
  const { t } = useI18n()
  const [titles, setTitles] = useState<ThreadSummary[]>([])
  const [series, setSeries] = useState<SeriesSummary[]>([])
  const [mergeTarget, setMergeTarget] = useState<Record<number, string>>({})
  const [loading, setLoading] = useState<Record<string, boolean>>({})
  const [editingTitle, setEditingTitle] = useState<number | null>(null)
  const [editingSeries, setEditingSeries] = useState<number | null>(null)
  const [titleForm, setTitleForm] = useState<Record<string, string>>({})
  const [seriesForm, setSeriesForm] = useState<Record<string, string>>({})
  const [confirm, setConfirm] = useState<ConfirmAction | null>(null)
  const [confirmError, setConfirmError] = useState<string | null>(null)
  const [similarMap, setSimilarMap] = useState<Record<number, SeriesSummary[]>>({})

  const refresh = useCallback(() => {
    api.reviewItems().then(async d => {
      setTitles(d.titles)
      setSeries(d.series)
      const sims: Record<number, SeriesSummary[]> = {}
      await Promise.all(d.series.map(async s => {
        try {
          const similar = await api.similarSeries(s.series_id)
          if (similar.length > 0) sims[s.series_id] = similar
        } catch { /* ignore */ }
      }))
      setSimilarMap(sims)
    }).catch(() => {})
  }, [])

  useEffect(() => { refresh() }, [refresh])

  const startEditTitle = (th: ThreadSummary) => {
    setEditingTitle(editingTitle === th.tid ? null : th.tid)
    setEditingSeries(null)
    setTitleForm({
      display_title: th.display_title || '',
      author_guess: th.publisher || '',
      core_title_guess: th.core_title_guess || '',
      series_key: th.series_key || '',
    })
  }

  const proposeSaveTitle = () => {
    if (!editingTitle) return
    const th = titles.find(x => x.tid === editingTitle)
    if (!th) return
    setConfirm({
      type: 'title',
      label: t('save_title_change'),
      before: {
        [t('title')]: th.display_title || th.raw_title || '',
        [t('author')]: th.publisher || '',
        [t('core_title')]: th.core_title_guess || '',
        [t('series_key')]: th.series_key || '',
      },
      after: {
        [t('title')]: titleForm.display_title || '',
        [t('author')]: titleForm.author_guess || '',
        [t('core_title')]: titleForm.core_title_guess || '',
        [t('series_key')]: titleForm.series_key || '',
      },
      execute: async () => {
        await api.updateTitle({ tid: editingTitle, ...titleForm })
        setEditingTitle(null)
        refresh()
      },
    })
  }

  const startEditSeries = (s: SeriesSummary) => {
    setEditingSeries(editingSeries === s.series_id ? null : s.series_id)
    setEditingTitle(null)
    setSeriesForm({
      canonical_title: s.canonical_title || '',
      series_key: s.series_key || '',
      author_guess: s.author_guess || '',
    })
  }

  const proposeSaveSeries = () => {
    if (!editingSeries) return
    const s = series.find(x => x.series_id === editingSeries)
    if (!s) return
    setConfirm({
      type: 'series',
      label: t('save_series_change'),
      before: {
        [t('series_name')]: s.canonical_title || '',
        [t('series_key')]: s.series_key || '',
        [t('author')]: s.author_guess || '',
      },
      after: {
        [t('series_name')]: seriesForm.canonical_title || '',
        [t('series_key')]: seriesForm.series_key || '',
        [t('author')]: seriesForm.author_guess || '',
      },
      execute: async () => {
        await api.updateSeries({ series_id: editingSeries, ...seriesForm })
        setEditingSeries(null)
        refresh()
      },
    })
  }

  const proposeConfirmTitle = (th: ThreadSummary) => {
    setConfirm({
      type: 'confirmTitle',
      label: t('confirm'),
      before: { 'TID': String(th.tid), [t('title')]: th.display_title || th.raw_title || '', [t('review_status')]: t('pending_review') },
      after: { 'TID': String(th.tid), [t('title')]: th.display_title || th.raw_title || '', [t('review_status')]: t('confirmed') },
      execute: async () => { await api.confirmTitle(th.tid); refresh() },
    })
  }

  const proposeConfirmSeries = (s: SeriesSummary) => {
    setConfirm({
      type: 'confirmSeries',
      label: t('confirm'),
      before: { 'ID': String(s.series_id), [t('series_name')]: s.canonical_title || '', [t('review_status')]: t('pending_review') },
      after: { 'ID': String(s.series_id), [t('series_name')]: s.canonical_title || '', [t('review_status')]: t('confirmed') },
      execute: async () => { await api.confirmSeries(s.series_id); refresh() },
    })
  }

  const proposeMerge = async (s: SeriesSummary) => {
    const targetStr = mergeTarget[s.series_id]
    if (!targetStr?.trim()) return
    const targetId = parseInt(targetStr)
    if (isNaN(targetId)) return

    let targetTitle = `(${t('unknown')})`
    let targetAuthor = '-'
    let targetKey = '-'
    let targetCount = 0
    try {
      const detail = await api.seriesDetail(targetId)
      targetTitle = detail.series.canonical_title || detail.series.series_key || `(${t('unknown')})`
      targetAuthor = detail.series.author_guess || '-'
      targetKey = detail.series.series_key || '-'
      targetCount = detail.series.thread_count
    } catch { /* use defaults */ }

    setConfirm({
      type: 'merge',
      label: t('merge_series'),
      before: {
        [t('source_id')]: String(s.series_id),
        [t('source_name')]: s.canonical_title || '',
        [t('source_author')]: s.author_guess || '-',
        [t('source_thread_count')]: String(s.thread_count),
      },
      after: {
        [t('target_id')]: String(targetId),
        [t('target_name')]: targetTitle,
        [t('target_author')]: targetAuthor,
        [t('target_key')]: targetKey,
        [t('target_thread_count')]: String(targetCount),
        [t('result')]: `${t('merge_desc')} ${targetId}`,
      },
      execute: async () => {
        await api.mergeSeries(s.series_id, targetId)
        setMergeTarget(m => { const n = { ...m }; delete n[s.series_id]; return n })
        refresh()
      },
    })
  }

  const executeConfirm = async () => {
    if (!confirm) return
    setConfirmError(null)
    setLoading(l => ({ ...l, [confirm.type]: true }))
    try {
      await confirm.execute()
      setConfirm(null)
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e)
      setConfirmError(msg)
      console.error('Review action failed:', msg)
    }
    setLoading(l => ({ ...l, [confirm.type]: false }))
  }

  return (
    <>
      <h2>{t('series_review')}</h2>
      {series.length === 0 ? (
        <div className="panel" style={{ color: 'var(--text-tertiary)' }}>{t('no_pending_series')}</div>
      ) : (
        <div className="table-wrap"><table className="review-table" style={{ tableLayout: 'fixed', width: '100%' }}>
          <thead><tr><th style={{ width: 45 }}>{t('id')}</th><th style={{ width: '30%' }}>{t('title')}</th><th style={{ width: 160 }}>{t('author')}</th><th style={{ width: 260 }}>{t('series_key')}</th><th style={{ width: '20%' }}>{t('action')}</th></tr></thead>
          <tbody>
            {series.flatMap(s => {
              const sims = similarMap[s.series_id] || []
              return [
              <tr key={s.series_id}>
                <td className="mono"><Link to={`/series/${s.series_id}`}>{s.series_id}</Link></td>
                <td className="truncate" title={s.canonical_title || s.series_key || ''}>{s.canonical_title || s.series_key}</td>
                <td className="truncate" title={s.author_guess || ''}>{s.author_guess || '-'}</td>
                <td className="mono truncate" title={s.series_key || ''}>{s.series_key || '-'}</td>
                <td>
                  <div className="row-actions" style={{ gap: 4, flexWrap: 'wrap' }}>
                    <button className="btn-subtle" onClick={() => startEditSeries(s)}>{editingSeries === s.series_id ? t('collapse') : t('edit')}</button>
                    <button className="btn-subtle" onClick={() => proposeConfirmSeries(s)}>{t('confirm')}</button>
                    <div className="input-btn-group">
                      <input placeholder={t('merge_target_id')} value={mergeTarget[s.series_id] || ''}
                        onChange={e => setMergeTarget(m => ({ ...m, [s.series_id]: e.target.value }))} />
                      <button className="btn-subtle" onClick={() => proposeMerge(s)}>{t('merge_action')}</button>
                    </div>
                  </div>
                </td>
              </tr>,
              ...(sims.length ? [
                <tr key={`${s.series_id}-similar`} style={{ background: 'var(--bg-muted)' }}>
                  <td colSpan={2} style={{ padding: '3px 8px', borderTop: '1px dashed var(--border-light)' }}>
                    <span style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>{t('similar_label')}</span>
                  </td>
                  <td colSpan={3} style={{ padding: '3px 8px', borderTop: '1px dashed var(--border-light)' }}>
                    <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', alignItems: 'center' }}>
                      {sims.map(sim => (
                        <button
                          key={sim.series_id}
                          className="btn-chip"
                          onClick={() => setMergeTarget(m => ({ ...m, [s.series_id]: String(sim.series_id) }))}
                          title={`${sim.canonical_title || sim.series_key} (${sim.thread_count}${t('thread_link')})`}
                        >
                          {sim.series_id}: {(sim.canonical_title || sim.series_key || '').slice(0, 16)}
                        </button>
                      ))}
                    </div>
                  </td>
                </tr>,
              ] : []),
              ...(editingSeries === s.series_id ? [
                <tr key={`${s.series_id}-edit`}>
                  <td colSpan={5} style={{ padding: 0 }}>
                    <div className="inline-edit">
                      <div className="inline-edit-grid">
                        <label>{t('series_name')}<input value={seriesForm.canonical_title || ''} onChange={e => setSeriesForm(f => ({ ...f, canonical_title: e.target.value }))} /></label>
                        <label>{t('series_key')}<input value={seriesForm.series_key || ''} onChange={e => setSeriesForm(f => ({ ...f, series_key: e.target.value }))} /></label>
                        <label>{t('author')}<input value={seriesForm.author_guess || ''} onChange={e => setSeriesForm(f => ({ ...f, author_guess: e.target.value }))} /></label>
                      </div>
                      <div className="inline-edit-actions">
                        <button className="btn-primary" onClick={proposeSaveSeries}>{t('save')}</button>
                        <button className="btn-subtle" onClick={() => setEditingSeries(null)}>{t('cancel')}</button>
                      </div>
                    </div>
                  </td>
                </tr>,
              ] : []),
            ]})}
          </tbody>
        </table></div>
      )}

      <h2>{t('thread_title_review')}</h2>
      {titles.length === 0 ? (
        <div className="panel" style={{ color: 'var(--text-tertiary)' }}>{t('no_pending_titles')}</div>
      ) : (
        <div className="table-wrap"><table className="review-table">
          <thead><tr><th>{t('tid')}</th><th>{t('title')}</th><th>{t('core_title')}</th><th>{t('author')}</th><th>{t('series_key')}</th><th>{t('action')}</th></tr></thead>
          <tbody>
            {titles.flatMap(th => [
              <tr key={th.tid}>
                <td className="mono"><Link to={`/threads/${th.tid}`}>{th.tid}</Link></td>
                <td className="truncate" title={th.display_title || th.raw_title || ''}>{th.display_title || th.raw_title}</td>
                <td>{th.core_title_guess || '-'}</td>
                <td>{th.publisher || '-'}</td>
                <td className="mono">{th.series_key || '-'}</td>
                <td>
                  <div className="row-actions">
                    <button className="btn-subtle" onClick={() => startEditTitle(th)}>
                      {editingTitle === th.tid ? t('collapse') : t('edit')}
                    </button>
                    <button className="btn-subtle" onClick={() => proposeConfirmTitle(th)}>{t('confirm')}</button>
                  </div>
                </td>
              </tr>,
              ...(editingTitle === th.tid ? [
                <tr key={`${th.tid}-edit`}>
                  <td colSpan={6} style={{ padding: 0 }}>
                    <div className="inline-edit">
                      <div className="inline-edit-grid">
                        <label>{t('title')}<input value={titleForm.display_title || ''} onChange={e => setTitleForm(f => ({ ...f, display_title: e.target.value }))} /></label>
                        <label>{t('author')}<input value={titleForm.author_guess || ''} onChange={e => setTitleForm(f => ({ ...f, author_guess: e.target.value }))} /></label>
                        <label>{t('core_title')}<input value={titleForm.core_title_guess || ''} onChange={e => setTitleForm(f => ({ ...f, core_title_guess: e.target.value }))} /></label>
                        <label>{t('series_key')}<input value={titleForm.series_key || ''} onChange={e => setTitleForm(f => ({ ...f, series_key: e.target.value }))} /></label>
                      </div>
                      <div className="inline-edit-actions">
                        <button className="btn-primary" onClick={proposeSaveTitle}>{t('save_and_confirm')}</button>
                        <button className="btn-subtle" onClick={() => setEditingTitle(null)}>{t('cancel')}</button>
                      </div>
                    </div>
                  </td>
                </tr>,
              ] : []),
            ])}
          </tbody>
        </table></div>
      )}

      {confirm && (
        <div className="confirm-overlay" onClick={() => { setConfirm(null); setConfirmError(null) }}>
          <div className="confirm-dialog" onClick={e => e.stopPropagation()}>
            <h2 style={{ margin: '0 0 16px', fontSize: 15, color: 'var(--text-primary)', textTransform: 'none', letterSpacing: 0 }}>{confirm.label}</h2>
            <div className="confirm-compare">
              <div className="confirm-col">
                <div className="confirm-col-header">{t('modify_before')}</div>
                {Object.entries(confirm.before).map(([k, v]) => (
                  <div key={k} className="confirm-row"><span className="confirm-key">{k}</span><span className="confirm-val">{v || '-'}</span></div>
                ))}
              </div>
              <div className="confirm-arrow">→</div>
              <div className="confirm-col">
                <div className="confirm-col-header">{t('modify_after')}</div>
                {Object.entries(confirm.after).map(([k, v]) => (
                  <div key={k} className="confirm-row"><span className="confirm-key">{k}</span><span className="confirm-val">{v || '-'}</span></div>
                ))}
              </div>
            </div>
            <div className="confirm-actions">
              {confirmError && <div style={{ color: 'var(--status-error)', fontSize: 12, marginRight: 'auto' }}>{t('error')}: {confirmError}</div>}
              <button className="btn-primary" disabled={loading[confirm.type]} onClick={executeConfirm}>{t('confirm_execute')}</button>
              <button className="btn-subtle" onClick={() => { setConfirm(null); setConfirmError(null) }}>{t('cancel')}</button>
            </div>
          </div>
        </div>
      )}
    </>
  )
}
