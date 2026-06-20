import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, type JobSummary, type JobEvent } from '../api/client'
import { Badge } from '../components/Badge'
import { useI18n } from '../context/I18nContext'
import { formatDateTime } from '../utils/time'

export function JobDetail() {
  const { t } = useI18n()
  const id = window.location.pathname.split('/').pop() || ''
  const [job, setJob] = useState<JobSummary | null>(null)
  const [events, setEvents] = useState<JobEvent[]>([])
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.job(id).then(setJob).catch(e => setError(e.message))
    api.jobEvents(id).then(setEvents).catch(() => {})
  }, [id])

  if (error) return <div className="panel" style={{ color: 'var(--status-error)' }}>{error}</div>
  if (!job) return <div className="panel" style={{ color: 'var(--text-tertiary)' }}>{t('loading')}</div>

  const rows = [
    ['ID', <span className="mono">{job.job_id}</span>],
    [t('type'), job.job_type],
    [t('status'), <Badge status={job.status} />],
    [t('stage'), job.stage || '-'],
    [t('tid'), job.tid ? <Link to={`/threads/${job.tid}`}>{job.tid}</Link> : '-'],
    [t('progress'), `${job.progress_current}/${job.progress_total ?? '?'}`],
    [t('worker'), job.worker_id || '-'],
    [t('error'), `${job.error_code || ''} ${job.error_message || ''}`.trim() || '-'],
    [t('created_at'), formatDateTime(job.created_at)],
    [t('updated'), formatDateTime(job.updated_at)],
    [t('finished_at'), formatDateTime(job.finished_at)],
  ]

  return (
    <>
      <h2>{t('job_detail')}</h2>
      <div className="table-wrap"><table>
        <tbody>{rows.map(([k, v], i) => <tr key={i}><th style={{ width: 120 }}>{k}</th><td style={{ textAlign: 'left' }}>{v}</td></tr>)}</tbody>
      </table></div>

      {events.length > 0 && (
        <>
          <h2>{t('event_timeline')}</h2>
          <div className="table-wrap"><table>
            <thead><tr><th>{t('id')}</th><th>{t('time')}</th><th>{t('event_type')}</th><th>{t('status')}</th><th>{t('stage')}</th></tr></thead>
            <tbody>
              {events.map(e => (
                <tr key={e.event_id}>
                  <td className="mono">{e.event_id}</td>
                  <td className="nowrap">{formatDateTime(e.created_at)}</td>
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
