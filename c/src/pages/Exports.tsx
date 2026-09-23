import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api, type ThreadSummary, type SeriesSummary } from '../api/client'
import { Badge } from '../components/Badge'
import { LoadingIndicator } from '../components/LoadingIndicator'
import { useI18n } from '../context/I18nContext'
import { formatThreadListTitle } from '../utils/threadTitle'

export function Exports() {
  const { t, tx } = useI18n()
  const [exports, setExports] = useState<ThreadSummary[]>([])
  const [error, setError] = useState(false)
  const [loaded, setLoaded] = useState(false)
  const [copiedTid, setCopiedTid] = useState<number | null>(null)
  const [page, setPage] = useState(1)
  const pageSize = 25
  const visible = exports.slice((page - 1) * pageSize, page * pageSize)
  const totalPages = Math.max(1, Math.ceil(exports.length / pageSize))
  useEffect(() => { api.exports().then(setExports).catch(() => setError(true)).finally(() => setLoaded(true)) }, [])
  const copyPath = async (item: ThreadSummary) => {
    if (!item.export_path) return
    try { await navigator.clipboard.writeText(item.export_path); setCopiedTid(item.tid) }
    catch { setCopiedTid(null) }
  }

  return (
    <div>
      <header className="mb-4 border-b border-border pb-3"><h1 className="text-3xl">{tx('导出中心', 'Exports')}</h1><p className="mt-1 text-sm text-muted-foreground">{tx('查看已生成的档案及其保存位置。', 'Browse generated archives and their storage locations.')}</p></header>
      {error && <p role="alert" className="panel">{tx('导出列表加载失败，请刷新后重试。', 'Unable to load exports. Refresh and try again.')}</p>}
      {!error && !visible.length && (loaded ? <p className="panel text-sm text-muted-foreground">{tx('还没有导出档案。可从帖子详情创建导出任务。', 'No exports yet. Create an export task from a thread page.')}</p> : <LoadingIndicator label={tx('正在读取导出档案…', 'Loading exports…')} />)}
      <div className="space-y-2 md:hidden">
        {visible.map(item => <article key={item.tid} className="rounded border border-border bg-card p-4">
          <Link to={`/threads/${item.tid}`} className="block break-words text-sm font-light leading-6">{formatThreadListTitle(item)}</Link>
          <div className="mt-2 flex items-center gap-2 text-xs text-muted-foreground"><span>#{item.tid}</span><Badge status={item.archive_status} /></div>
          <div className="mt-2 break-all text-xs text-muted-foreground">{item.export_path?.split(/[\\/]/).pop() || tx('路径不可用', 'Path unavailable')}</div>
          {item.export_path && <details className="mt-2 text-xs"><summary className="cursor-pointer text-primary">{tx('查看完整路径', 'Show full path')}</summary><code className="mt-1 block break-all">{item.export_path}</code><button type="button" onClick={() => void copyPath(item)} className="mt-2 min-h-11 rounded border border-border px-3">{copiedTid === item.tid ? tx('已复制', 'Copied') : tx('复制路径', 'Copy path')}</button></details>}
        </article>)}
      </div>
      <div className="table-wrap hidden md:block"><table className="min-w-[780px]">
      <thead><tr><th>{t('tid')}</th><th>{t('title')}</th><th>{t('archive_status')}</th><th>{t('export_path')}</th></tr></thead>
      <tbody>
        {visible.map(t_ => (
          (() => {
            const titleText = formatThreadListTitle(t_)
            return (
          <tr key={t_.tid}>
            <td className="mono"><Link to={`/threads/${t_.tid}`}>{t_.tid}</Link></td>
            <td className="table-cell-long min-w-80"><Link className="line-clamp-2 break-words leading-5" to={`/threads/${t_.tid}`}>{titleText}</Link></td>
            <td><Badge status={t_.archive_status} /></td>
            <td className="table-cell-long"><details><summary className="cursor-pointer break-all">{t_.export_path?.split(/[\\/]/).pop() || tx('路径不可用', 'Path unavailable')}</summary><code className="block max-w-80 break-all pt-2 text-xs">{t_.export_path || '-'}</code>{t_.export_path && <button type="button" onClick={() => void copyPath(t_)} className="mt-2 block rounded border border-border px-2 py-1 text-xs">{copiedTid === t_.tid ? tx('已复制', 'Copied') : tx('复制路径', 'Copy path')}</button>}</details></td>
          </tr>
            )
          })()
        ))}
      </tbody>
    </table></div>
      <div className="flex flex-wrap items-center justify-between gap-3 text-sm text-muted-foreground"><span>{tx(`共 ${exports.length} 个档案`, `${exports.length} exports`)}</span><div className="flex items-center gap-2"><button type="button" disabled={page <= 1} onClick={() => setPage(page - 1)} className="rounded border border-border px-3 py-2 disabled:opacity-50">{tx('上一页', 'Previous')}</button><span>{page} / {totalPages}</span><button type="button" disabled={page >= totalPages} onClick={() => setPage(page + 1)} className="rounded border border-border px-3 py-2 disabled:opacity-50">{tx('下一页', 'Next')}</button></div></div>
    </div>
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
              <td className="table-cell-long truncate" title={s.canonical_title || s.series_key || ''}><Link to={`/series/${s.series_id}`}>{s.canonical_title || s.series_key}</Link></td>
              <td className="table-cell-long">{s.author_guess || '-'}</td>
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
  const { t, lang, tx } = useI18n()
  const id = parseInt(window.location.pathname.split('/').pop() || '0')
  const navigate = useNavigate()
  const [data, setData] = useState<{ series: SeriesSummary; threads: ThreadSummary[] } | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
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
    setActionError(null)
    if (!confirmDelete) { setConfirmDelete(true); return }
    setLoading('delete')
    try {
      await api.deleteSeries(id)
      navigate('/series')
    } catch (e) {
      setActionError(e instanceof Error ? e.message : String(e))
    }
    setLoading(null)
    setConfirmDelete(false)
  }

  const handleMerge = async () => {
    setActionError(null)
    if (!mergeTargetId.trim()) {
      setActionError(t('target_id'))
      return
    }
    const targetId = parseInt(mergeTargetId, 10)
    if (isNaN(targetId)) {
      setActionError(tx(`${t('target_id')} 无效`, `${t('target_id')} is invalid`))
      return
    }
    if (targetId === id) {
      setActionError(tx('源系列和目标系列必须不同', 'The source and target series must be different.'))
      return
    }
    setLoading('merge')
    try {
      const detail = await api.seriesDetail(targetId)
      setMergeTargetInfo(detail.series)
      setConfirmMerge(true)
    } catch (e) {
      setMergeTargetInfo(null)
      setConfirmMerge(false)
      setActionError(e instanceof Error ? e.message : String(e))
    }
    setLoading(null)
  }

  const executeMerge = async () => {
    setActionError(null)
    const targetId = parseInt(mergeTargetId, 10)
    if (isNaN(targetId)) {
      setActionError(tx(`${t('target_id')} 无效`, `${t('target_id')} is invalid`))
      return
    }
    setLoading('merge')
    try {
      await api.mergeSeries(id, targetId)
      navigate(`/series/${targetId}`)
    } catch (e) {
      setActionError(e instanceof Error ? e.message : String(e))
    }
    setLoading(null)
    setConfirmMerge(false)
  }

  const handleEdit = () => {
    setActionError(null)
    setEditForm({
      series_key: data?.series.series_key || '',
      author_guess: data?.series.author_guess || '',
    })
    setEditing(true)
  }

  const handleSaveEdit = async () => {
    setLoading('edit')
    setActionError(null)
    try {
      await api.updateSeries({ series_id: id, ...editForm })
      const refreshed = await api.seriesDetail(id)
      setData(refreshed)
      setEditing(false)
    } catch (e) {
      setActionError(e instanceof Error ? e.message : String(e))
    }
    setLoading(null)
  }

  if (error) return <div className="panel" style={{ color: 'var(--status-error)' }}>{error}</div>
  if (!data) return <LoadingIndicator label={t('loading')} />

  const isEmpty = data.threads.length === 0

  return (
    <>
      <Link to="/series" className="btn-subtle mb-2">← {t('series_list')}</Link>
      <header className="mb-3 space-y-1"><h1 className="text-2xl break-words">{data.series.canonical_title || (lang === 'en' ? 'Unnamed series' : '未命名系列')}</h1><p className="text-sm text-muted-foreground">{data.series.author_guess || (lang === 'en' ? 'Author unknown' : '作者待补充')} · {data.threads.length} {lang === 'en' ? 'threads' : '篇帖子'} · #{data.series.series_id}</p><Badge status={data.series.needs_review ? 'warn' : 'ok'}>{data.series.needs_review ? t('needs_review') : t('confirmed')}</Badge></header>

      <details className="panel"><summary className="cursor-pointer font-medium">{t('series_info')}</summary>
      <h2 style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        {t('series_info')}
        {!editing && (
          <button onClick={handleEdit} style={{ background: 'none', border: 'none', cursor: 'pointer', padding: 0, fontSize: 13, lineHeight: 1 }} title={t('edit')}>
            {t('edit')}
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

      </details>
      <h2>{t('threads')}</h2>
      <ol className="space-y-1.5">
        {data.threads.map(thread => <li key={thread.tid} className="px-3 py-2 bg-card border border-border rounded-sm">
          <Link className="block font-sans text-[15px] font-light leading-5 break-words hover:underline" to={`/threads/${thread.tid}`} state={{ from: 'series', seriesId: id }}>{formatThreadListTitle(thread)}</Link>
          <div className="flex flex-wrap items-center gap-2 mt-1 text-xs text-muted-foreground"><span>#{thread.tid}</span><Badge status={thread.archive_status} /></div>
        </li>)}
      </ol>
      {data.threads.length === 0 && <p className="panel text-sm text-muted-foreground">{lang === 'en' ? 'No threads in this series yet.' : '此系列暂时没有帖子。'}</p>}

      <details className="panel mt-5"><summary className="cursor-pointer font-medium">{tx('系列管理 · 合并与删除', 'Series management · Merge or delete')}</summary>
        <div className="row-actions mt-4">
          <Link to="/series" className="btn-subtle">← {t('series_list')}</Link>
          <div className="input-btn-group">
            <input aria-label={tx('合并到系列 ID', 'Target series ID')} placeholder={tx('合并到系列 ID', 'Target series ID')} value={mergeTargetId}
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
        {actionError && <div className="inline-edit-error" style={{ marginTop: 10 }}>{actionError}</div>}
      </details>

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
