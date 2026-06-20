import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, type JobSummary } from '../api/client'
import { Badge } from '../components/Badge'

const STATUSES = [null, 'queued', 'running', 'succeeded', 'failed', 'interrupted'] as const

export function Jobs() {
  const [jobs, setJobs] = useState<JobSummary[]>([])
  const [status, setStatus] = useState<string | null>(null)

  useEffect(() => { api.jobs(status || undefined).then(setJobs) }, [status])

  return (
    <>
      <div className="segmented">
        {STATUSES.map(s => (
          <a key={s || 'all'} className={status === s ? 'active' : ''}
            href="#" onClick={e => { e.preventDefault(); setStatus(s) }}>
            {s || '全部'}
          </a>
        ))}
      </div>
      <div className="table-wrap"><table>
        <thead><tr><th>ID</th><th>类型</th><th>状态</th><th>阶段</th><th>TID</th><th>进度</th><th>错误</th><th>创建时间</th></tr></thead>
        <tbody>
          {jobs.map(j => (
            <tr key={j.job_id}>
              <td className="mono"><Link to={`/jobs/${j.job_id}`}>{j.job_id}</Link></td>
              <td className="nowrap">{j.job_type}</td>
              <td><Badge status={j.status} /></td>
              <td className="nowrap">{j.stage || '-'}</td>
              <td>{j.tid ? <Link to={`/threads/${j.tid}`}>{j.tid}</Link> : '-'}</td>
              <td className="nowrap">{j.progress_current}/{j.progress_total ?? '?'}</td>
              <td className="truncate">{j.error_code || '-'}</td>
              <td className="nowrap">{j.created_at}</td>
            </tr>
          ))}
        </tbody>
      </table></div>
    </>
  )
}
