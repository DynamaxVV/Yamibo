import { useEffect, useRef, useState, useCallback } from 'react'
import { Link } from 'react-router-dom'
import { api, type JobSummary } from '../api/client'
import { Badge } from '../components/Badge'
import { PaginationControls } from '../components/PaginationControls'
import { useI18n } from '../context/I18nContext'
import { formatDateTime } from '../utils/time'
import { formatJobFailureKind, getJobFailureKind, type JobFailureKind } from '../utils/jobMessages'

const STATUSES = [null, 'queued', 'running', 'paused', 'succeeded', 'partial', 'failed', 'interrupted', 'superseded'] as const
const FAILURE_KINDS: Array<JobFailureKind | null> = [null, 'forum_closed', 'thread_missing', 'login_required', 'maintenance', 'remote_fetch', 'unexpected_page', 'empty_content', 'local_missing', 'validation', 'cancelled', 'other']
const PAGE_SIZE_OPTIONS = [25, 50, 100] as const
const STORAGE_KEY = 'yamibo_jobs_status'
const FAILURE_STORAGE_KEY = 'yamibo_jobs_failure_kind'
const PAGE_SIZE_STORAGE_KEY = 'yamibo_jobs_page_size'
const LOADING_DELAY_MS = 180

export function Jobs() {
  const { t, lang } = useI18n()
  const [jobs, setJobs] = useState<JobSummary[]>([])
  const [statusCounts, setStatusCounts] = useState<Record<string, number>>({})
  const [isLoading, setIsLoading] = useState(true)
  const [status, setStatus] = useState<string | null>(() => {
    try { return localStorage.getItem(STORAGE_KEY) as string | null || null } catch { return null }
  })
  const [failureKind, setFailureKind] = useState<JobFailureKind | null>(() => {
    try { return (localStorage.getItem(FAILURE_STORAGE_KEY) as JobFailureKind | null) || null } catch { return null }
  })
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState<number>(() => {
    try {
      const saved = Number(localStorage.getItem(PAGE_SIZE_STORAGE_KEY) || 25)
      return PAGE_SIZE_OPTIONS.includes(saved as typeof PAGE_SIZE_OPTIONS[number]) ? saved : 25
    } catch {
      return 25
    }
  })
  const [totalPages, setTotalPages] = useState(1)
  const [totalCount, setTotalCount] = useState(0)
  const [failureKindCounts, setFailureKindCounts] = useState<Record<string, number>>({})
  const [confirmDelete, setConfirmDelete] = useState<JobSummary | null>(null)
  const [confirmBatchDelete, setConfirmBatchDelete] = useState<string | null>(null)
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  const [confirmSelectedDelete, setConfirmSelectedDelete] = useState(false)
  const [deleteError, setDeleteError] = useState<string | null>(null)
  const [jobActionError, setJobActionError] = useState<string | null>(null)
  const [pendingCancelId, setPendingCancelId] = useState<string | null>(null)
  const [pendingRetryId, setPendingRetryId] = useState<string | null>(null)
  const [pendingSelectedRetry, setPendingSelectedRetry] = useState(false)
  const jobsRef = useRef<JobSummary[]>([])
  const countsRef = useRef<Record<string, number>>({})
  const refreshTokenRef = useRef(0)
  const loadingTimerRef = useRef<number | null>(null)

  const desc = (j: JobSummary) => lang === 'en' ? j.description_en : j.description

  useEffect(() => {
    setPage(1)
    setSelectedIds(new Set())
    setFailureKind(null)
    try { localStorage.removeItem(FAILURE_STORAGE_KEY) } catch {}
  }, [status])

  useEffect(() => {
    setSelectedIds(new Set())
  }, [page, pageSize])

  const refreshCounts = useCallback(() => {
    api.jobCounts().then(next => {
      setStatusCounts(next)
      countsRef.current = next
    }).catch(() => {})
  }, [])

  const refreshJobs = useCallback(async (showLoading = false) => {
    const token = ++refreshTokenRef.current
    if (loadingTimerRef.current !== null) {
      window.clearTimeout(loadingTimerRef.current)
      loadingTimerRef.current = null
    }
    if (showLoading) {
      loadingTimerRef.current = window.setTimeout(() => {
        if (token === refreshTokenRef.current) setIsLoading(true)
      }, LOADING_DELAY_MS)
    }
    try {
      const jobsRequest = api.jobs({
        status: status || undefined,
        failure_kind: status === 'failed' ? failureKind || undefined : undefined,
        page,
        page_size: pageSize,
      })
      const failureCountsRequest = status === 'failed' ? api.jobFailureCounts('failed') : Promise.resolve<Record<string, number>>({})
      const [nextJobs, nextCounts, nextFailureCounts] = await Promise.all([jobsRequest, api.jobCounts(), failureCountsRequest])
      if (token !== refreshTokenRef.current) return
      setJobs(nextJobs.items)
      setTotalPages(nextJobs.total_pages)
      setTotalCount(nextJobs.total_count)
      setStatusCounts(nextCounts)
      setFailureKindCounts(nextFailureCounts)
      jobsRef.current = nextJobs.items
      countsRef.current = nextCounts
    } catch {
      // ignore
    } finally {
      if (loadingTimerRef.current !== null) {
        window.clearTimeout(loadingTimerRef.current)
        loadingTimerRef.current = null
      }
      if (showLoading && token === refreshTokenRef.current) setIsLoading(false)
    }
  }, [failureKind, page, pageSize, status])

  useEffect(() => () => {
    if (loadingTimerRef.current !== null) {
      window.clearTimeout(loadingTimerRef.current)
      loadingTimerRef.current = null
    }
  }, [])

  useEffect(() => {
    void refreshJobs(true)
  }, [refreshJobs])

  useEffect(() => {
    let active = true
    const poll = window.setInterval(async () => {
      try {
        const jobsRequest = api.jobs({
          status: status || undefined,
          failure_kind: status === 'failed' ? failureKind || undefined : undefined,
          page,
          page_size: pageSize,
        })
        const failureCountsRequest = status === 'failed' ? api.jobFailureCounts('failed') : Promise.resolve<Record<string, number>>({})
        const [nextJobs, nextCounts, nextFailureCounts] = await Promise.all([jobsRequest, api.jobCounts(), failureCountsRequest])
        if (!active) return
        const prevJobsKey = JSON.stringify(jobsRef.current.map(j => [j.job_id, j.status, j.stage, j.updated_at]))
        const nextJobsKey = JSON.stringify(nextJobs.items.map(j => [j.job_id, j.status, j.stage, j.updated_at]))
        const prevCountsKey = JSON.stringify(countsRef.current)
        const nextCountsKey = JSON.stringify(nextCounts)
        const prevFailureCountsKey = JSON.stringify(failureKindCounts)
        const nextFailureCountsKey = JSON.stringify(nextFailureCounts)
        if (prevJobsKey !== nextJobsKey || prevCountsKey !== nextCountsKey || prevFailureCountsKey !== nextFailureCountsKey) {
          setJobs(nextJobs.items)
          setTotalPages(nextJobs.total_pages)
          setTotalCount(nextJobs.total_count)
          setStatusCounts(nextCounts)
          setFailureKindCounts(nextFailureCounts)
          jobsRef.current = nextJobs.items
          countsRef.current = nextCounts
        }
      } catch { /* ignore */ }
    }, 5000)
    return () => {
      active = false
      window.clearInterval(poll)
    }
  }, [failureKind, failureKindCounts, page, pageSize, status, t])

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
          await refreshJobs(false)
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

  const setFailureKindAndRemember = (kind: JobFailureKind | null) => {
    setFailureKind(kind)
    try { kind ? localStorage.setItem(FAILURE_STORAGE_KEY, kind) : localStorage.removeItem(FAILURE_STORAGE_KEY) } catch {}
  }

  const setPageSizeAndRemember = (nextPageSize: number) => {
    setPageSize(nextPageSize)
    setPage(1)
    setSelectedIds(new Set())
    try { localStorage.setItem(PAGE_SIZE_STORAGE_KEY, String(nextPageSize)) } catch {}
  }

  const showFailureKindSelect = status === 'failed'
  const paged = jobs

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

  const selectedJobs = jobs.filter(job => selectedIds.has(job.job_id))

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
      await refreshJobs(false)
    } catch (e: any) {
      setJobActionError(e.message || String(e))
    }
  }

  const handleRetry = async (j: JobSummary) => {
    setJobActionError(null)
    setPendingRetryId(j.job_id)
    try {
      await api.retryJob(j.job_id)
      await refreshJobs(false)
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

  const handleSelectedRetry = async () => {
    const retryableJobs = selectedJobs.filter(job => job.status === 'failed' || job.status === 'partial' || job.status === 'interrupted')
    if (retryableJobs.length === 0) return
    setJobActionError(null)
    setPendingSelectedRetry(true)
    try {
      for (const job of retryableJobs) {
        await api.retryJob(job.job_id)
      }
      setSelectedIds(new Set())
      await refreshJobs(false)
    } catch (e: any) {
      setJobActionError(e.message || String(e))
    } finally {
      setPendingSelectedRetry(false)
    }
  }

  const handleSelectedPause = async () => {
    const queuedJobs = selectedJobs.filter(job => job.status === 'queued')
    if (queuedJobs.length === 0) return
    setJobActionError(null)
    setPendingSelectedRetry(true)
    try {
      for (const job of queuedJobs) {
        await api.pauseJob(job.job_id)
      }
      setSelectedIds(new Set())
      await refreshJobs(false)
    } catch (e: any) {
      setJobActionError(e.message || String(e))
    } finally {
      setPendingSelectedRetry(false)
    }
  }

  const handleSelectedResume = async () => {
    const pausedJobs = selectedJobs.filter(job => job.status === 'paused')
    if (pausedJobs.length === 0) return
    setJobActionError(null)
    setPendingSelectedRetry(true)
    try {
      for (const job of pausedJobs) {
        await api.resumeJob(job.job_id)
      }
      setSelectedIds(new Set())
      await refreshJobs(false)
    } catch (e: any) {
      setJobActionError(e.message || String(e))
    } finally {
      setPendingSelectedRetry(false)
    }
  }

  const batchDeleteKey = (status === 'succeeded' || status === 'failed' || status === 'interrupted' || status === 'partial' || status === 'superseded') ? status : null
  const pagedIds = jobs.map(j => j.job_id)
  const allPagedSelected = pagedIds.length > 0 && pagedIds.every(id => selectedIds.has(id))
  const canRetrySelected = selectedJobs.some(job => job.status === 'failed' || job.status === 'partial' || job.status === 'interrupted')
  const canPauseSelected = selectedJobs.length > 0 && selectedJobs.every(job => job.status === 'queued')
  const canResumeSelected = selectedJobs.length > 0 && selectedJobs.every(job => job.status === 'paused')

  return (
    <>
      <div className="segmented jobs-toolbar">
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
        {showFailureKindSelect && (
          <label className="job-failure-filter">
            <span>{lang === 'en' ? 'Failure kind' : '失败类型'}</span>
            <select
              value={failureKind || ''}
              onChange={e => {
                const next = (e.target.value || null) as JobFailureKind | null
                setFailureKindAndRemember(next)
                setPage(1)
                setSelectedIds(new Set())
              }}
            >
              {FAILURE_KINDS.map(kind => (
                <option key={kind || 'all'} value={kind || ''}>
                  {kind ? `${formatJobFailureKind(kind, lang)} (${failureKindCounts[kind] || 0})` : `${lang === 'en' ? 'All failure kinds' : '全部失败类型'} (${statusCounts.failed || 0})`}
                </option>
              ))}
            </select>
          </label>
        )}
        {batchDeleteKey && totalCount > 0 && (
          <button className="btn-danger-outline toolbar-compact-btn" style={{ marginLeft: 8 }}
            onClick={() => setConfirmBatchDelete(batchDeleteKey)}>
            {t(`delete_all_${batchDeleteKey}`)}
          </button>
        )}
        <div className="jobs-toolbar-end">
          {selectedIds.size > 0 && (
            <>
            <button className="btn-danger-outline toolbar-compact-btn" style={{ marginLeft: 8 }}
              onClick={() => setConfirmSelectedDelete(true)}>
              {t('delete_selected')} ({selectedIds.size})
            </button>
            {canRetrySelected && (
              <button className="btn-subtle toolbar-compact-btn" style={{ marginLeft: 8 }}
                onClick={() => void handleSelectedRetry()} disabled={pendingSelectedRetry}>
                {pendingSelectedRetry ? t('running') : `${t('rerun')} (${selectedIds.size})`}
              </button>
            )}
            {canPauseSelected && (
              <button className="btn-subtle toolbar-compact-btn" style={{ marginLeft: 8 }}
                onClick={() => void handleSelectedPause()} disabled={pendingSelectedRetry}>
                {pendingSelectedRetry ? t('running') : `${t('batch_pause_selected')} (${selectedIds.size})`}
              </button>
            )}
            {canResumeSelected && (
              <button className="btn-subtle toolbar-compact-btn" style={{ marginLeft: 8 }}
                onClick={() => void handleSelectedResume()} disabled={pendingSelectedRetry}>
                {pendingSelectedRetry ? t('running') : `${t('batch_resume_selected')} (${selectedIds.size})`}
              </button>
            )}
            </>
          )}
          <label className="job-page-size">
            <span>{t('page_size')}</span>
            <select value={String(pageSize)} onChange={e => setPageSizeAndRemember(Number(e.target.value))}>
              {PAGE_SIZE_OPTIONS.map(option => <option key={option} value={option}>{option}</option>)}
            </select>
          </label>
        </div>
      </div>
      {jobActionError && <div className="panel" style={{ marginTop: 12, color: 'var(--status-error)' }}>{jobActionError}</div>}
      <div id="jobs-pagination-top" />
      <div className="jobs-table-shell">
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
          {jobs.map(j => (
            <tr key={j.job_id}>
              <td><input type="checkbox" checked={selectedIds.has(j.job_id)} onChange={() => toggleSelect(j.job_id)} /></td>
              <td>{j.tid ? <Link to={`/threads/${j.tid}`}>{j.tid}</Link> : '-'}</td>
              <td className="truncate" title={desc(j)}><Link to={`/jobs/${j.job_id}`}>{desc(j)}</Link></td>
              <td>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 4, alignItems: 'center', justifyContent: 'center' }}>
                  <Badge status={j.status} />
                  {j.status === 'superseded' && j.rerun_job_status && (
                    <div style={{ display: 'inline-flex', gap: 4, alignItems: 'center', flexWrap: 'wrap', justifyContent: 'center', fontSize: 11, color: 'var(--text-tertiary)' }}>
                      <span>{t('rerun_status')}</span>
                      <Badge status={j.rerun_job_status} />
                    </div>
                  )}
                  {(() => {
                    const kind = j.failure_kind || getJobFailureKind(j)
                    return kind ? <span className="badge badge-muted">{formatJobFailureKind(kind, lang)}</span> : null
                  })()}
                </div>
              </td>
              <td className="nowrap hide-mobile">{j.stage || '-'}</td>
              <td className="nowrap hide-mobile">{j.progress_current}/{j.progress_total ?? '?'}</td>
              <td className="nowrap col-time">{formatDateTime(j.created_at)}</td>
              <td>
                <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                  {(j.status === 'partial' || j.status === 'failed' || j.status === 'interrupted') && (
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
        {isLoading && (
          <div className="jobs-loading-overlay" aria-live="polite" aria-busy="true">
            <div className="jobs-loading-card">
              <span className="loading-spinner" />
              <span>{t('loading')}</span>
            </div>
          </div>
        )}
      </div>

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
