import { useEffect, useRef, useState, useCallback } from 'react'
import { Link } from 'react-router-dom'
import { api, type JobSummary } from '../api/client'
import { Badge } from '../components/Badge'
import { PaginationControls } from '../components/PaginationControls'
import { useI18n } from '../context/I18nContext'
import { formatDateTime } from '../utils/time'

const STATUSES = [null, 'queued', 'running', 'paused', 'succeeded', 'partial', 'failed', 'interrupted'] as const
const PAGE_SIZE = 50
const STORAGE_KEY = 'yamibo_jobs_status'

export function Jobs() {
  const { t, lang } = useI18n()
  const [jobs, setJobs] = useState<JobSummary[]>([])
  const [statusCounts, setStatusCounts] = useState<Record<string, number>>({})
  const [status, setStatus] = useState<string | null>(() => {
    try { return localStorage.getItem(STORAGE_KEY) as string | null || null } catch { return null }
  })
  const [page, setPage] = useState(1)
  const [confirmDelete, setConfirmDelete] = useState<JobSummary | null>(null)
  const [confirmBatchDelete, setConfirmBatchDelete] = useState<string | null>(null)
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  const [confirmSelectedDelete, setConfirmSelectedDelete] = useState(false)
  const [deleteError, setDeleteError] = useState<string | null>(null)
  const [jobActionError, setJobActionError] = useState<string | null>(null)
  const [pendingCancelId, setPendingCancelId] = useState<string | null>(null)
  const [pendingRetryId, setPendingRetryId] = useState<string | null>(null)
  const jobsRef = useRef<JobSummary[]>([])
  const countsRef = useRef<Record<string, number>>({})

  const desc = (j: JobSummary) => lang === 'en' ? j.description_en : j.description

  const refreshCounts = useCallback(() => {
    api.jobCounts().then(next => {
      setStatusCounts(next)
      countsRef.current = next
    }).catch(() => {})
  }, [])

  useEffect(() => {
    refreshCounts()
    api.jobs(status || undefined).then(next => {
      setJobs(next)
      jobsRef.current = next
    }).catch(() => {})
  }, [refreshCounts, status])

  useEffect(() => {
    setPage(1)
    setSelectedIds(new Set())
  }, [status])

  const refreshJobs = useCallback(async () => {
    try {
      const [nextJobs, nextCounts] = await Promise.all([
        api.jobs(status || undefined),
        api.jobCounts(),
      ])
      setJobs(nextJobs)
      setStatusCounts(nextCounts)
      jobsRef.current = nextJobs
      countsRef.current = nextCounts
    } catch {
      // ignore
    }
  }, [status])

  useEffect(() => {
    let active = true
    const poll = window.setInterval(async () => {
      try {
        const [nextJobs, nextCounts] = await Promise.all([
          api.jobs(status || undefined),
          api.jobCounts(),
        ])
        if (!active) return
        const prevJobsKey = JSON.stringify(jobsRef.current.map(j => [j.job_id, j.status, j.stage, j.updated_at]))
        const nextJobsKey = JSON.stringify(nextJobs.map(j => [j.job_id, j.status, j.stage, j.updated_at]))
        const prevCountsKey = JSON.stringify(countsRef.current)
        const nextCountsKey = JSON.stringify(nextCounts)
        if (prevJobsKey !== nextJobsKey || prevCountsKey !== nextCountsKey) {
          setJobs(nextJobs)
          setStatusCounts(nextCounts)
          jobsRef.current = nextJobs
          countsRef.current = nextCounts
          setPage(currentPage => {
            const totalPages = Math.max(1, Math.ceil(nextJobs.length / PAGE_SIZE))
            return Math.min(currentPage, totalPages)
          })
        }
      } catch { /* ignore */ }
    }, 5000)
    return () => {
      active = false
      window.clearInterval(poll)
    }
  }, [status, t])

  useEffect(() => {
    if (!pendingCancelId) return
    let cancelled = false
    const poll = setInterval(async () => {
      try {
        const j = await api.job(pendingCancelId)
        if (cancelled) return
        if (j.status !== 'running' && j.status !== 'cancel_requested') {
          await api.deleteJob(pendingCancelId)
          setPendingCancelId(null)
          refreshJobs()
        }
      } catch {
        setPendingCancelId(null)
      }
    }, 2000)
    return () => { cancelled = true; clearInterval(poll) }
  }, [pendingCancelId, refreshJobs])

  const setStatusAndRemember = (s: string | null) => {
    setStatus(s)
    try { s ? localStorage.setItem(STORAGE_KEY, s) : localStorage.removeItem(STORAGE_KEY) } catch {}
  }

  const totalPages = Math.max(1, Math.ceil(jobs.length / PAGE_SIZE))
  const paged = jobs.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE)

  const toggleSelect = (jobId: string) => {
    setSelectedIds(prev => {
      const next = new Set(prev)
      next.has(jobId) ? next.delete(jobId) : next.add(jobId)
      return next
    })
  }

  const toggleSelectAll = () => {
    const pagedIds = paged.map(j => j.job_id)
    const allSelected = pagedIds.every(id => selectedIds.has(id))
    setSelectedIds(prev => {
      const next = new Set(prev)
      if (allSelected) {
        pagedIds.forEach(id => next.delete(id))
      } else {
        pagedIds.forEach(id => next.add(id))
      }
      return next
    })
  }

  const handleDelete = async (j: JobSummary) => {
    setDeleteError(null)
    setConfirmDelete(j)
  }

  const handlePauseResume = async (j: JobSummary) => {
    setJobActionError(null)
    try {
      if (j.status === 'paused') {
        await api.resumeJob(j.job_id)
      } else {
        await api.pauseJob(j.job_id)
      }
      await refreshJobs()
    } catch (e: any) {
      setJobActionError(e.message || String(e))
    }
  }

  const handleRetry = async (j: JobSummary) => {
    setJobActionError(null)
    setPendingRetryId(j.job_id)
    try {
      await api.retryJob(j.job_id)
      await refreshJobs()
    } catch (e: any) {
      setJobActionError(e.message || String(e))
    } finally {
      setPendingRetryId(null)
    }
  }

  const confirmDoDelete = async () => {
    if (!confirmDelete) return
    try {
      const result = await api.safeDeleteJob(confirmDelete.job_id)
      if (result.action === 'cancel_requested') {
        setPendingCancelId(confirmDelete.job_id)
        setConfirmDelete(null)
        setDeleteError(null)
      } else {
        setJobs(prev => prev.filter(job => job.job_id !== confirmDelete.job_id))
        jobsRef.current = jobsRef.current.filter(job => job.job_id !== confirmDelete.job_id)
        setConfirmDelete(null)
        setDeleteError(null)
        refreshCounts()
      }
    } catch (e: any) {
      setDeleteError(e.message || String(e))
    }
  }

  const handleBatchDelete = async () => {
    if (!confirmBatchDelete) return
    try {
      const result = await api.batchDeleteJobs(confirmBatchDelete)
      if (result.deleted > 0) {
        setJobs(prev => prev.filter(job => job.status !== confirmBatchDelete))
        jobsRef.current = jobsRef.current.filter(job => job.status !== confirmBatchDelete)
      }
      setConfirmBatchDelete(null)
      setDeleteError(null)
      refreshCounts()
    } catch (e: any) {
      setDeleteError(e.message || String(e))
    }
  }

  const handleSelectedDelete = async () => {
    const ids = Array.from(selectedIds)
    if (ids.length === 0) return
    try {
      const deletedIds = new Set(ids)
      const result = await api.batchDeleteJobIds(ids)
      if (result.deleted > 0) {
        setJobs(prev => prev.filter(job => !deletedIds.has(job.job_id)))
        jobsRef.current = jobsRef.current.filter(job => !deletedIds.has(job.job_id))
      }
      setSelectedIds(new Set())
      setConfirmSelectedDelete(false)
      setDeleteError(null)
      refreshCounts()
    } catch (e: any) {
      setDeleteError(e.message || String(e))
    }
  }

  const batchDeleteKey = (status === 'succeeded' || status === 'failed' || status === 'interrupted' || status === 'partial') ? status : null
  const pagedIds = paged.map(j => j.job_id)
  const allPagedSelected = pagedIds.length > 0 && pagedIds.every(id => selectedIds.has(id))

  return (
    <>
      <div className="segmented">
        {STATUSES.map(s => {
          const key = s || 'all'
          const count = statusCounts[key]
          return (
            <a key={key} className={status === s ? 'active' : ''}
              href="#" onClick={e => { e.preventDefault(); setStatusAndRemember(s) }}>
              {s ? t(s) : t('all')}{count != null && <span className="seg-count">{count}</span>}
            </a>
          )
        })}
        {batchDeleteKey && jobs.length > 0 && (
          <button className="btn-danger-outline" style={{ marginLeft: 8, fontSize: 12, padding: '3px 8px' }}
            onClick={() => setConfirmBatchDelete(batchDeleteKey)}>
            {t(`delete_all_${batchDeleteKey}`)}
          </button>
        )}
        {selectedIds.size > 0 && (
          <button className="btn-danger-outline" style={{ marginLeft: 8, fontSize: 12, padding: '3px 8px' }}
            onClick={() => setConfirmSelectedDelete(true)}>
            {t('delete_selected')} ({selectedIds.size})
          </button>
        )}
      </div>
      {jobActionError && <div className="panel" style={{ marginTop: 12, color: 'var(--status-error)' }}>{jobActionError}</div>}
      <div id="jobs-pagination-top" />
      <div className="table-wrap"><table style={{ tableLayout: 'fixed', width: '100%' }}>
        <thead><tr>
          <th style={{ width: 32 }}><input type="checkbox" checked={allPagedSelected} onChange={toggleSelectAll} /></th>
          <th style={{ width: 65 }}>{t('tid')}</th>
          <th style={{ width: '35%' }}>{t('description')}</th>
          <th style={{ width: 80 }}>{t('status')}</th>
          <th style={{ width: 80 }} className="hide-mobile">{t('stage')}</th>
          <th style={{ width: 80 }} className="hide-mobile">{t('progress')}</th>
          <th style={{ width: 110 }}>{t('created_at')}</th>
          <th style={{ width: 110 }}>{t('action')}</th>
        </tr></thead>
        <tbody>
          {paged.map(j => (
            <tr key={j.job_id}>
              <td><input type="checkbox" checked={selectedIds.has(j.job_id)} onChange={() => toggleSelect(j.job_id)} /></td>
              <td>{j.tid ? <Link to={`/threads/${j.tid}`}>{j.tid}</Link> : '-'}</td>
              <td className="truncate" title={desc(j)}><Link to={`/jobs/${j.job_id}`}>{desc(j)}</Link></td>
              <td><Badge status={j.status} /></td>
              <td className="nowrap hide-mobile">{j.stage || '-'}</td>
              <td className="nowrap hide-mobile">{j.progress_current}/{j.progress_total ?? '?'}</td>
              <td className="nowrap col-time">{formatDateTime(j.created_at)}</td>
              <td>
                <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                  {(j.status === 'partial' || j.status === 'failed') && (
                    <button className="btn-subtle" onClick={() => void handleRetry(j)} disabled={pendingRetryId === j.job_id} style={{ fontSize: 11, padding: '2px 6px' }}>
                      {pendingRetryId === j.job_id ? t('running') : t('rerun')}
                    </button>
                  )}
                  {(j.status === 'queued' || j.status === 'running' || j.status === 'retrying' || j.status === 'paused') && (
                    <button className="btn-subtle" onClick={() => handlePauseResume(j)} style={{ fontSize: 11, padding: '2px 6px' }}>
                      {j.status === 'paused' ? t('resume') : t('pause')}
                    </button>
                  )}
                  <button className="btn-subtle" onClick={() => handleDelete(j)} style={{ fontSize: 11, padding: '2px 6px' }}>{t('delete')}</button>
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table></div>

      <PaginationControls page={page} totalPages={totalPages} onPageChange={setPage} scrollTargetId="jobs-pagination-top" />

      {confirmDelete && (
        <div className="confirm-overlay" onClick={() => { setConfirmDelete(null); setDeleteError(null) }}>
          <div className="confirm-dialog" onClick={e => e.stopPropagation()}>
            <h2 style={{ margin: '0 0 16px', fontSize: 15, color: 'var(--text-primary)', textTransform: 'none', letterSpacing: 0 }}>{t('confirm_delete')}</h2>
            <p style={{ fontSize: 13, color: 'var(--text-secondary)', lineHeight: 1.6, margin: '0 0 16px' }}>
              {confirmDelete.status === 'running' ? t('delete_running_job_warning') : t('delete_job_confirm')}
            </p>
            <p style={{ fontSize: 12, color: 'var(--text-tertiary)', margin: '0 0 16px' }}>
              {desc(confirmDelete)}
            </p>
            {deleteError && <p style={{ fontSize: 12, color: 'var(--status-error)', margin: '0 0 12px' }}>{deleteError}</p>}
            <div className="confirm-actions">
              <button className="btn-subtle" onClick={() => { setConfirmDelete(null); setDeleteError(null) }}>{t('cancel')}</button>
              <button className="btn-danger" onClick={confirmDoDelete}>{t('confirm_execute')}</button>
            </div>
          </div>
        </div>
      )}

      {confirmBatchDelete && (
        <div className="confirm-overlay" onClick={() => { setConfirmBatchDelete(null); setDeleteError(null) }}>
          <div className="confirm-dialog" onClick={e => e.stopPropagation()}>
            <h2 style={{ margin: '0 0 16px', fontSize: 15, color: 'var(--text-primary)', textTransform: 'none', letterSpacing: 0 }}>{t(`delete_all_${confirmBatchDelete}`)}</h2>
            <p style={{ fontSize: 13, color: 'var(--text-secondary)', lineHeight: 1.6, margin: '0 0 16px' }}>
              {t(`batch_delete_confirm_${confirmBatchDelete}`, { n: String(statusCounts[confirmBatchDelete] ?? jobs.length) })}
            </p>
            {deleteError && <p style={{ fontSize: 12, color: 'var(--status-error)', margin: '0 0 12px' }}>{deleteError}</p>}
            <div className="confirm-actions">
              <button className="btn-subtle" onClick={() => { setConfirmBatchDelete(null); setDeleteError(null) }}>{t('cancel')}</button>
              <button className="btn-danger" onClick={handleBatchDelete}>{t('confirm_execute')}</button>
            </div>
          </div>
        </div>
      )}

      {confirmSelectedDelete && (
        <div className="confirm-overlay" onClick={() => setConfirmSelectedDelete(false)}>
          <div className="confirm-dialog" onClick={e => e.stopPropagation()}>
            <h2 style={{ margin: '0 0 16px', fontSize: 15, color: 'var(--text-primary)', textTransform: 'none', letterSpacing: 0 }}>{t('delete_selected')}</h2>
            <p style={{ fontSize: 13, color: 'var(--text-secondary)', lineHeight: 1.6, margin: '0 0 16px' }}>
              {t('delete_selected_confirm', { n: String(selectedIds.size) })}
            </p>
            {deleteError && <p style={{ fontSize: 12, color: 'var(--status-error)', margin: '0 0 12px' }}>{deleteError}</p>}
            <div className="confirm-actions">
              <button className="btn-subtle" onClick={() => { setConfirmSelectedDelete(false); setDeleteError(null) }}>{t('cancel')}</button>
              <button className="btn-danger" onClick={handleSelectedDelete}>{t('confirm_execute')}</button>
            </div>
          </div>
        </div>
      )}

      {pendingCancelId && (
        <div className="confirm-overlay" style={{ cursor: 'wait' }}>
          <div className="confirm-dialog" onClick={e => e.stopPropagation()}>
            <h2 style={{ margin: '0 0 16px', fontSize: 15, color: 'var(--text-primary)', textTransform: 'none', letterSpacing: 0 }}>{t('cancelling_job')}</h2>
            <p style={{ fontSize: 13, color: 'var(--text-secondary)', lineHeight: 1.6, margin: '0 0 16px' }}>
              {t('cancelling_job_desc')}
            </p>
            <div style={{ height: 4, borderRadius: 2, background: 'var(--bg-muted)', overflow: 'hidden', marginBottom: 16 }}>
              <div style={{ height: '100%', background: 'var(--accent)', animation: 'pulse 1.5s ease-in-out infinite', width: '60%' }} />
            </div>
            <div className="confirm-actions">
              <button className="btn-subtle" onClick={() => setPendingCancelId(null)}>{t('background')}</button>
            </div>
          </div>
        </div>
      )}
    </>
  )
}
