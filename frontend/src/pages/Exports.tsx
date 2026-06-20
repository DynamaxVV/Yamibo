import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api, type ThreadSummary, type SeriesSummary } from '../api/client'
import { Badge } from '../components/Badge'

export function Exports() {
  const [exports, setExports] = useState<ThreadSummary[]>([])
  useEffect(() => { api.exports().then(setExports) }, [])

  return (
    <div className="table-wrap"><table>
      <thead><tr><th>TID</th><th>标题</th><th>归档状态</th><th>导出路径</th></tr></thead>
      <tbody>
        {exports.map(t => (
          <tr key={t.tid}>
            <td className="mono"><Link to={`/threads/${t.tid}`}>{t.tid}</Link></td>
            <td className="truncate"><Link to={`/threads/${t.tid}`}>{t.display_title || t.raw_title}</Link></td>
            <td><Badge status={t.archive_status} /></td>
            <td className="truncate">{t.export_path || '-'}</td>
          </tr>
        ))}
      </tbody>
    </table></div>
  )
}

export function Series() {
  const [series, setSeries] = useState<SeriesSummary[]>([])
  useEffect(() => { api.series().then(setSeries) }, [])

  return (
    <div className="table-wrap"><table>
      <thead><tr><th>ID</th><th>标题</th><th>作者</th><th>系列键</th><th>贴子数</th><th>复核</th></tr></thead>
      <tbody>
        {series.map(s => (
          <tr key={s.series_id}>
            <td className="mono"><Link to={`/series/${s.series_id}`}>{s.series_id}</Link></td>
            <td className="truncate"><Link to={`/series/${s.series_id}`}>{s.canonical_title || s.series_key}</Link></td>
            <td>{s.author_guess || '-'}</td>
            <td className="mono">{s.series_key || '-'}</td>
            <td>{s.thread_count}</td>
            <td><Badge status={s.needs_review ? 'partial' : 'succeeded'}>{s.needs_review ? '需复核' : '已确认'}</Badge></td>
          </tr>
        ))}
      </tbody>
    </table></div>
  )
}

export function SeriesDetail() {
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
  if (!data) return <div className="panel" style={{ color: 'var(--text-tertiary)' }}>Loading...</div>

  const isEmpty = data.threads.length === 0

  return (
    <>
      <div className="panel">
        <div className="row-actions">
          <Link to="/series" className="btn-subtle">← 系列列表</Link>
          {confirmDelete ? (
            <>
              <button className="btn-danger" disabled={loading === 'delete' || !isEmpty} onClick={handleDelete}>
                {isEmpty ? '确认删除？' : '无法删除（非空系列）'}
              </button>
              <button className="btn-subtle" onClick={() => setConfirmDelete(false)}>取消</button>
            </>
          ) : (
            <button className="btn-danger-outline" disabled={!isEmpty} onClick={() => setConfirmDelete(true)}>
              删除系列
            </button>
          )}
          {!isEmpty && <span style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>仅空系列可删除</span>}
        </div>
      </div>

      <h2>系列信息</h2>
      <div className="table-wrap"><table>
        <tbody>
          {([
            ['ID', String(data.series.series_id)],
            ['系列键', data.series.series_key || '-'],
            ['作者', data.series.author_guess || '-'],
          ] as [string, string][]).map(([k, v], i) => <tr key={i}><th style={{ width: 100 }}>{k}</th><td>{v}</td></tr>)}
        </tbody>
      </table></div>

      <h2>贴子</h2>
      <div className="table-wrap"><table>
        <thead><tr><th>TID</th><th>标题</th><th>章节</th><th>归档</th></tr></thead>
        <tbody>
          {data.threads.map(t => (
            <tr key={t.tid}>
              <td className="mono"><Link to={`/threads/${t.tid}`} state={{ from: 'series' }}>{t.tid}</Link></td>
              <td className="truncate"><Link to={`/threads/${t.tid}`} state={{ from: 'series' }}>{t.display_title || t.raw_title}</Link></td>
              <td>{t.chapter_name || '-'}</td>
              <td><Badge status={t.archive_status} /></td>
            </tr>
          ))}
        </tbody>
      </table></div>
    </>
  )
}
