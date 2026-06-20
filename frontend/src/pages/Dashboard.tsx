import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, type DashboardData } from '../api/client'
import { Badge } from '../components/Badge'

export function Dashboard() {
  const [data, setData] = useState<DashboardData | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.dashboard().then(setData).catch(e => setError(e.message))
  }, [])

  if (error) return <div className="panel" style={{ color: 'var(--status-error)' }}>{error}</div>
  if (!data) return <div className="panel" style={{ color: 'var(--text-tertiary)' }}>Loading...</div>

  return (
    <>
      <div className="stat-row">
        <div className="stat-cell"><div className="label">贴子数</div><div className="value">{data.thread_count}</div></div>
        <div className="stat-cell"><div className="label">系列数</div><div className="value">{data.series_count}</div></div>
        <div className="stat-cell"><div className="label">导出数</div><div className="value">{data.export_count}</div></div>
      </div>

      <h2>最近任务</h2>
      <div className="table-wrap"><table>
        <thead><tr><th>ID</th><th>类型</th><th>状态</th><th>阶段</th><th>TID</th><th>更新时间</th></tr></thead>
        <tbody>
          {data.recent_jobs.map(j => (
            <tr key={j.job_id}>
              <td className="mono"><Link to={`/jobs/${j.job_id}`}>{j.job_id}</Link></td>
              <td className="nowrap">{j.job_type}</td>
              <td><Badge status={j.status} /></td>
              <td className="nowrap">{j.stage || '-'}</td>
              <td>{j.tid ? <Link to={`/threads/${j.tid}`}>{j.tid}</Link> : '-'}</td>
              <td className="nowrap">{j.updated_at}</td>
            </tr>
          ))}
        </tbody>
      </table></div>

      {data.workers.length > 0 && (
        <>
          <h2>Worker 心跳</h2>
          <div className="table-wrap"><table>
            <thead><tr><th>Worker</th><th>运行中</th><th>已处理</th><th>最近心跳</th></tr></thead>
            <tbody>
              {data.workers.map(w => (
                <tr key={w.worker_id}>
                  <td className="mono">{w.worker_id}</td>
                  <td>{w.running_jobs}</td>
                  <td>{w.seen_jobs}</td>
                  <td className="nowrap">{w.latest_heartbeat_at || '-'}</td>
                </tr>
              ))}
            </tbody>
          </table></div>
        </>
      )}

      <h2>审计事件</h2>
      <div className="table-wrap"><table>
        <thead><tr><th>操作</th><th>目标</th><th>执行者</th><th>时间</th></tr></thead>
        <tbody>
          {data.recent_audits.map(a => (
            <tr key={a.event_id}>
              <td>{a.action}</td>
              <td>{a.target_type}:{a.target_id}</td>
              <td>{a.actor}</td>
              <td className="nowrap">{a.created_at}</td>
            </tr>
          ))}
        </tbody>
      </table></div>

      <h2>最近归档</h2>
      <div className="table-wrap"><table>
        <thead><tr><th>TID</th><th>标题</th><th>归档状态</th><th>同步时间</th></tr></thead>
        <tbody>
          {data.recent_threads.map(t => (
            <tr key={t.tid}>
              <td className="mono"><Link to={`/threads/${t.tid}`}>{t.tid}</Link></td>
              <td className="truncate"><Link to={`/threads/${t.tid}`}>{t.display_title || t.raw_title}</Link></td>
              <td><Badge status={t.archive_status} /></td>
              <td className="nowrap">{t.sync_time || '-'}</td>
            </tr>
          ))}
        </tbody>
      </table></div>
    </>
  )
}
