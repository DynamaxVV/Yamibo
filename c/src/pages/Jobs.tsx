import { useEffect, useRef, useState, useCallback } from 'react'
import { Link } from 'react-router-dom'
import { api, type JobSummary, type BackfillStatus } from '../api/client'
import { Badge } from '../components/Badge'
import { PaginationControls } from '../components/PaginationControls'
import { useI18n } from '../context/I18nContext'
import { formatDateTime } from '../utils/time'
import { formatJobFailureKind, getJobFailureKind, type JobFailureKind } from '../utils/jobMessages'
import { useTableLayout } from '../components/TableLayoutEditor'

const STATUSES = [null, 'queued', 'running', 'paused', 'succeeded', 'partial', 'failed', 'interrupted'] as const
const FAILURE_KINDS: Array<JobFailureKind | null> = [null, 'forum_closed', 'thread_deleted', 'thread_permission', 'thread_missing', 'login_required', 'maintenance', 'remote_http_404', 'remote_http_error', 'remote_timeout', 'remote_connection', 'remote_blocked', 'remote_fetch', 'unexpected_page', 'empty_content', 'local_missing', 'validation', 'cancelled', 'other']
const PAGE_SIZE_OPTIONS = [25, 50, 100] as const
const STORAGE_KEY = 'yamibo_jobs_status'
const FAILURE_STORAGE_KEY = 'yamibo_jobs_failure_kind'
const PAGE_STORAGE_KEY = 'yamibo_jobs_page'
const PAGE_SIZE_STORAGE_KEY = 'yamibo_jobs_page_size'
const IDLE_TASKS_STORAGE_KEY = 'yamibo_jobs_idle_tasks'
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
  const [page, setPage] = useState(() => {
    try {
      const saved = Number(localStorage.getItem(PAGE_STORAGE_KEY))
      return saved > 0 ? saved : 1
    } catch { return 1 }
  })
  const [pageSize, setPageSize] = useState<number>(() => {
    try {
      const saved = Number(localStorage.getItem(PAGE_SIZE_STORAGE_KEY) || 25)
      return PAGE_SIZE_OPTIONS.includes(saved as typeof PAGE_SIZE_OPTIONS[number]) ? saved : 25
    } catch {
      return 25
    }
  })
  const tableLayout = useTableLayout('jobs')
  const column = (key: string) => tableLayout.find(item => item.key === key)
  const [totalPages, setTotalPages] = useState(1)
  const [totalCount, setTotalCount] = useState(0)
  const [failureKindCounts, setFailureKindCounts] = useState<Record<string, number>>({})
  const [failureKindCountsLoaded, setFailureKindCountsLoaded] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState<JobSummary | null>(null)
  const [confirmBatchDelete, setConfirmBatchDelete] = useState<string | null>(null)
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  const [confirmSelectedDelete, setConfirmSelectedDelete] = useState(false)
  const [deleteError, setDeleteError] = useState<string | null>(null)
  const [jobActionError, setJobActionError] = useState<string | null>(null)
  const [pendingCancelId, setPendingCancelId] = useState<string | null>(null)
  const [pendingRetryId, setPendingRetryId] = useState<string | null>(null)
  const [pendingSelectedRetry, setPendingSelectedRetry] = useState(false)
  const [pendingAction, setPendingAction] = useState(false)
  const [showIdleTasks, setShowIdleTasks] = useState(false)
  const [backfillStatus, setBackfillStatus] = useState<BackfillStatus | null>(null)
  const [idleJobs, setIdleJobs] = useState<JobSummary[]>([])
  const jobsRef = useRef<JobSummary[]>([])
  const countsRef = useRef<Record<string, number>>({})
  const refreshTokenRef = useRef(0)
  const loadingTimerRef = useRef<number | null>(null)
  const prevStatusRef = useRef(status)

  const desc = (j: JobSummary) => lang === 'en' ? j.description_en : j.description

  useEffect(() => {
    if (prevStatusRef.current === status) return // 首次渲染跳过，只在 status 真正变化时清空筛选
    prevStatusRef.current = status
    setPage(1)
    setSelectedIds(new Set())
    setFailureKind(null)
    try { localStorage.removeItem(FAILURE_STORAGE_KEY) } catch {}
    try { localStorage.removeItem(PAGE_STORAGE_KEY) } catch {}
  }, [status])

  useEffect(() => {
    try { localStorage.setItem(PAGE_STORAGE_KEY, String(page)) } catch {}
  }, [page])

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
      setFailureKindCountsLoaded(status === 'failed')
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
        const [status, allJobs] = await Promise.all([
          api.backfillStatus(),
          api.jobs({ page_size: 50 }),
        ])
        if (!active) return
        setBackfillStatus(status)
        setIdleJobs(allJobs.items.filter(j => j.job_type === 'image_backfill'))
      } catch { /* ignore */ }
      if (!showIdleTasks) {
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
            setFailureKindCountsLoaded(status === 'failed')
            jobsRef.current = nextJobs.items
            countsRef.current = nextCounts
          }
        } catch { /* ignore */ }
      }
    }, 5000)
    // 首次加载立即拉取
    api.backfillStatus().then(s => { if (active) setBackfillStatus(s) }).catch(() => {})
    api.jobs({ page_size: 50 }).then(r => { if (active) setIdleJobs(r.items.filter(j => j.job_type === 'image_backfill')) }).catch(() => {})
    return () => {
      active = false
      window.clearInterval(poll)
    }
  }, [failureKind, failureKindCounts, page, pageSize, status, t, showIdleTasks])

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
    setShowIdleTasks(false)
    try { localStorage.removeItem(IDLE_TASKS_STORAGE_KEY) } catch {}
    setStatus(s)
    try { s ? localStorage.setItem(STORAGE_KEY, s) : localStorage.removeItem(STORAGE_KEY) } catch {}
  }

  const setFailureKindAndRemember = (kind: JobFailureKind | null) => {
    setFailureKind(kind)
    try { kind ? localStorage.setItem(FAILURE_STORAGE_KEY, kind) : localStorage.removeItem(FAILURE_STORAGE_KEY) } catch {}
  }

  useEffect(() => {
    if (!failureKindCountsLoaded || !failureKind) return
    if ((failureKindCounts[failureKind] || 0) > 0) return
    setFailureKindAndRemember(null)
    setPage(1)
    setSelectedIds(new Set())
  }, [failureKind, failureKindCounts, failureKindCountsLoaded])

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
    setPendingAction(true)
    try {
      if (j.status === 'paused') {
        await api.resumeJob(j.job_id)
      } else {
        await api.pauseJob(j.job_id)
      }
      await refreshJobs(false)
    } catch (e: any) {
      setJobActionError(e.message || String(e))
    } finally {
      setPendingAction(false)
    }
  }

  const handleRetry = async (j: JobSummary) => {
    setJobActionError(null)
    setPendingRetryId(j.job_id)
    setPendingAction(true)
    try {
      await api.retryJob(j.job_id)
      await refreshJobs(false)
    } catch (e: any) {
      setJobActionError(e.message || String(e))
    } finally {
      setPendingRetryId(null)
      setPendingAction(false)
    }
  }

  const confirmDoDelete = async () => {
    if (!confirmDelete) return
    setPendingAction(true)
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
    } finally {
      setPendingAction(false)
    }
  }

  const handleBatchDelete = async () => {
    if (!confirmBatchDelete) return
    setPendingAction(true)
    try {
      let deletedCount = 0
      if (confirmBatchDelete === 'failed' && failureKind) {
        const result = await api.batchDeleteJobs(confirmBatchDelete, failureKind)
        deletedCount = result.deleted
      } else {
        const result = await api.batchDeleteJobs(confirmBatchDelete)
        deletedCount = result.deleted
      }
      setConfirmBatchDelete(null)
      setDeleteError(null)
      refreshCounts()
      if (deletedCount > 0) await refreshJobs(false)
    } catch (e: any) {
      setDeleteError(e.message || String(e))
    } finally {
      setPendingAction(false)
    }
  }

  const handleSelectedDelete = async () => {
    const ids = Array.from(selectedIds)
    if (ids.length === 0) return
    setPendingAction(true)
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
    } finally {
      setPendingAction(false)
    }
  }

  const handleSelectedRetry = async () => {
    const retryableJobs = selectedJobs.filter(job => job.status === 'failed' || job.status === 'partial' || job.status === 'interrupted')
    if (retryableJobs.length === 0) return
    setJobActionError(null)
    setPendingSelectedRetry(true)
    setPendingAction(true)
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
      setPendingAction(false)
    }
  }

  const handleSelectedPause = async () => {
    const queuedJobs = selectedJobs.filter(job => job.status === 'queued')
    if (queuedJobs.length === 0) return
    setJobActionError(null)
    setPendingSelectedRetry(true)
    setPendingAction(true)
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
      setPendingAction(false)
    }
  }

  const handleSelectedResume = async () => {
    const pausedJobs = selectedJobs.filter(job => job.status === 'paused')
    if (pausedJobs.length === 0) return
    setJobActionError(null)
    setPendingSelectedRetry(true)
    setPendingAction(true)
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
      setPendingAction(false)
    }
  }

  const batchDeleteKey = (status === 'succeeded' || status === 'failed' || status === 'interrupted' || status === 'partial') ? status : null
  const pagedIds = jobs.map(j => j.job_id)
  const allPagedSelected = pagedIds.length > 0 && pagedIds.every(id => selectedIds.has(id))
  const canRetrySelected = selectedJobs.some(job => job.status === 'failed' || job.status === 'partial' || job.status === 'interrupted')
  const canPauseSelected = selectedJobs.length > 0 && selectedJobs.every(job => job.status === 'queued')
  const canResumeSelected = selectedJobs.length > 0 && selectedJobs.every(job => job.status === 'paused')

  return (
    <>
      <div className="jobs-toolbar">
        <div className="segmented jobs-status-tabs">
          {STATUSES.map(s => {
            const key = s || 'all'
            const count = statusCounts[key]
            return (
              <a key={key} className={status === s && !showIdleTasks ? 'active' : ''}
                data-count-digits={count == null ? 0 : String(Math.abs(count)).length}
                href="#" onClick={e => { e.preventDefault(); setStatusAndRemember(s) }}>
                {s ? t(s) : t('all')}{count != null && <span className="seg-count">{count}</span>}
              </a>
            )
          })}
        </div>
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
              {FAILURE_KINDS.filter(kind => kind === null || (failureKindCounts[kind] || 0) > 0).map(kind => (
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
            {batchDeleteKey === 'failed' && failureKind
              ? (lang === 'en' ? `Clear all "${formatJobFailureKind(failureKind, lang)}" failed` : `一键清除当前失败类型`)
              : t(`delete_all_${batchDeleteKey}`)}
          </button>
        )}
        <div className="jobs-toolbar-end">
          {selectedIds.size > 0 && (
            <>
            <button className="btn-danger-outline toolbar-compact-btn" style={{ marginLeft: 8 }}
              onClick={() => setConfirmSelectedDelete(true)} disabled={pendingAction}>
              {t('delete_selected')} <span className="seg-count">{selectedIds.size}</span>
            </button>
            {canRetrySelected && (
              <button className="btn-subtle toolbar-compact-btn" style={{ marginLeft: 8 }}
                onClick={() => void handleSelectedRetry()} disabled={pendingSelectedRetry || pendingAction}>
                {pendingSelectedRetry ? t('running') : <>{t('rerun')} <span className="seg-count">{selectedIds.size}</span></>}
              </button>
            )}
            {canPauseSelected && (
              <button className="btn-subtle toolbar-compact-btn" style={{ marginLeft: 8 }}
                onClick={() => void handleSelectedPause()} disabled={pendingSelectedRetry || pendingAction}>
                {pendingSelectedRetry ? t('running') : `${t('batch_pause_selected')} (${selectedIds.size})`}
              </button>
            )}
            {canResumeSelected && (
              <button className="btn-subtle toolbar-compact-btn" style={{ marginLeft: 8 }}
                onClick={() => void handleSelectedResume()} disabled={pendingSelectedRetry || pendingAction}>
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
      {(() => {
        const idleTypes = [...new Set(idleJobs.map(j => j.job_type))]
        const todayInfo = backfillStatus
          ? ` · ${t('backfill_pending_count')}: ${backfillStatus.pending_count ?? '-'} · ${t('backfill_today_count')}: ${backfillStatus.today_count ?? '-'}/${backfillStatus.daily_limit ?? '-'}`
          : ''
        const names = idleTypes.length > 0 ? idleTypes.map(jt => t(`job_type_${jt}` as any) || jt).join(' · ') : ''
        return (
          <div style={{ marginTop: 4, fontSize: 11, color: 'var(--text-tertiary)' }}>
            <a href="#" onClick={e => {
              e.preventDefault()
              setShowIdleTasks(prev => !prev)
              try { localStorage.setItem(IDLE_TASKS_STORAGE_KEY, showIdleTasks ? '0' : '1') } catch {}
            }} style={{ color: 'var(--text-tertiary)' }}>
              {t('idle_tasks')}{names ? ` · ${names}` : ''}{todayInfo}
            </a>
          </div>
        )
      })()}
      {showIdleTasks ? (
        <>
          {backfillStatus && (
            <div className="panel" style={{ marginTop: 12 }}>
              <h3 style={{ margin: '0 0 12px', fontSize: 14, color: 'var(--text-primary)' }}>{t('backfill_params_title')}</h3>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))', gap: '8px 16px', fontSize: 13 }}>
                <div><span style={{ color: 'var(--text-tertiary)' }}>{t('backfill_enabled')}</span>: {backfillStatus.enabled ? '✅' : '❌'}</div>
                <div><span style={{ color: 'var(--text-tertiary)' }}>{t('backfill_dry_run')}</span>: {backfillStatus.dry_run ? '⚠️' : '✅'}</div>
                <div><span style={{ color: 'var(--text-tertiary)' }}>{t('backfill_forum_id')}</span>: {backfillStatus.forum_id}</div>
                <div><span style={{ color: 'var(--text-tertiary)' }}>{t('backfill_daily_limit')}</span>: {backfillStatus.daily_limit}</div>
                <div><span style={{ color: 'var(--text-tertiary)' }}>{t('backfill_interval')}</span>: {backfillStatus.interval_seconds}</div>
                <div><span style={{ color: 'var(--text-tertiary)' }}>{t('backfill_max_pages')}</span>: {backfillStatus.max_pages}</div>
              </div>
              <h3 style={{ margin: '16px 0 12px', fontSize: 14, color: 'var(--text-primary)' }}>{t('backfill_status_title')}</h3>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))', gap: '8px 16px', fontSize: 13 }}>
                <div><span style={{ color: 'var(--text-tertiary)' }}>{t('backfill_today_count')}</span>: <strong>{backfillStatus.today_count}</strong> / {backfillStatus.daily_limit}</div>
                {backfillStatus.last_enqueued_at && <div><span style={{ color: 'var(--text-tertiary)' }}>{t('backfill_last_enqueued')}</span>: {formatDateTime(backfillStatus.last_enqueued_at)}</div>}
                {backfillStatus.last_reason && <div><span style={{ color: 'var(--text-tertiary)' }}>{t('backfill_last_reason')}</span>: {backfillStatus.last_reason}</div>}
              </div>
            </div>
          )}
          <div id="jobs-pagination-top" />
          <div className="jobs-table-shell">
            <div className="table-wrap"><table style={{ tableLayout: 'fixed', width: '100%' }}>
            <thead><tr>
              <th style={{ width: 65 }}>{t('tid')}</th>
              <th style={{ width: '35%' }}>{t('description')}</th>
              <th style={{ width: 80 }}>{t('status')}</th>
              <th style={{ width: 80 }} className="hide-mobile">{t('progress')}</th>
              <th style={{ width: 110 }}>{t('created_at')}</th>
            </tr></thead>
            <tbody>
              {idleJobs.length === 0 ? (
                <tr><td colSpan={5} style={{ textAlign: 'center', padding: 32, color: 'var(--text-tertiary)' }}>{t('backfill_no_jobs')}</td></tr>
              ) : idleJobs.map(j => (
                <tr key={j.job_id}>
                  <td>{j.tid ? <Link to={`/threads/${j.tid}`}>{j.tid}</Link> : '-'}</td>
                  <td className="truncate" title={desc(j)}><Link to={`/jobs/${j.job_id}`}>{desc(j)}</Link></td>
                  <td><Badge status={j.status} /></td>
                  <td className="nowrap hide-mobile">{j.progress_current}/{j.progress_total ?? '?'}</td>
                  <td className="nowrap col-time">{formatDateTime(j.created_at)}</td>
                </tr>
              ))}
            </tbody>
            </table></div>
          </div>
        </>
      ) : (
        <>
      <div id="jobs-pagination-top" />
      <div className="jobs-table-shell">
        <div className="table-wrap"><table style={{ tableLayout: 'fixed', width: '100%' }}>
        <thead><tr>
          <th style={{ width: 32 }}><input type="checkbox" checked={allPagedSelected} onChange={toggleSelectAll} /></th>
          {column('tid')?.visible && <th style={{ width: column('tid')?.width }}>{t('tid')}</th>}
          {column('description')?.visible && <th style={{ width: column('description')?.width }}>{t('description')}</th>}
          {column('status')?.visible && <th style={{ width: column('status')?.width }}>{t('status')}</th>}
          {column('stage')?.visible && <th style={{ width: column('stage')?.width }} className="hide-mobile">{t('stage')}</th>}
          {column('progress')?.visible && <th style={{ width: column('progress')?.width }} className="hide-mobile">{t('progress')}</th>}
          {column('created_at')?.visible && <th style={{ width: column('created_at')?.width }}>{t('created_at')}</th>}
          <th style={{ width: column('action')?.width || 110 }}>{t('action')}</th>
        </tr></thead>
        <tbody>
          {jobs.map(j => (
            <tr key={j.job_id}>
              <td><input type="checkbox" checked={selectedIds.has(j.job_id)} onChange={() => toggleSelect(j.job_id)} /></td>
              {column('tid')?.visible && <td>{j.tid ? <Link to={`/threads/${j.tid}`}>{j.tid}</Link> : '-'}</td>}
              {column('description')?.visible && <td className="truncate" title={desc(j)}><Link to={`/jobs/${j.job_id}`}>{desc(j)}</Link></td>}
              {column('status')?.visible && <td>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 4, alignItems: 'center', justifyContent: 'center' }}>
                  <Badge status={j.status} />
                  {(() => {
                    const kind = j.failure_kind || getJobFailureKind(j)
                    return kind ? <span className="badge badge-muted">{formatJobFailureKind(kind, lang)}</span> : null
                  })()}
                </div>
              </td>}
              {column('stage')?.visible && <td className="nowrap hide-mobile">{j.stage || '-'}</td>}
              {column('progress')?.visible && <td className="nowrap hide-mobile">{j.progress_current}/{j.progress_total ?? '?'}</td>}
              {column('created_at')?.visible && <td className="nowrap col-time">{formatDateTime(j.created_at)}</td>}
              <td>
                <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                  {(j.status === 'partial' || j.status === 'failed' || j.status === 'interrupted') && (
                    <button className="btn-subtle" onClick={() => void handleRetry(j)} disabled={pendingRetryId === j.job_id || pendingAction} style={{ fontSize: 11, padding: '2px 6px' }}>
                      {pendingRetryId === j.job_id ? t('running') : t('rerun')}
                    </button>
                  )}
                  {(j.status === 'queued' || j.status === 'running' || j.status === 'retrying' || j.status === 'paused') && (
                    <button className="btn-subtle" onClick={() => handlePauseResume(j)} disabled={pendingAction} style={{ fontSize: 11, padding: '2px 6px' }}>
                      {j.status === 'paused' ? t('resume') : t('pause')}
                    </button>
                  )}
                  <button className="btn-subtle" onClick={() => handleDelete(j)} disabled={pendingAction} style={{ fontSize: 11, padding: '2px 6px' }}>{t('delete')}</button>
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
        </>
      )}

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
              <button className="btn-subtle" onClick={() => { setConfirmDelete(null); setDeleteError(null) }} disabled={pendingAction}>{t('cancel')}</button>
              <button className="btn-danger" onClick={confirmDoDelete} disabled={pendingAction}>
                {pendingAction ? <span className="loading-spinner" style={{ width: 12, height: 12, borderWidth: 2, marginRight: 6, display: 'inline-block', verticalAlign: 'middle' }} /> : null}
                {pendingAction ? t('running') : t('confirm_execute')}
              </button>
            </div>
          </div>
        </div>
      )}

      {confirmBatchDelete && (
        <div className="confirm-overlay" onClick={() => { setConfirmBatchDelete(null); setDeleteError(null) }}>
          <div className="confirm-dialog" onClick={e => e.stopPropagation()}>
            <h2 style={{ margin: '0 0 16px', fontSize: 15, color: 'var(--text-primary)', textTransform: 'none', letterSpacing: 0 }}>
              {confirmBatchDelete === 'failed' && failureKind
                ? (lang === 'en' ? `Clear all "${formatJobFailureKind(failureKind, lang)}" failed jobs` : '一键清除当前失败类型')
                : t(`delete_all_${confirmBatchDelete}`)}
            </h2>
            <p style={{ fontSize: 13, color: 'var(--text-secondary)', lineHeight: 1.6, margin: '0 0 16px' }}>
              {confirmBatchDelete === 'failed' && failureKind
                ? (lang === 'en'
                  ? `Are you sure you want to delete all ${totalCount} failed jobs of type "${formatJobFailureKind(failureKind, lang)}"? This action cannot be undone.`
                  : `确定删除当前类型 (${failureKind ? formatJobFailureKind(failureKind, lang) : ''}) 的全部 ${totalCount} 条失败任务？此操作不可撤销。`)
                : t(`batch_delete_confirm_${confirmBatchDelete}`, { n: String(statusCounts[confirmBatchDelete] ?? jobs.length) })}
            </p>
            {deleteError && <p style={{ fontSize: 12, color: 'var(--status-error)', margin: '0 0 12px' }}>{deleteError}</p>}
            <div className="confirm-actions">
              <button className="btn-subtle" onClick={() => { setConfirmBatchDelete(null); setDeleteError(null) }} disabled={pendingAction}>{t('cancel')}</button>
              <button className="btn-danger" onClick={handleBatchDelete} disabled={pendingAction}>
                {pendingAction ? <><span className="loading-spinner" style={{ width: 12, height: 12, borderWidth: 2, marginRight: 6, display: 'inline-block', verticalAlign: 'middle' }} />{t('running')}</> : t('confirm_execute')}
              </button>
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
              <button className="btn-subtle" onClick={() => { setConfirmSelectedDelete(false); setDeleteError(null) }} disabled={pendingAction}>{t('cancel')}</button>
              <button className="btn-danger" onClick={handleSelectedDelete} disabled={pendingAction}>
                {pendingAction ? <><span className="loading-spinner" style={{ width: 12, height: 12, borderWidth: 2, marginRight: 6, display: 'inline-block', verticalAlign: 'middle' }} />{t('running')}</> : t('confirm_execute')}
              </button>
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
