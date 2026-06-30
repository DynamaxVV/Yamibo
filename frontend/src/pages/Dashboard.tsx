import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, type DashboardData, type ThreadSummary } from '../api/client'
import { Badge } from '../components/Badge'
import { useI18n } from '../context/I18nContext'
import { formatDateTime } from '../utils/time'

function RecentTable({ threads, forumNames, t, liveStatuses }: { threads: ThreadSummary[]; forumNames: Record<number, string>; t: (k: string) => string; liveStatuses: Record<number, string> }) {
  if (threads.length === 0) return <div className="panel" style={{ color: 'var(--text-tertiary)', padding: '8px 12px', fontSize: 12 }}>{t('no_data')}</div>
  return (
      <div className="table-wrap"><table className="dashboard-recent-table">
        <thead><tr><th className="col-tid">{t('tid')}</th><th className="col-title">{t('title')}</th><th className="col-forum">{t('forum')}</th><th className="col-status">{t('archive_status')}</th><th className="col-pub-time">{t('pub_time')}</th><th className="col-sync-time">{t('sync_time')}</th></tr></thead>
        <tbody>
          {threads.map(t_ => (
            (() => {
              const liveStatus = liveStatuses[t_.tid]
              const status = liveStatus || t_.archive_status
              return (
            <tr key={t_.tid}>
              <td className="mono col-tid"><Link to={`/threads/${t_.tid}`}>{t_.tid}</Link></td>
              <td className="truncate col-title" title={t_.display_title || t_.raw_title}><Link to={`/threads/${t_.tid}`}>{t_.display_title || t_.raw_title}</Link></td>
              <td className="col-forum">{forumNames[t_.forum_id ?? 0] || '-'}</td>
              <td className="col-status"><Badge status={status} /></td>
              <td className="dashboard-time col-pub-time">{formatDateTime(t_.pub_time)}</td>
              <td className="dashboard-time col-sync-time">{formatDateTime(t_.sync_time)}</td>
            </tr>
              )
            })()
          ))}
        </tbody>
      </table></div>
  )
}

function LimitSelect({ value, onChange, t }: { value: number; onChange: (n: number) => void; t: (k: string, p?: Record<string, string | number>) => string }) {
  return (
    <select value={value} onChange={e => onChange(Number(e.target.value))}
      style={{ fontSize: 11, padding: '1px 4px', border: '1px solid var(--border-light)', borderRadius: 'var(--radius-sm)', background: 'var(--bg-card)', color: 'var(--text-primary)' }}>
      <option value={10}>{t('recent_n', { n: 10 })}</option>
      <option value={25}>{t('recent_n', { n: 25 })}</option>
      <option value={50}>{t('recent_n', { n: 50 })}</option>
    </select>
  )
}

function SectionHeader({ title, limit, onLimitChange, t }: { title: string; limit: number; onLimitChange: (n: number) => void; t: (k: string, p?: Record<string, string | number>) => string }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', margin: '16px 0 6px' }}>
      <h3 style={{ margin: 0, fontSize: 13, color: 'var(--text-secondary)' }}>{title}</h3>
      <LimitSelect value={limit} onChange={onLimitChange} t={t} />
    </div>
  )
}

