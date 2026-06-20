import { useEffect, useState, useCallback } from 'react'
import { Link } from 'react-router-dom'
import { api, type ThreadSummary, type SeriesSummary } from '../api/client'

interface ConfirmAction {
  type: 'title' | 'series' | 'merge' | 'confirmTitle' | 'confirmSeries' | 'rebuild'
  label: string
  before: Record<string, string>
  after: Record<string, string>
  execute: () => Promise<void>
}

export function Review() {
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
      // Fetch similar series for each review item
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

  // ── Title editing ──

  const startEditTitle = (t: ThreadSummary) => {
    setEditingTitle(editingTitle === t.tid ? null : t.tid)
    setEditingSeries(null)
    setTitleForm({
      display_title: t.display_title || '',
      author_guess: t.publisher || '',
      core_title_guess: t.core_title_guess || '',
      series_key: t.series_key || '',
    })
  }

  const proposeSaveTitle = () => {
    if (!editingTitle) return
    const t = titles.find(x => x.tid === editingTitle)
    if (!t) return
    setConfirm({
      type: 'title',
      label: '保存标题修改',
      before: {
        '标题': t.display_title || t.raw_title || '',
        '作者': t.publisher || '',
        '核心标题': t.core_title_guess || '',
        '系列键': t.series_key || '',
      },
      after: {
        '标题': titleForm.display_title || '',
        '作者': titleForm.author_guess || '',
        '核心标题': titleForm.core_title_guess || '',
        '系列键': titleForm.series_key || '',
      },
      execute: async () => {
        await api.updateTitle({ tid: editingTitle, ...titleForm })
        setEditingTitle(null)
        refresh()
      },
    })
  }

  // ── Series editing ──

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
      label: '保存系列修改',
      before: {
        '系列名': s.canonical_title || '',
        '系列键': s.series_key || '',
        '作者': s.author_guess || '',
      },
      after: {
        '系列名': seriesForm.canonical_title || '',
        '系列键': seriesForm.series_key || '',
        '作者': seriesForm.author_guess || '',
      },
      execute: async () => {
        await api.updateSeries({ series_id: editingSeries, ...seriesForm })
        setEditingSeries(null)
        refresh()
      },
    })
  }

  // ── Confirm/merge actions ──

  const proposeConfirmTitle = (t: ThreadSummary) => {
    setConfirm({
      type: 'confirmTitle',
      label: '确认标题',
      before: { 'TID': String(t.tid), '标题': t.display_title || t.raw_title || '', '复核状态': '待复核' },
      after: { 'TID': String(t.tid), '标题': t.display_title || t.raw_title || '', '复核状态': '已确认' },
      execute: async () => { await api.confirmTitle(t.tid); refresh() },
    })
  }

  const proposeConfirmSeries = (s: SeriesSummary) => {
    setConfirm({
      type: 'confirmSeries',
      label: '确认系列',
      before: { 'ID': String(s.series_id), '系列名': s.canonical_title || '', '复核状态': '待复核' },
      after: { 'ID': String(s.series_id), '系列名': s.canonical_title || '', '复核状态': '已确认' },
      execute: async () => { await api.confirmSeries(s.series_id); refresh() },
    })
  }

  const proposeMerge = async (s: SeriesSummary) => {
    const targetStr = mergeTarget[s.series_id]
    if (!targetStr?.trim()) return
    const targetId = parseInt(targetStr)
    if (isNaN(targetId)) return

    // Fetch target series info from API
    let targetTitle = '(未知)'
    let targetAuthor = '-'
    let targetKey = '-'
    let targetCount = 0
    try {
      const detail = await api.seriesDetail(targetId)
      targetTitle = detail.series.canonical_title || detail.series.series_key || '(未知)'
      targetAuthor = detail.series.author_guess || '-'
      targetKey = detail.series.series_key || '-'
      targetCount = detail.series.thread_count
    } catch { /* use defaults */ }

    setConfirm({
      type: 'merge',
      label: '合并系列',
      before: {
        '源系列 ID': String(s.series_id),
        '源系列名': s.canonical_title || '',
        '源系列作者': s.author_guess || '-',
        '源系列贴子数': String(s.thread_count),
      },
      after: {
        '目标系列 ID': String(targetId),
        '目标系列名': targetTitle,
        '目标系列作者': targetAuthor,
        '目标系列键': targetKey,
        '目标系列贴子数': String(targetCount),
        '结果': `源系列的所有贴子将转移到目标系列 ${targetId}`,
      },
      execute: async () => {
        await api.mergeSeries(s.series_id, targetId)
        setMergeTarget(m => { const n = { ...m }; delete n[s.series_id]; return n })
        refresh()
      },
    })
  }

  const proposeRebuild = () => {
    setConfirm({
      type: 'rebuild',
      label: '批量重算系列',
      before: { '操作': '当前系列分配' },
      after: { '操作': '重新计算所有贴子的系列归属' },
      execute: async () => { await api.rebuildSeries(); refresh() },
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
      <h2>贴子标题复核</h2>
      {titles.length === 0 ? (
        <div className="panel" style={{ color: 'var(--text-tertiary)' }}>暂无待复核标题。</div>
      ) : (
        <div className="table-wrap"><table>
          <thead><tr><th>TID</th><th>标题</th><th>核心标题</th><th>作者</th><th>系列键</th><th>操作</th></tr></thead>
          <tbody>
            {titles.flatMap(t => [
              <tr key={t.tid}>
                <td className="mono"><Link to={`/threads/${t.tid}`}>{t.tid}</Link></td>
                <td className="truncate">{t.display_title || t.raw_title}</td>
                <td>{t.core_title_guess || '-'}</td>
                <td>{t.publisher || '-'}</td>
                <td className="mono">{t.series_key || '-'}</td>
                <td>
                  <div className="row-actions">
                    <button className="btn-subtle" onClick={() => startEditTitle(t)}>
                      {editingTitle === t.tid ? '收起' : '编辑'}
                    </button>
                    <button className="btn-subtle" onClick={() => proposeConfirmTitle(t)}>确认</button>
                  </div>
                </td>
              </tr>,
              ...(editingTitle === t.tid ? [
                <tr key={`${t.tid}-edit`}>
                  <td colSpan={6} style={{ padding: 0 }}>
                    <div className="inline-edit">
                      <div className="inline-edit-grid">
                        <label>标题<input value={titleForm.display_title || ''} onChange={e => setTitleForm(f => ({ ...f, display_title: e.target.value }))} /></label>
                        <label>作者<input value={titleForm.author_guess || ''} onChange={e => setTitleForm(f => ({ ...f, author_guess: e.target.value }))} /></label>
                        <label>核心标题<input value={titleForm.core_title_guess || ''} onChange={e => setTitleForm(f => ({ ...f, core_title_guess: e.target.value }))} /></label>
                        <label>系列键<input value={titleForm.series_key || ''} onChange={e => setTitleForm(f => ({ ...f, series_key: e.target.value }))} /></label>
                      </div>
                      <div className="inline-edit-actions">
                        <button className="btn-primary" onClick={proposeSaveTitle}>保存并确认</button>
                        <button className="btn-subtle" onClick={() => setEditingTitle(null)}>取消</button>
                      </div>
                    </div>
                  </td>
                </tr>,
              ] : []),
            ])}
          </tbody>
        </table></div>
      )}

      <h2>系列复核</h2>
      <div style={{ marginBottom: 12 }}>
        <button className="btn-subtle" onClick={proposeRebuild}>批量重算系列</button>
      </div>
      {series.length === 0 ? (
        <div className="panel" style={{ color: 'var(--text-tertiary)' }}>暂无待复核系列。</div>
      ) : (
        <div className="table-wrap"><table>
          <thead><tr><th>ID</th><th>标题</th><th>作者</th><th>系列键</th><th>贴子数</th><th>操作</th></tr></thead>
          <tbody>
            {series.flatMap(s => [
              <tr key={s.series_id}>
                <td className="mono"><Link to={`/series/${s.series_id}`}>{s.series_id}</Link></td>
                <td className="truncate">{s.canonical_title || s.series_key}</td>
                <td>{s.author_guess || '-'}</td>
                <td className="mono">{s.series_key || '-'}</td>
                <td>{s.thread_count}</td>
                <td>
                  <div className="row-actions">
                    <button className="btn-subtle" onClick={() => startEditSeries(s)}>
                      {editingSeries === s.series_id ? '收起' : '编辑'}
                    </button>
                    <button className="btn-subtle" onClick={() => proposeConfirmSeries(s)}>确认</button>
                    <input className="input-sm" size={6} placeholder="目标ID" value={mergeTarget[s.series_id] || ''}
                      onChange={e => setMergeTarget(m => ({ ...m, [s.series_id]: e.target.value }))} />
                    <button className="btn-subtle" onClick={() => proposeMerge(s)}>合并</button>
                  </div>
                  {similarMap[s.series_id] && (
                    <div style={{ marginTop: 4, display: 'flex', gap: 4, flexWrap: 'wrap', alignItems: 'center' }}>
                      <span style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>相似:</span>
                      {similarMap[s.series_id].map(sim => (
                        <button
                          key={sim.series_id}
                          className="btn-chip"
                          onClick={() => setMergeTarget(m => ({ ...m, [s.series_id]: String(sim.series_id) }))}
                          title={`${sim.canonical_title || sim.series_key} (${sim.thread_count}贴)`}
                        >
                          {sim.series_id}: {(sim.canonical_title || sim.series_key || '').slice(0, 12)}
                        </button>
                      ))}
                    </div>
                  )}
                </td>
              </tr>,
              ...(editingSeries === s.series_id ? [
                <tr key={`${s.series_id}-edit`}>
                  <td colSpan={6} style={{ padding: 0 }}>
                    <div className="inline-edit">
                      <div className="inline-edit-grid">
                        <label>系列名<input value={seriesForm.canonical_title || ''} onChange={e => setSeriesForm(f => ({ ...f, canonical_title: e.target.value }))} /></label>
                        <label>系列键<input value={seriesForm.series_key || ''} onChange={e => setSeriesForm(f => ({ ...f, series_key: e.target.value }))} /></label>
                        <label>作者<input value={seriesForm.author_guess || ''} onChange={e => setSeriesForm(f => ({ ...f, author_guess: e.target.value }))} /></label>
                      </div>
                      <div className="inline-edit-actions">
                        <button className="btn-primary" onClick={proposeSaveSeries}>保存</button>
                        <button className="btn-subtle" onClick={() => setEditingSeries(null)}>取消</button>
                      </div>
                    </div>
                  </td>
                </tr>,
              ] : []),
            ])}
          </tbody>
        </table></div>
      )}

      {/* Confirmation Dialog */}
      {confirm && (
        <div className="confirm-overlay" onClick={() => setConfirm(null)}>
          <div className="confirm-dialog" onClick={e => e.stopPropagation()}>
            <h2 style={{ margin: '0 0 16px', fontSize: 15, color: 'var(--text-primary)', textTransform: 'none', letterSpacing: 0 }}>{confirm.label}</h2>
            <div className="confirm-compare">
              <div className="confirm-col">
                <div className="confirm-col-header">修改前</div>
                {Object.entries(confirm.before).map(([k, v]) => (
                  <div key={k} className="confirm-row"><span className="confirm-key">{k}</span><span className="confirm-val">{v || '-'}</span></div>
                ))}
              </div>
              <div className="confirm-arrow">→</div>
              <div className="confirm-col">
                <div className="confirm-col-header">修改后</div>
                {Object.entries(confirm.after).map(([k, v]) => (
                  <div key={k} className="confirm-row"><span className="confirm-key">{k}</span><span className="confirm-val">{v || '-'}</span></div>
                ))}
              </div>
            </div>
            <div className="confirm-actions">
              {confirmError && <div style={{ color: 'var(--status-error)', fontSize: 12, marginRight: 'auto' }}>错误: {confirmError}</div>}
              <button className="btn-primary" disabled={loading[confirm.type]} onClick={executeConfirm}>确认执行</button>
              <button className="btn-subtle" onClick={() => { setConfirm(null); setConfirmError(null) }}>取消</button>
            </div>
          </div>
        </div>
      )}
    </>
  )
}
