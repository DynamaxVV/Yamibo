import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api, type JobSummary, type JobEvent } from '../api/client'
import { Badge } from '../components/Badge'
import { useI18n } from '../context/I18nContext'
import { formatDateTime } from '../utils/time'
import { getArchiveBreakdown, getPartialArchiveReason, hasPartialArchiveBreakdown } from '../utils/archiveSummary'
import { formatJobErrorMessage, formatJobFailureKind, getJobFailureKind, renderBbsLinks } from '../utils/jobMessages'

export function JobDetail() {
  const { t, lang } = useI18n()
  const navigate = useNavigate()
  const desc = (j: { description: string; description_en: string }) => lang === 'en' ? j.description_en : j.description
  const id = window.location.pathname.split('/').pop() || ''
  const [job, setJob] = useState<JobSummary | null>(null)
  const [events, setEvents] = useState<JobEvent[]>([])
  const [error, setError] = useState<string | null>(null)
  const [refreshNotice, setRefreshNotice] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [rerunSubmitting, setRerunSubmitting] = useState(false)
  const jobRef = useRef<JobSummary | null>(null)

  useEffect(() => {
    api.job(id).then(next => {
      setJob(next)
      jobRef.current = next
    }).catch(e => setError(e.message))
    api.jobEvents(id).then(setEvents).catch(() => {})
  }, [id])

  useEffect(() => {
    let active = true
    const poll = window.setInterval(async () => {
      try {
        const next = await api.job(id)
        if (!active) return
        const prev = jobRef.current
        if (prev && (prev.status !== next.status || prev.stage !== next.stage || prev.updated_at !== next.updated_at)) {
          setRefreshNotice(`${t('job_status_updated')} ${t(next.status)}。`)
        }
        jobRef.current = next
        setJob(next)
      } catch { /* ignore */ }
    }, 4000)
    return () => {
      active = false
      window.clearInterval(poll)
    }
  }, [id, t])

  if (error) return <div className="panel" style={{ color: 'var(--status-error)' }}>{error}</div>
  if (!job) return <div className="panel" style={{ color: 'var(--text-tertiary)' }}>{t('loading')}</div>

  const isImageBackfill = job.job_type === 'image_backfill'
  const remoteImageCount = job.artifacts?.remote_image_count
  const applyNeedFetchCount = job.artifacts?.apply_need_fetch_count
  const rows = [
    [t('description'), <span style={{ fontSize: 14, fontWeight: 500 }}>{desc(job)}</span>],
    ['ID', <span className="mono" style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>{job.job_id}</span>],
    [t('status'), <Badge status={job.status} />],
    [t('stage'), job.stage || '-'],
    [t('paused_at'), formatDateTime(job.paused_at)],
    [t('original_url'), job.url ? <a href={job.url} target="_blank" rel="noreferrer">{job.url}</a> : '-'],
    [t('tid'), job.tid ? <Link to={`/threads/${job.tid}`}>{job.tid}</Link> : '-'],
    [lang === 'en' ? 'Failure type' : '失败类型', (() => {
      const kind = job.failure_kind || getJobFailureKind(job)
      return kind ? <span className="badge badge-muted">{formatJobFailureKind(kind, lang)}</span> : '-'
    })()],
    ...(isImageBackfill ? [
      [lang === 'en' ? 'Total remote images' : '图片总数', remoteImageCount == null ? '-' : String(remoteImageCount)],
      [lang === 'en' ? 'Images to backfill' : '待补图片', applyNeedFetchCount == null ? '-' : String(applyNeedFetchCount)],
    ] : []),
    [t('progress'), `${job.progress_current}/${job.progress_total ?? '?'}`],
    [t('worker'), job.worker_id || '-'],
    [t('error'), (() => {
      const text = formatJobErrorMessage(job.error_code, job.error_message, lang)
      return text === '-' ? '-' : <span style={{ textAlign: 'left' }}>{renderBbsLinks(text)}</span>
    })()],
    [t('created_at'), formatDateTime(job.created_at)],
    [t('updated'), formatDateTime(job.updated_at)],
    [t('finished_at'), formatDateTime(job.finished_at)],
  ]

  const payloadEntries = Object.entries(job.payload || {})
  const artifactEntries = Object.entries(job.artifacts || {})
  const failureContext = (job.artifacts?.failure_context as Record<string, unknown> | undefined) || null
  const remoteFetch = (failureContext?.remote_fetch as Record<string, unknown> | undefined) || null
  const failureKind = job.failure_kind || getJobFailureKind(job)
  const archiveBreakdown = getArchiveBreakdown(null, job.artifacts)
  const showPartialSummary = (job.status === 'partial' || job.artifacts?.archive_status === 'partial')
    && hasPartialArchiveBreakdown(archiveBreakdown)
  const downloadedCount = Number(job.artifacts?.downloaded_image_count || 0)
  const nonExportCount = Number(job.artifacts?.non_export_image_count || 0)
  const sharedCount = Number(job.artifacts?.shared_image_count || 0)
  const skippedCount = Number(job.artifacts?.skipped_image_count || 0)
  const missingCount = Number(job.artifacts?.missing_image_count || 0)
  const missingSharedCount = Number(job.artifacts?.missing_shared_image_count || 0)
  const canRerun = job.status === 'partial' || job.status === 'failed' || job.status === 'interrupted'
  const failureTitle = lang === 'en' ? 'Failure diagnostics' : '失败诊断'
  const eventPayloadTitle = lang === 'en' ? 'Event payload' : '事件负载'

  const renderValue = (value: unknown) => {
    if (value == null || value === '') return '-'
    if (typeof value === 'object') {
      return <pre style={{ margin: 0, whiteSpace: 'pre-wrap', wordBreak: 'break-word', fontSize: 12 }}>{renderBbsLinks(JSON.stringify(value, null, 2))}</pre>
    }
    if (typeof value === 'string') {
      return renderBbsLinks(formatJobErrorMessage(null, value, lang))
    }
    return String(value)
  }

  const handleRerun = async () => {
    setActionError(null)
    setRerunSubmitting(true)
    try {
      const result = await api.retryJob(job.job_id)
      const next = await api.job(result.job_id)
      setJob(next)
      jobRef.current = next
      navigate(`/jobs/${result.job_id}`)
    } catch (e: any) {
      setActionError(e.message || String(e))
    } finally {
      setRerunSubmitting(false)
    }
  }

  return (
    <>
      <div className="threads-filter-row" style={{ marginBottom: 12 }}>
        <h2 style={{ margin: 0 }}>{t('job_detail')}</h2>
        <div className="threads-filter-actions">
          <Link to="/jobs" className="btn-subtle">← {t('job_list')}</Link>
          {(canRerun || job.status === 'queued' || job.status === 'running' || job.status === 'retrying' || job.status === 'paused') && (
            <>
              {canRerun && (
                <button className="btn-subtle" onClick={() => void handleRerun()} disabled={rerunSubmitting}>
                  {rerunSubmitting ? t('running') : t('rerun')}
                </button>
              )}
              <button className="btn-subtle" onClick={async () => {
                setActionError(null)
                try {
                  if (job.status === 'paused') {
                    await api.resumeJob(job.job_id)
                  } else {
                    await api.pauseJob(job.job_id)
                  }
                  const next = await api.job(id)
                  setJob(next)
                  jobRef.current = next
                } catch (e: any) {
                  setActionError(e.message || String(e))
                }
              }}>
                {job.status === 'paused' ? t('resume') : t('pause')}
              </button>
            </>
          )}
        </div>
      </div>
      {actionError && <div className="panel" style={{ marginBottom: 12, color: 'var(--status-error)' }}>{actionError}</div>}
      <div className="table-wrap"><table>
        <tbody>{rows.map(([k, v], i) => <tr key={i}><th style={{ width: 120 }}>{k}</th><td style={{ textAlign: 'left' }}>{v}</td></tr>)}</tbody>
      </table></div>

      {failureContext && (
        <>
          <h2>{failureTitle}</h2>
          <div className="table-wrap"><table>
            <tbody>
              <tr><th style={{ width: 160 }}>{lang === 'en' ? 'failure_kind' : '失败类型'}</th><td style={{ textAlign: 'left' }}>{failureKind ? <span className="badge badge-muted">{formatJobFailureKind(failureKind, lang)}</span> : '-'}</td></tr>
              <tr><th style={{ width: 160 }}>exception_type</th><td style={{ textAlign: 'left' }}>{renderValue(failureContext.exception_type)}</td></tr>
              <tr><th style={{ width: 160 }}>message</th><td style={{ textAlign: 'left' }}>{renderValue(failureContext.message)}</td></tr>
              {remoteFetch && Object.entries(remoteFetch).map(([k, v]) => (
                <tr key={k}><th style={{ width: 160 }}>{k}</th><td style={{ textAlign: 'left' }}>{renderValue(v)}</td></tr>
              ))}
            </tbody>
          </table></div>
        </>
      )}

      {refreshNotice && (
        <div className="panel notice-panel">
          <div className="notice-panel-body">
            <span>{refreshNotice}</span>
            <div className="notice-actions">
              <button className="btn-subtle" onClick={() => setRefreshNotice(null)}>{t('dismiss')}</button>
              <button className="btn-primary" onClick={() => {
                setRefreshNotice(null)
                api.job(id).then(next => {
                  setJob(next)
                  jobRef.current = next
                }).catch(() => {})
                api.jobEvents(id).then(setEvents).catch(() => {})
              }}>{t('refresh_content')}</button>
            </div>
          </div>
        </div>
      )}

      {showPartialSummary && archiveBreakdown && (
        <details className="archive-summary-card" open>
          <summary className="archive-summary-title">
            <span>{t('archive_partial_detail')}</span>
            <span className="archive-summary-arrow">▾</span>
          </summary>
          <div className="archive-summary-grid">
            <div><span>{t('archive_success')}</span><strong>{downloadedCount + nonExportCount + sharedCount + skippedCount}</strong></div>
            <div><span>{t('archive_failed')}</span><strong>{missingCount + missingSharedCount}</strong></div>
            <div><span>{t('downloaded')}</span><strong>{downloadedCount}</strong></div>
            <div><span>{t('downloaded_shared')}</span><strong>{sharedCount}</strong></div>
            <div><span>{t('non_export')}</span><strong>{nonExportCount}</strong></div>
            <div><span>{t('skipped')}</span><strong>{skippedCount}</strong></div>
          </div>
          <p className="archive-summary-reason">{getPartialArchiveReason(archiveBreakdown)}</p>
          <div className="archive-summary-list-group">
            {archiveBreakdown.missing_image_urls?.length ? (
              <div className="archive-summary-list">
                <span>{t('missing_image_urls')}</span>
                <ul>
                  {archiveBreakdown.missing_image_urls.map(url => <li key={url}>{url}</li>)}
                </ul>
              </div>
            ) : null}
            {archiveBreakdown.missing_shared_image_urls?.length ? (
              <div className="archive-summary-list">
                <span>{t('missing_shared_image_urls')}</span>
                <ul>
                  {archiveBreakdown.missing_shared_image_urls.map(url => <li key={url}>{url}</li>)}
                </ul>
              </div>
            ) : null}
          </div>
        </details>
      )}

      {payloadEntries.length > 0 && (
        <>
          <h2>{t('payload')}</h2>
          <div className="table-wrap"><table>
            <thead><tr><th>{t('key')}</th><th>{t('value')}</th></tr></thead>
            <tbody>
              {payloadEntries.map(([k, v], i) => (
                <tr key={i}><td className="mono">{k}</td><td style={{ textAlign: 'left' }}>{typeof v === 'object' ? JSON.stringify(v) : String(v ?? '-')}</td></tr>
              ))}
            </tbody>
          </table></div>
        </>
      )}

      {artifactEntries.length > 0 && (
        <>
          <h2>{t('artifacts')}</h2>
          <div className="table-wrap"><table>
            <thead><tr><th>{t('key')}</th><th>{t('value')}</th></tr></thead>
            <tbody>
              {artifactEntries.map(([k, v], i) => (
                <tr key={i}><td className="mono">{k}</td><td style={{ textAlign: 'left' }}>{typeof v === 'object' ? JSON.stringify(v) : String(v ?? '-')}</td></tr>
              ))}
            </tbody>
          </table></div>
        </>
      )}

      {events.length > 0 && (
        <>
          <h2>{t('event_timeline')}</h2>
          <div className="table-wrap"><table>
            <thead><tr><th>{t('id')}</th><th>{t('time')}</th><th>{t('event_type')}</th><th>{t('status')}</th><th>{t('stage')}</th><th>{eventPayloadTitle}</th></tr></thead>
            <tbody>
              {events.map(e => (
                <tr key={e.event_id}>
                  <td className="mono">{e.event_id}</td>
                  <td className="nowrap">{formatDateTime(e.created_at)}</td>
                  <td className="nowrap">{e.event_type}</td>
                  <td><Badge status={e.status} /></td>
                  <td className="nowrap">{e.stage || '-'}</td>
                  <td style={{ textAlign: 'left' }}>{renderValue(e.payload)}</td>
                </tr>
              ))}
            </tbody>
          </table></div>
        </>
      )}
    </>
  )
}
