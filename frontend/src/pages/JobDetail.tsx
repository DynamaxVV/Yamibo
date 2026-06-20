import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, type JobSummary, type JobEvent } from '../api/client'
import { Badge } from '../components/Badge'

export function JobDetail() {
  const id = window.location.pathname.split('/').pop() || ''
  const [job, setJob] = useState<JobSummary | null>(null)
  const [events, setEvents] = useState<JobEvent[]>([])
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.job(id).then(setJob).catch(e => setError(e.message))
    api.jobEvents(id).then(setEvents).catch(() => {})
  }, [id])

  if (error) return <div className="panel" style={{ color: 'var(--status-error)' }}>{error}</div>
  if (!job) return <div className="panel" style={{ color: 'var(--text-tertiary)' }}>Loading...</div>

  const rows = [
    ['ID', <span className="mono">{job.job_id}</span>],
    ['类型', job.job_type],
    ['状态', <Badge status={job.status} />],
    ['阶段', job.stage || '-'],
    ['TID', job.tid ? <Link to={`/threads/${job.tid}`}>{job.tid}</Link> : '-'],
    ['进度', `${job.progress_current}/${job.progress_total ?? '?'}`],
    ['Worker', job.worker_id || '-'],
    ['错误', `${job.error_code || ''} ${job.error_message || ''}`.trim() || '-'],
    ['创建时间', job.created_at],
    ['更新时间', job.updated_at],
    ['完成时间', job.finished_at || '-'],
  ]

  return (
    <>
      <h2>任务详情</h2>
      <div className="table-wrap"><table>
        <tbody>{rows.map(([k, v], i) => <tr key={i}><th style={{ width: 120 }}>{k}</th><td>{v}</td></tr>)}</tbody>
      </table></div>

      {events.length > 0 && (
        <>
          <h2>事件时间线</h2>
          <div className="table-wrap"><table>
            <thead><tr><th>ID</th><th>时间</th><th>类型</th><th>状态</th><th>阶段</th></tr></thead>
            <tbody>
              {events.map(e => (
                <tr key={e.event_id}>
                  <td className="mono">{e.event_id}</td>
                  <td className="nowrap">{e.created_at}</td>
                  <td className="nowrap">{e.event_type}</td>
                  <td><Badge status={e.status} /></td>
                  <td className="nowrap">{e.stage || '-'}</td>
                </tr>
              ))}
            </tbody>
          </table></div>
        </>
      )}
    </>
  )
}
