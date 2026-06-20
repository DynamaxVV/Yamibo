import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, type JobSummary } from '../api/client'
import { Badge } from '../components/Badge'
import { useI18n } from '../context/I18nContext'
import { formatDateTime } from '../utils/time'

const STATUSES = [null, 'queued', 'running', 'succeeded', 'failed', 'interrupted'] as const

export function Jobs() {
  const { t } = useI18n()
  const [jobs, setJobs] = useState<JobSummary[]>([])
  const [status, setStatus] = useState<string | null>(null)

  useEffect(() => { api.jobs(status || undefined).then(setJobs) }, [status])

  return (
    <>
      <div className="segmented">
        {STATUSES.map(s => (
          <a key={s || 'all'} className={status === s ? 'active' : ''}
            href="#" onClick={e => { e.preventDefault(); setStatus(s) }}>
            {s ? t(s) : t('all')}
          </a>
        ))}
      </div>
      <div className="table-wrap"><table>
        <thead><tr><th>{t('id')}</th><th>{t('type')}</th><th>{t('status')}</th><th>{t('stage')}</th><th>{t('tid')}</th><th>{t('progress')}</th><th>{t('error')}</th><th>{t('created_at')}</th></tr></thead>
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
              <td className="nowrap">{formatDateTime(j.created_at)}</td>
            </tr>
          ))}
        </tbody>
      </table></div>
    </>
  )
}