export function Dashboard() {
  const { t, lang } = useI18n()
  const desc = (j: { description: string; description_en: string }) => lang === 'en' ? j.description_en : j.description
  const auditDesc = (a: { description: string; description_en: string }) => lang === 'en' ? a.description_en : a.description
  const [data, setData] = useState<DashboardData | null>(null)
  const [primaryThreads, setPrimaryThreads] = useState<ThreadSummary[]>([])
  const [otherThreads, setOtherThreads] = useState<ThreadSummary[]>([])
  const [error, setError] = useState<string | null>(null)
  const [forumNames, setForumNames] = useState<Record<number, string>>({})
  const [primaryLimit, setPrimaryLimit] = useState(10)
  const [otherLimit, setOtherLimit] = useState(10)
  const [dashboardTick, setDashboardTick] = useState(0)
  const [resumeBusy, setResumeBusy] = useState(false)
  const [jobControlBusy, setJobControlBusy] = useState(false)

  useEffect(() => {
    api.forums().then(fs => {
      const m: Record<number, string> = {}
      fs.forEach(f => { m[f.forum_id] = lang === 'en' ? (f.name_en || f.name) : f.name })
      setForumNames(m)
    }).catch(() => {})
  }, [lang])

  useEffect(() => {
    let active = true
    Promise.all([
      api.dashboard(Math.max(primaryLimit, otherLimit)),
      api.threads({ forum_id: 30, page_size: primaryLimit }).then(res => res.items),
      api.threads({ forum_id: 55, page_size: primaryLimit }).then(res => res.items),
      api.threads({ forum_id: 33, page_size: otherLimit }).then(res => res.items),
      api.threads({ forum_id: 5, page_size: otherLimit }).then(res => res.items),
    ]).then(([d, comic, novel, sea, anime]) => {
      if (!active) return
      setData(d)
      setPrimaryThreads([...comic, ...novel].sort((a, b) => (b.sync_time || '').localeCompare(a.sync_time || '')).slice(0, primaryLimit))
      setOtherThreads([...sea, ...anime].sort((a, b) => (b.sync_time || '').localeCompare(a.sync_time || '')).slice(0, otherLimit))
    }).catch(e => setError(e.message))
    const timer = window.setInterval(() => setDashboardTick(t => t + 1), 5000)
    return () => {
      active = false
      window.clearInterval(timer)
    }
  }, [primaryLimit, otherLimit])

  useEffect(() => {
    let active = true
    api.dashboard(Math.max(primaryLimit, otherLimit)).then(d => {
      if (active) setData(d)
    }).catch(e => setError(e.message))
    return () => { active = false }
  }, [primaryLimit, otherLimit, dashboardTick])

  const liveStatuses = data?.live_thread_statuses ?? {}
  const jobControl = data?.job_control ?? { jobs_enabled: true, queued: 0, running: 0, retrying: 0, interrupted: 0, paused: 0 }
  const daemonRunning = jobControl.jobs_enabled
  const canPauseJobs = daemonRunning
  const canResumeJobs = !daemonRunning
  const jobControlAction = canPauseJobs ? 'pause' : 'resume'
  const runJobControl = () => {
    if (!canPauseJobs && !canResumeJobs) return
    setJobControlBusy(true)
    api.controlJobs(jobControlAction)
      .then(result => {
        setError(null)
        setData(prev => prev ? { ...prev, job_control: result.job_control } : prev)
        setDashboardTick(tick => tick + 1)
      })
      .catch(e => setError(e.message))
      .finally(() => setJobControlBusy(false))
  }

  if (error) return <div className="panel" style={{ color: 'var(--status-error)' }}>{error}</div>
  if (!data) return <div className="panel" style={{ color: 'var(--text-tertiary)' }}>{t('loading')}</div>

  return (
    <>
      {data.remote_access_pause?.active && (
        <div className="alert-banner" role="alert">
          <div className="alert-banner-copy">
            <strong>{t('remote_access_paused_title')}</strong>
            <p>{t('remote_access_paused_desc')}</p>
            <p className="alert-banner-meta">
              {t('remote_access_paused_meta', {
                time: formatDateTime(data.remote_access_pause.triggered_at),
                source: data.remote_access_pause.source,
                count: data.remote_access_pause.paused_job_count,
              })}
            </p>
          </div>
          <button
            className="btn-danger"
            disabled={resumeBusy}
            onClick={() => {
              setResumeBusy(true)
              api.resumeRemoteAccess()
                .then(() => {
                  setError(null)
                  setDashboardTick(tick => tick + 1)
                })
                .catch(e => setError(e.message))
                .finally(() => setResumeBusy(false))
            }}
          >
            {t('remote_access_resume')}
          </button>
        </div>
      )}
      <div className="stat-row">
        <div className="stat-cell">
          <div className="label">{t('thread_count')}</div>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 12, flexWrap: 'wrap' }}>
            <div className="value">{data.thread_count}</div>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              {Object.entries(data.forum_counts).map(([fid, cnt]) => (
                <span key={fid} style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>
                  {forumNames[Number(fid)] || fid} <b style={{ color: 'var(--text-secondary)' }}>{cnt}</b>
                </span>
              ))}
            </div>
          </div>
        </div>
        <div className="stat-cell"><div className="label">{t('series_count')}</div><div className="value">{data.series_count}</div></div>
        <div className="stat-cell"><div className="label">{t('export_count')}</div><div className="value">{data.export_count}</div></div>
        <div className="stat-cell">
          <div className="label">{t('job_control')}</div>
          <div className="job-control-cell">
            <div className="job-control-counts">
              <span>{t('queued')} <b>{jobControl.queued}</b></span>
              <span>{t('running')} <b>{jobControl.running + jobControl.retrying}</b></span>
              <span>{t('paused')} <b>{jobControl.paused}</b></span>
            </div>
            <button
              className={canPauseJobs ? 'btn-danger' : 'btn-subtle'}
              disabled={jobControlBusy || (!canPauseJobs && !canResumeJobs)}
              onClick={runJobControl}
            >
              {jobControlBusy ? t('running') : (canPauseJobs ? t('pause_active_jobs') : t('resume_paused_jobs'))}
            </button>
          </div>
        </div>
      </div>

      <h2 style={{ margin: '16px 0 8px' }}>{t('recent_threads')}</h2>

      <SectionHeader title={t('comic_novel')} limit={primaryLimit} onLimitChange={setPrimaryLimit} t={t} />
      <RecentTable threads={primaryThreads} forumNames={forumNames} t={t} liveStatuses={liveStatuses} />

      <SectionHeader title={t('other_forums')} limit={otherLimit} onLimitChange={setOtherLimit} t={t} />
      <RecentTable threads={otherThreads} forumNames={forumNames} t={t} liveStatuses={liveStatuses} />

      <h2>{t('recent_jobs')}</h2>
      <div className="table-wrap"><table>
        <thead><tr><th style={{ width: 75 }}>{t('tid')}</th><th>{t('description')}</th><th>{t('status')}</th><th style={{ width: 65 }}>{t('progress')}</th><th>{t('updated')}</th></tr></thead>
        <tbody>
          {data.recent_jobs.map(j => (
            <tr key={j.job_id}>
              <td>{j.tid ? <Link to={`/threads/${j.tid}`}>{j.tid}</Link> : '-'}</td>
              <td className="truncate" title={desc(j)}><Link to={`/jobs/${j.job_id}`}>{desc(j)}</Link></td>
              <td><Badge status={j.status} /></td>
              <td className="nowrap">{j.progress_current}/{j.progress_total ?? '?'}</td>
              <td className="nowrap">{formatDateTime(j.updated_at)}</td>
            </tr>
          ))}
        </tbody>
      </table></div>

      {data.workers.length > 0 && (
        <>
          <h2>{t('worker_heartbeats')}</h2>
          <div className="table-wrap"><table>
            <thead><tr><th>{t('worker')}</th><th>{t('running_jobs')}</th><th>{t('seen_jobs')}</th><th>{t('last_heartbeat')}</th></tr></thead>
            <tbody>
              {data.workers.map(w => (
                <tr key={w.worker_id}>
                  <td className="mono">{w.worker_id}</td>
                  <td>{w.running_jobs}</td>
                  <td>{w.seen_jobs}</td>
                  <td className="nowrap">{formatDateTime(w.latest_heartbeat_at)}</td>
                </tr>
              ))}
            </tbody>
          </table></div>
        </>
      )}

      <h2>{t('audit_events')}</h2>
      <div className="table-wrap"><table>
        <thead><tr><th>{t('description')}</th><th>{t('actor')}</th><th>{t('time')}</th></tr></thead>
        <tbody>
          {data.recent_audits.map(a => (
            <tr key={a.event_id}>
              <td title={auditDesc(a)}>{auditDesc(a)}</td>
              <td>{a.actor}</td>
              <td className="nowrap">{formatDateTime(a.created_at)}</td>
            </tr>
          ))}
        </tbody>
      </table></div>
    </>
  )
}
