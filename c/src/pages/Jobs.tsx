import { useEffect, useRef, useState, useCallback, useMemo, type CSSProperties } from 'react'
import { Link } from 'react-router-dom'
import {
  RotateCcw,
  Pause,
  Play,
  Trash2,
  AlertCircle,
  SlidersHorizontal,
  Layers,
  X,
} from 'lucide-react'
import { api, type JobSummary, type BackfillStatus } from '../api/client'
import { Badge } from '../components/Badge'
import { PaginationControls } from '../components/PaginationControls'
import { useI18n } from '../context/I18nContext'
import { formatDateTime } from '../utils/time'
import { formatJobFailureKind, getJobFailureKind, type JobFailureKind } from '../utils/jobMessages'
import { useResponsiveTableWidths, useTableLayout } from '../components/TableLayoutEditor'
import { cn } from '../lib/utils'
import '../styles/tools.css'

const STATUSES = [null, 'queued', 'running', 'retrying', 'paused', 'succeeded', 'partial', 'failed', 'interrupted'] as const
const FAILURE_KINDS: Array<JobFailureKind | null> = [
  null,
  'forum_closed',
  'thread_deleted',
  'thread_permission',
  'thread_missing',
  'login_required',
  'maintenance',
  'remote_http_404',
  'remote_http_error',
  'remote_timeout',
  'remote_connection',
  'remote_blocked',
  'remote_fetch',
  'unexpected_page',
  'image_download_failed',
  'empty_content',
  'local_missing',
  'validation',
  'cancelled',
  'other',
]

function formatDateTimeStacked(value: string | null) {
  const formatted = formatDateTime(value)
  if (formatted === '-') return formatted
  const [datePart, timePart, ...rest] = formatted.split(' ')
  if (!datePart || !timePart || rest.length > 0) return formatted
  return (
    <span className="flex flex-col items-center text-center leading-tight">
      <span className="text-foreground">{datePart}</span>
      <span className="text-muted-foreground/80">{timePart}</span>
    </span>
  )
}
const PAGE_SIZE_OPTIONS = [25, 50, 100] as const
const STORAGE_KEY = 'yamibo_jobs_status'
const FAILURE_STORAGE_KEY = 'yamibo_jobs_failure_kind'
const PAGE_STORAGE_KEY = 'yamibo_jobs_page'
const PAGE_SIZE_STORAGE_KEY = 'yamibo_jobs_page_size'
const IDLE_TASKS_STORAGE_KEY = 'yamibo_jobs_idle_tasks'
const LOADING_DELAY_MS = 180

export function Jobs() {
  const { t, lang, tx } = useI18n()
  const [jobs, setJobs] = useState<JobSummary[]>([])
  const [statusCounts, setStatusCounts] = useState<Record<string, number>>({})
  const [isLoading, setIsLoading] = useState(true)
  const [status, setStatus] = useState<string | null>(() => {
    try {
      return (localStorage.getItem(STORAGE_KEY) as string | null) || null
    } catch {
      return null
    }
  })
  const [failureKind, setFailureKind] = useState<JobFailureKind | null>(() => {
    try {
      return (localStorage.getItem(FAILURE_STORAGE_KEY) as JobFailureKind | null) || null
    } catch {
      return null
    }
  })
  const [page, setPage] = useState(() => {
    try {
      const saved = Number(localStorage.getItem(PAGE_STORAGE_KEY))
      return saved > 0 ? saved : 1
    } catch {
      return 1
    }
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
  const [tableContainer, setTableContainer] = useState<HTMLDivElement | null>(null)
  const { widths: responsiveWidths, tableWidth } = useResponsiveTableWidths(tableLayout, tableContainer, 'description')
  const column = (key: string) => tableLayout.find(item => item.key === key)
  const columnWidth = (key: string) => {
    const width = responsiveWidths[key] ?? column(key)?.width
    return width == null ? undefined : { width }
  }
  const [totalPages, setTotalPages] = useState(1)
  const [totalCount, setTotalCount] = useState(0)
  const [failureKindCounts, setFailureKindCounts] = useState<Record<string, number>>({})
  const [, setFailureKindCountsLoaded] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState<JobSummary | null>(null)
  const [confirmBatchDelete, setConfirmBatchDelete] = useState<string | null>(null)
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  const [confirmSelectedDelete, setConfirmSelectedDelete] = useState(false)
  const [deleteError, setDeleteError] = useState<string | null>(null)
  const [jobActionError, setJobActionError] = useState<string | null>(null)
  const [, setPendingCancelId] = useState<string | null>(null)
  const [pendingRetryId, setPendingRetryId] = useState<string | null>(null)
  const [pendingSelectedRetry, setPendingSelectedRetry] = useState(false)
  const [pendingAction, setPendingAction] = useState(false)
  const [showIdleTasks, setShowIdleTasks] = useState(false)
  const [backfillStatus, setBackfillStatus] = useState<BackfillStatus | null>(null)
  const jobsRef = useRef<JobSummary[]>([])
  const countsRef = useRef<Record<string, number>>({})
  const refreshTokenRef = useRef(0)
  const loadingTimerRef = useRef<number | null>(null)
  const prevStatusRef = useRef(status)

  const desc = (j: JobSummary) => (lang === 'en' ? j.description_en : j.description)

  useEffect(() => {
    if (prevStatusRef.current === status) return
    prevStatusRef.current = status
    setPage(1)
    setSelectedIds(new Set())
    setFailureKind(null)
    try {
      localStorage.removeItem(FAILURE_STORAGE_KEY)
    } catch {}
    try {
      localStorage.removeItem(PAGE_STORAGE_KEY)
    } catch {}
  }, [status])

  useEffect(() => {
    try {
      localStorage.setItem(PAGE_STORAGE_KEY, String(page))
    } catch {}
  }, [page])

  useEffect(() => {
    setSelectedIds(new Set())
  }, [page, pageSize])

  const refreshCounts = useCallback(() => {
    api
      .jobCounts()
      .then(next => {
        setStatusCounts(next)
        countsRef.current = next
      })
      .catch(() => {})
  }, [])

  const refreshJobs = useCallback(
    async (showLoading = false) => {
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
        const failureCountsRequest =
          status === 'failed'
            ? api.jobFailureCounts('failed')
            : Promise.resolve<Record<string, number>>({})
        const [nextJobs, nextCounts, nextFailureCounts] = await Promise.all([
          jobsRequest,
          api.jobCounts(),
          failureCountsRequest,
        ])
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
    },
    [failureKind, page, pageSize, status]
  )

  useEffect(
    () => () => {
      if (loadingTimerRef.current !== null) {
        window.clearTimeout(loadingTimerRef.current)
        loadingTimerRef.current = null
      }
    },
    []
  )

  useEffect(() => {
    void refreshJobs(true)
  }, [refreshJobs])

  useEffect(() => {
    let active = true
    const poll = window.setInterval(async () => {
      try {
        const backfillStat = await api.backfillStatus()
        if (!active) return
        setBackfillStatus(backfillStat)
      } catch {}
      try {
        const jobsRequest = api.jobs({
          status: status || undefined,
          failure_kind: status === 'failed' ? failureKind || undefined : undefined,
          page,
          page_size: pageSize,
        })
        const failureCountsRequest =
          status === 'failed'
            ? api.jobFailureCounts('failed')
            : Promise.resolve<Record<string, number>>({})
        const [nextJobs, nextCounts, nextFailureCounts] = await Promise.all([
          jobsRequest,
          api.jobCounts(),
          failureCountsRequest,
        ])
        if (!active) return
        const prevJobsKey = JSON.stringify(
          jobsRef.current.map(j => [j.job_id, j.status, j.stage, j.updated_at])
        )
        const nextJobsKey = JSON.stringify(
          nextJobs.items.map(j => [j.job_id, j.status, j.stage, j.updated_at])
        )
        const prevCountsKey = JSON.stringify(countsRef.current)
        const nextCountsKey = JSON.stringify(nextCounts)
        const prevFailureCountsKey = JSON.stringify(failureKindCounts)
        const nextFailureCountsKey = JSON.stringify(nextFailureCounts)
        if (
          prevJobsKey !== nextJobsKey ||
          prevCountsKey !== nextCountsKey ||
          prevFailureCountsKey !== nextFailureCountsKey
        ) {
          setJobs(nextJobs.items)
          setTotalPages(nextJobs.total_pages)
          setTotalCount(nextJobs.total_count)
          setStatusCounts(nextCounts)
          setFailureKindCounts(nextFailureCounts)
          setFailureKindCountsLoaded(status === 'failed')
          jobsRef.current = nextJobs.items
          countsRef.current = nextCounts
        }
      } catch {}
    }, 5000)

    api
      .backfillStatus()
      .then(s => {
        if (active) setBackfillStatus(s)
      })
      .catch(() => {})
    return () => {
      active = false
      window.clearInterval(poll)
    }
  }, [failureKind, failureKindCounts, page, pageSize, status])

  const setStatusAndRemember = (s: string | null) => {
    setStatus(s)
    setSelectedIds(new Set())
    try {
      if (s) localStorage.setItem(STORAGE_KEY, s)
      else localStorage.removeItem(STORAGE_KEY)
    } catch {}
  }

  const setFailureKindAndRemember = (k: JobFailureKind | null) => {
    setFailureKind(k)
    try {
      if (k) localStorage.setItem(FAILURE_STORAGE_KEY, k)
      else localStorage.removeItem(FAILURE_STORAGE_KEY)
    } catch {}
  }

  const setPageSizeAndRemember = (size: number) => {
    setPageSize(size)
    setPage(1)
    try {
      localStorage.setItem(PAGE_SIZE_STORAGE_KEY, String(size))
    } catch {}
  }

  const toggleSelect = (jobId: string) => {
    setSelectedIds(prev => {
      const next = new Set(prev)
      if (next.has(jobId)) next.delete(jobId)
      else next.add(jobId)
      return next
    })
  }

  const toggleSelectAll = () => {
    const pagedIds = jobs.map(j => j.job_id)
    if (pagedIds.length === 0) return
    const allSelected = pagedIds.every(id => selectedIds.has(id))
    setSelectedIds(prev => {
      const next = new Set(prev)
      pagedIds.forEach(id => {
        if (allSelected) next.delete(id)
        else next.add(id)
      })
      return next
    })
  }

  const selectedJobs = useMemo(
    () => jobs.filter(job => selectedIds.has(job.job_id)),
    [jobs, selectedIds]
  )

  const handlePauseResume = async (j: JobSummary) => {
    setJobActionError(null)
    setPendingAction(true)
    try {
      if (j.status === 'paused') await api.resumeJob(j.job_id)
      else await api.pauseJob(j.job_id)
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
    const retryableJobs = selectedJobs.filter(
      job => job.status === 'failed' || job.status === 'partial' || job.status === 'interrupted'
    )
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

  const batchDeleteKey =
    status === 'succeeded' ||
    status === 'failed' ||
    status === 'interrupted' ||
    status === 'partial'
      ? status
      : null
  const displayJobs = jobs
  const pagedIds = displayJobs.map(j => j.job_id)
  const allPagedSelected = pagedIds.length > 0 && pagedIds.every(id => selectedIds.has(id))
  const canRetrySelected = selectedJobs.some(
    job => job.status === 'failed' || job.status === 'partial' || job.status === 'interrupted'
  )
  const canPauseSelected =
    selectedJobs.length > 0 && selectedJobs.every(job => job.status === 'queued')
  const canResumeSelected =
    selectedJobs.length > 0 && selectedJobs.every(job => job.status === 'paused')

  return (
    <div className="space-y-3">
      <header className="border-b border-border pb-3">
        <h1 className="text-3xl">{tx('任务中心', 'Task center')}</h1>
        <p className="mt-1 text-sm text-muted-foreground">{tx('查看任务进度、处理失败与暂停的任务。', 'Track task progress and manage failed or paused tasks.')}</p>
      </header>
      {/* ─── Top Filter & Action Bar ─── */}
      <div className="space-y-3">
        {/* Status Pills Bar */}
        <div className="flex items-center gap-1.5 overflow-x-auto p-1 bg-card border border-border rounded-md shadow-2xs">
          {STATUSES.map(s => {
            const key = s || 'all'
            const count = statusCounts[key]
            const active = status === s

            return (
              <button
                key={key}
                onClick={() => setStatusAndRemember(s)}
                className={cn(
                  'flex shrink-0 items-center gap-1.5 whitespace-nowrap px-3 py-2 rounded-sm text-xs font-mono font-medium transition-all press-feedback select-none',
                  active
                    ? 'bg-yamibo-burgundy text-white dark:bg-yamibo-burgundy-light shadow-2xs font-semibold'
                    : 'text-muted-foreground hover:text-foreground hover:bg-muted/70'
                )}
              >
                <span>{s ? t(s) : t('all')}</span>
                {count != null && (
                  <span
                    className={cn(
                      'text-[10px] px-1 py-0.2 rounded-full font-bold',
                      active
                        ? 'bg-white/20 text-white'
                        : 'bg-muted text-muted-foreground/90'
                    )}
                  >
                    {count}
                  </span>
                )}
              </button>
            )
          })}
        </div>

        {/* Sub-Filters & Controls Toolbar */}
        <div className="flex flex-wrap items-center justify-between gap-3 text-xs font-mono">
          <div className="flex flex-wrap items-center gap-2">
            {/* Failure Kind Dropdown */}
            {status === 'failed' && (
              <div className="flex items-center gap-1.5">
                <span className="text-muted-foreground">{lang === 'en' ? 'Failure:' : '失败分类:'}</span>
                <select
                  value={failureKind || ''}
                  onChange={e => {
                    const next = (e.target.value || null) as JobFailureKind | null
                    setFailureKindAndRemember(next)
                    setPage(1)
                    setSelectedIds(new Set())
                  }}
                  className="px-2 py-1 rounded-sm border border-border bg-card text-foreground text-xs font-mono focus:outline-none focus:ring-1 focus:ring-yamibo-burgundy"
                >
                  {FAILURE_KINDS.filter(
                    kind => kind === null || (failureKindCounts[kind] || 0) > 0
                  ).map(kind => (
                    <option key={kind || 'all'} value={kind || ''}>
                      {kind
                        ? `${formatJobFailureKind(kind, lang)} (${failureKindCounts[kind] || 0})`
                        : `${lang === 'en' ? 'All failure kinds' : '全部失败类型'} (${statusCounts.failed || 0})`}
                    </option>
                  ))}
                </select>
              </div>
            )}

            {/* Quick Batch Clear by Status */}
            {batchDeleteKey && totalCount > 0 && (
              <button
                onClick={() => setConfirmBatchDelete(batchDeleteKey)}
                className="flex items-center gap-1.5 px-2.5 py-1 rounded-sm border border-rose-200 bg-rose-50 hover:bg-rose-100 text-rose-800 dark:bg-rose-950/40 dark:border-rose-900/60 dark:text-rose-300 font-sans transition-colors press-feedback"
              >
                <Trash2 className="w-3 h-3" />
                <span>
                  {batchDeleteKey === 'failed' && failureKind
                    ? lang === 'en'
                      ? `Clear "${formatJobFailureKind(failureKind, lang)}"`
                    : tx('一键清除此失败类型', 'Clear all of this failure type')
                    : t(`delete_all_${batchDeleteKey}`)}
                </span>
              </button>
            )}

            {/* Backfill / Idle Tasks Toggle */}
            <button
              onClick={() => {
                setShowIdleTasks(prev => !prev)
                setSelectedIds(new Set())
                try {
                  localStorage.setItem(IDLE_TASKS_STORAGE_KEY, showIdleTasks ? '0' : '1')
                } catch {}
              }}
              className={cn(
                'flex items-center gap-1.5 px-2.5 py-1 rounded-sm border transition-colors press-feedback font-sans',
                showIdleTasks
                  ? 'bg-yamibo-burgundy text-white border-yamibo-burgundy font-medium'
                  : 'bg-card border-border text-muted-foreground hover:text-foreground'
              )}
            >
              <Layers className="w-3 h-3" />
              <span>{t('idle_tasks')}</span>
              {backfillStatus && (
                <span className="font-mono text-[11px] opacity-80">
                  ({backfillStatus.today_count ?? 0}/{backfillStatus.daily_limit ?? '-'})
                </span>
              )}
            </button>
          </div>

          <div className="flex items-center gap-3">
            {/* Page Size Select */}
            <div className="flex items-center gap-1.5 text-muted-foreground">
              <span>{t('page_size')}:</span>
              <select
                value={String(pageSize)}
                onChange={e => setPageSizeAndRemember(Number(e.target.value))}
                className="px-2 py-0.5 rounded-sm border border-border bg-card text-foreground font-mono focus:outline-none focus:ring-1 focus:ring-yamibo-burgundy"
              >
                {PAGE_SIZE_OPTIONS.map(option => (
                  <option key={option} value={option}>
                    {option}
                  </option>
                ))}
              </select>
            </div>
          </div>
        </div>
      </div>

      {/* ─── Action Error Notice ─── */}
      {jobActionError && (
        <div className="flex items-center gap-2 p-3 rounded-md bg-rose-50 border border-rose-200 text-rose-800 dark:bg-rose-950/40 dark:border-rose-900/60 dark:text-rose-300 text-xs font-mono">
          <AlertCircle className="w-4 h-4 shrink-0" />
          <span>{jobActionError}</span>
        </div>
      )}

      {/* ─── Idle / Backfill Tasks Panel (if expanded) ─── */}
      {showIdleTasks && backfillStatus && (
        <div className="p-4 bg-card border border-border rounded-md shadow-2xs space-y-3">
          <div className="flex items-center justify-between border-b border-border pb-2">
            <h3 className="text-xs font-mono uppercase tracking-wider text-foreground font-semibold flex items-center gap-2">
              <SlidersHorizontal className="w-3.5 h-3.5 text-yamibo-burgundy dark:text-yamibo-coral" />
              <span>{t('backfill_params_title')}</span>
            </h3>
            <span className="text-[11px] font-mono text-muted-foreground">
              {backfillStatus.enabled ? `✅ ${tx('已启用', 'Enabled')}` : `❌ ${tx('未启用', 'Disabled')}`}
            </span>
          </div>

          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3 text-xs font-mono">
            <div className="p-2 rounded-sm bg-muted/40 border border-border/50">
              <span className="text-muted-foreground block text-[10px]">{tx('仅预演', 'Dry run only')}</span>
              <span className="font-semibold text-foreground">{backfillStatus.dry_run ? tx('是', 'Yes') : tx('否', 'No')}</span>
            </div>
            <div className="p-2 rounded-sm bg-muted/40 border border-border/50">
              <span className="text-muted-foreground block text-[10px]">{t('backfill_forum_id')}</span>
              <span className="font-semibold text-foreground">#{backfillStatus.forum_id}</span>
            </div>
            <div className="p-2 rounded-sm bg-muted/40 border border-border/50">
              <span className="text-muted-foreground block text-[10px]">{t('backfill_daily_limit')}</span>
              <span className="font-semibold text-foreground">{backfillStatus.daily_limit}</span>
            </div>
            <div className="p-2 rounded-sm bg-muted/40 border border-border/50">
              <span className="text-muted-foreground block text-[10px]">{t('backfill_interval')}</span>
              <span className="font-semibold text-foreground">{backfillStatus.interval_seconds}s</span>
            </div>
            <div className="p-2 rounded-sm bg-muted/40 border border-border/50">
              <span className="text-muted-foreground block text-[10px]">{t('backfill_max_pages')}</span>
              <span className="font-semibold text-foreground">{backfillStatus.max_pages}</span>
            </div>
            <div className="p-2 rounded-sm bg-muted/40 border border-border/50">
              <span className="text-muted-foreground block text-[10px]">{t('backfill_today_count')}</span>
              <span className="font-bold text-yamibo-burgundy dark:text-yamibo-coral">
                {backfillStatus.today_count} / {backfillStatus.daily_limit}
              </span>
            </div>
          </div>
        </div>
      )}

      {/* ─── Jobs Data Table Shell ─── */}
      <div id="jobs-pagination-top" />
      <div className="flex min-h-10 w-full items-center gap-2 overflow-x-auto whitespace-nowrap border-b border-border/50 py-1">
        <p className="min-w-0 truncate text-xs text-muted-foreground max-md:hidden xl:hidden">{tx('表格可横向滚动查看阶段、时间与操作。', 'Scroll the table horizontally to view stages, times, and actions.')}</p>
        {selectedIds.size === 0 && <p className="text-xs text-muted-foreground md:hidden xl:block">{tx('勾选任务以批量处理', 'Select tasks for bulk actions')}</p>}
        {selectedIds.size > 0 && (
          <div role="toolbar" aria-label={tx('已选任务操作', 'Selected task actions')} className="ml-auto flex shrink-0 items-center gap-2 text-xs font-mono">
            <div className="flex shrink-0 items-center gap-1.5">
              <span className="h-1.5 w-1.5 rounded-full bg-yamibo-burgundy dark:bg-yamibo-coral" />
              <span className="text-xs font-mono font-medium text-foreground">
                {tx('已选中', 'Selected')} <span className="font-bold text-yamibo-burgundy dark:text-yamibo-coral">{selectedIds.size}</span> {tx('项任务', 'tasks')}
              </span>
            </div>

            <div className="flex shrink-0 items-center gap-1.5">
              {canRetrySelected && (
                <button
                  onClick={() => void handleSelectedRetry()}
                  disabled={pendingSelectedRetry || pendingAction}
                  className="flex items-center gap-1 px-2 py-1 rounded-sm bg-muted hover:bg-muted/80 text-foreground text-xs font-sans font-medium transition-colors press-feedback disabled:opacity-50"
                >
                  <RotateCcw className="w-3 h-3" />
                  <span>{pendingSelectedRetry ? t('running') : t('rerun')}</span>
                </button>
              )}

              {canPauseSelected && (
                <button
                  onClick={() => void handleSelectedPause()}
                  disabled={pendingSelectedRetry || pendingAction}
                  className="flex items-center gap-1 px-2 py-1 rounded-sm bg-muted hover:bg-muted/80 text-foreground text-xs font-sans font-medium transition-colors press-feedback disabled:opacity-50"
                >
                  <Pause className="w-3 h-3" />
                  <span>{t('batch_pause_selected')}</span>
                </button>
              )}

              {canResumeSelected && (
                <button
                  onClick={() => void handleSelectedResume()}
                  disabled={pendingSelectedRetry || pendingAction}
                  className="flex items-center gap-1 px-2 py-1 rounded-sm bg-muted hover:bg-muted/80 text-foreground text-xs font-sans font-medium transition-colors press-feedback disabled:opacity-50"
                >
                  <Play className="w-3 h-3" />
                  <span>{t('batch_resume_selected')}</span>
                </button>
              )}

              <button
                onClick={() => setConfirmSelectedDelete(true)}
                disabled={pendingAction}
                className="flex items-center gap-1 px-2 py-1 rounded-sm bg-rose-50 hover:bg-rose-100 text-rose-800 border border-rose-200 dark:bg-rose-950/40 dark:border-rose-900/60 dark:text-rose-300 text-xs font-sans font-medium transition-colors press-feedback disabled:opacity-50"
              >
                <Trash2 className="w-3 h-3" />
                <span>{t('delete_selected')}</span>
              </button>

              <button
                onClick={() => setSelectedIds(new Set())}
                className="shrink-0 p-1 rounded-sm text-muted-foreground hover:text-foreground transition-colors"
                title={tx('取消全部选择', 'Clear selection')}
              >
                <X className="w-4 h-4" />
              </button>
            </div>
          </div>
        )}
      </div>
      <div className="space-y-2 md:hidden">
        {displayJobs.length === 0 && <p className="rounded border border-border bg-card p-5 text-sm text-muted-foreground">{isLoading ? t('loading') : t('no_data')}</p>}
        {displayJobs.map(j => <article key={j.job_id} className="rounded border border-border bg-card p-3">
          <div className="flex items-start gap-2">
            <input type="checkbox" checked={selectedIds.has(j.job_id)} onChange={() => toggleSelect(j.job_id)} aria-label={`${tx('选择任务', 'Select task')} ${j.job_id}`} className="mt-1 h-5 w-5 shrink-0" />
            <div className="min-w-0 flex-1">
              <Link to={`/jobs/${j.job_id}`} className="block break-words font-display text-sm font-medium leading-5 text-foreground hover:text-primary">{j.job_type === 'image_backfill' && j.tid ? tx(`为 #${j.tid} 补全图片`, `Recover images for #${j.tid}`) : desc(j)}</Link>
              <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground"><Badge status={j.archive_status || j.status} />{j.tid && <span>#{j.tid}</span>}<span>{j.progress_current}/{j.progress_total ?? '?'}</span><span>{formatDateTime(j.created_at).slice(0, 16)}</span></div>
            </div>
          </div>
          <div className="mt-1 flex justify-end gap-2 text-xs">
            {(j.status === 'partial' || j.status === 'failed' || j.status === 'interrupted') && <button type="button" onClick={() => void handleRetry(j)} disabled={pendingAction} className="min-h-11 rounded border border-border px-3 py-2">{t('rerun')}</button>}
            {['queued', 'running', 'retrying', 'paused'].includes(j.status) && <button type="button" onClick={() => handlePauseResume(j)} disabled={pendingAction} className="min-h-11 rounded border border-border px-3 py-2">{j.status === 'paused' ? t('resume') : t('pause')}</button>}
            <button type="button" onClick={() => setConfirmDelete(j)} disabled={pendingAction} className="min-h-11 rounded border border-destructive px-3 py-2 text-destructive">{t('delete')}</button>
          </div>
        </article>)}
      </div>
      <div ref={setTableContainer} className="relative hidden overflow-x-auto rounded-md border border-border bg-card shadow-2xs md:block">
        <table className="list-table text-left text-[13px] divide-y divide-border" style={{ tableLayout: 'fixed', width: tableWidth }}>
          <thead className="bg-muted/50 text-[11px] font-mono text-muted-foreground uppercase tracking-wider">
            <tr>
              <th className="px-3 py-2.5 w-10">
                <input
                  type="checkbox"
                  checked={allPagedSelected}
                  onChange={toggleSelectAll}
                  className="rounded-xs border-border text-yamibo-burgundy focus:ring-yamibo-burgundy"
                />
              </th>
              {column('tid')?.visible && <th style={columnWidth('tid')} className="column-layout-cell px-3 py-2.5 w-20">{t('tid')}</th>}
              {column('description')?.visible && <th style={columnWidth('description')} className="column-layout-cell min-w-80 px-3 py-2.5">{t('description')}</th>}
              {column('status')?.visible && <th style={columnWidth('status')} className="column-layout-cell px-3 py-2.5 w-36 text-center">{t('status')}</th>}
              {column('stage')?.visible && (
                <th style={columnWidth('stage')} className="column-layout-cell px-3 py-2.5 w-28 hidden md:table-cell">{t('stage')}</th>
              )}
              {column('progress')?.visible && (
                <th style={columnWidth('progress')} className="column-layout-cell px-3 py-2.5 w-24 hidden md:table-cell">{t('progress')}</th>
              )}
              {column('created_at')?.visible && (
                <th style={columnWidth('created_at')} className="column-layout-cell px-3 py-2.5 w-32 hidden sm:table-cell">{t('created_at')}</th>
              )}
              <th style={columnWidth('action')} className="column-layout-cell px-2 py-2.5 w-28 whitespace-nowrap">{t('action')}</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border/60">
            {displayJobs.length === 0 ? (
              <tr>
                <td colSpan={8} className="p-8 text-center text-xs font-mono text-muted-foreground">
                  {isLoading ? t('loading') : t('no_data')}
                </td>
              </tr>
            ) : (
              displayJobs.map(j => {
                const isSelected = selectedIds.has(j.job_id)
                const kind =
                  j.status === 'retrying' ||
                  j.status === 'failed' ||
                  j.status === 'partial' ||
                  j.status === 'interrupted'
                    ? j.failure_kind || getJobFailureKind(j)
                    : null
                const hasProgressRatio = j.progress_total != null && j.progress_total > 0
                const progressRatio = hasProgressRatio
                  ? Math.max(0, Math.min(1, j.progress_current / j.progress_total!))
                  : 0
                const progressPercent = Math.round(progressRatio * 100)
                const progressStep = hasProgressRatio ? 100 / j.progress_total! : 100
                const progressMark = hasProgressRatio && j.progress_total! > 1 ? Math.min(progressStep, 1.5) : 0

                return (
                  <tr
                    key={j.job_id}
                    className={cn(
                      'hover:bg-muted/40 transition-colors press-feedback group',
                      isSelected && 'bg-muted/60'
                    )}
                  >
                    <td className="px-3 py-2">
                      <input
                        type="checkbox"
                        checked={isSelected}
                        onChange={() => toggleSelect(j.job_id)}
                        className="rounded-xs border-border text-yamibo-burgundy focus:ring-yamibo-burgundy"
                      />
                    </td>
                    {column('tid')?.visible && (
                      <td style={columnWidth('tid')} className="px-3 py-2 font-mono text-xs text-muted-foreground whitespace-nowrap">
                        {j.tid ? (
                          <Link
                            to={`/threads/${j.tid}`}
                            className="hover:text-yamibo-burgundy dark:hover:text-yamibo-coral font-medium"
                          >
                            #{j.tid}
                          </Link>
                        ) : (
                          '-'
                        )}
                      </td>
                    )}
                    {column('description')?.visible && (
                      <td style={columnWidth('description')} className="table-cell-long min-w-80 px-3 py-2 font-display font-medium text-foreground">
                        <Link
                          to={`/jobs/${j.job_id}`}
                          className="hover:text-yamibo-burgundy dark:hover:text-yamibo-coral line-clamp-2 break-words leading-5 transition-colors"
                          title={desc(j)}
                        >
                          {j.job_type === 'image_backfill' && j.tid ? tx(`为 #${j.tid} 补全图片`, `Recover images for #${j.tid}`) : desc(j)}
                        </Link>
                      </td>
                    )}
                    {column('status')?.visible && (
                      <td style={columnWidth('status')} className="px-3 py-2 text-center">
                        <div className="flex flex-col gap-1 items-center">
                          <Badge status={j.archive_status || j.status} />
                          {kind && (
                            <span className="text-center text-[10px] font-mono text-muted-foreground">
                              {formatJobFailureKind(kind, lang)}
                            </span>
                          )}
                          {j.status === 'retrying' && (
                            <span className="text-center text-[10px] font-mono text-amber-600 dark:text-amber-400">
                              {tx('退避', 'Backoff')} {j.retry_count}/{j.max_retries}
                            </span>
                          )}
                        </div>
                      </td>
                    )}
                    {column('stage')?.visible && (
                      <td style={columnWidth('stage')} className="px-3 py-2 text-xs font-mono text-muted-foreground hidden md:table-cell">
                        <span className="block truncate" title={j.stage || '-'}>{j.stage || '-'}</span>
                      </td>
                    )}
                    {column('progress')?.visible && (
                      <td style={columnWidth('progress')} className="px-3 py-2 align-middle text-center text-xs font-mono text-muted-foreground whitespace-nowrap hidden md:table-cell">
                        <div
                          className="jobs-progress-meter"
                          role="progressbar"
                          aria-label={tx('任务进度', 'Task progress')}
                          aria-valuemin={0}
                          aria-valuemax={100}
                          aria-valuenow={hasProgressRatio ? progressPercent : undefined}
                          aria-valuetext={hasProgressRatio ? `${progressPercent}%` : tx('进度未知', 'Progress unavailable')}
                          style={{
                            '--progress-ratio': `${progressPercent}%`,
                            '--progress-step': `${progressStep}%`,
                            '--progress-mark': `${progressMark}%`,
                          } as CSSProperties}
                        />
                      </td>
                    )}
                    {column('created_at')?.visible && (
                      <td style={columnWidth('created_at')} className="px-3 py-2 text-center text-xs font-mono text-muted-foreground whitespace-nowrap hidden sm:table-cell">
                        {formatDateTimeStacked(j.created_at)}
                      </td>
                    )}
                    <td style={columnWidth('action')} className="px-2 py-2 whitespace-nowrap">
                      <div className="flex items-center justify-end gap-1.5">
                        {(j.status === 'partial' ||
                          j.status === 'failed' ||
                          j.status === 'interrupted') && (
                          <button
                            onClick={() => void handleRetry(j)}
                            disabled={pendingRetryId === j.job_id || pendingAction}
                            className="p-1 rounded-sm border border-border bg-card hover:bg-muted text-foreground transition-colors press-feedback disabled:opacity-40"
                            title={t('rerun')}
                          >
                            <RotateCcw className="w-3.5 h-3.5" />
                          </button>
                        )}
                        {(j.status === 'queued' ||
                          j.status === 'running' ||
                          j.status === 'retrying' ||
                          j.status === 'paused') && (
                          <button
                            onClick={() => handlePauseResume(j)}
                            disabled={pendingAction}
                            className="p-1 rounded-sm border border-border bg-card hover:bg-muted text-foreground transition-colors press-feedback disabled:opacity-40"
                            title={j.status === 'paused' ? t('resume') : t('pause')}
                          >
                            {j.status === 'paused' ? (
                              <Play className="w-3.5 h-3.5" />
                            ) : (
                              <Pause className="w-3.5 h-3.5" />
                            )}
                          </button>
                        )}
                        <button
                          onClick={() => setConfirmDelete(j)}
                          disabled={pendingAction}
                          className="p-1 rounded-sm border border-border bg-card hover:bg-rose-50 dark:hover:bg-rose-950/40 text-muted-foreground hover:text-rose-600 transition-colors press-feedback disabled:opacity-40"
                          title={t('delete')}
                        >
                          <Trash2 className="w-3.5 h-3.5" />
                        </button>
                      </div>
                    </td>
                  </tr>
                )
              })
            )}
          </tbody>
        </table>

        {/* Loading Overlay */}
        {isLoading && (
          <div className="absolute inset-0 z-10 flex items-center justify-center bg-background/50 backdrop-blur-2xs">
            <div className="flex items-center gap-2 px-3 py-1.5 rounded-md bg-card border border-border shadow-xs text-xs font-mono">
              <span className="w-3 h-3 rounded-full border-2 border-yamibo-burgundy border-t-transparent animate-spin" />
              <span>{t('loading')}</span>
            </div>
          </div>
        )}
      </div>

      {/* Pagination Controls */}
      <PaginationControls
        page={page}
        totalPages={totalPages}
        onPageChange={setPage}
        scrollTargetId="jobs-pagination-top"
      />

      {/* ─── Delete Single Job Dialog ─── */}
      {confirmDelete && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50 backdrop-blur-2xs">
          <div className="bg-card border border-border rounded-md shadow-lg max-w-md w-full p-5 space-y-3 animate-in fade-in-50 zoom-in-95 duration-150">
            <h2 className="text-base font-semibold text-foreground font-sans">
              {t('confirm_delete')}
            </h2>
            <p className="text-xs text-muted-foreground font-sans leading-relaxed">
              {confirmDelete.status === 'running'
                ? t('delete_running_job_warning')
                : t('delete_job_confirm')}
            </p>
            <div className="p-2.5 rounded-sm bg-muted/60 border border-border/70 text-xs font-mono text-foreground line-clamp-2">
              {desc(confirmDelete)}
            </div>
            {deleteError && (
              <p className="text-xs font-mono text-rose-600 dark:text-rose-400">{deleteError}</p>
            )}
            <div className="flex justify-end gap-2 pt-2">
              <button
                onClick={() => {
                  setConfirmDelete(null)
                  setDeleteError(null)
                }}
                className="px-3 py-1.5 rounded-sm border border-border bg-card hover:bg-muted text-xs font-sans font-medium transition-colors"
              >
                {t('cancel')}
              </button>
              <button
                onClick={() => void confirmDoDelete()}
                disabled={pendingAction}
                className="px-3 py-1.5 rounded-sm bg-rose-600 hover:bg-rose-700 text-white text-xs font-sans font-medium transition-colors press-feedback disabled:opacity-50"
              >
                {t('delete')}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ─── Batch Delete Confirmation Dialog ─── */}
      {confirmBatchDelete && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50 backdrop-blur-2xs">
          <div className="bg-card border border-border rounded-md shadow-lg max-w-md w-full p-5 space-y-3 animate-in fade-in-50 zoom-in-95 duration-150">
            <h2 className="text-base font-semibold text-foreground font-sans">
              {t('confirm_batch_delete')}
            </h2>
            <p className="text-xs text-muted-foreground font-sans leading-relaxed">
              {t('confirm_batch_delete_desc', { status: t(confirmBatchDelete) })}
            </p>
            {deleteError && (
              <p className="text-xs font-mono text-rose-600 dark:text-rose-400">{deleteError}</p>
            )}
            <div className="flex justify-end gap-2 pt-2">
              <button
                onClick={() => {
                  setConfirmBatchDelete(null)
                  setDeleteError(null)
                }}
                className="px-3 py-1.5 rounded-sm border border-border bg-card hover:bg-muted text-xs font-sans font-medium transition-colors"
              >
                {t('cancel')}
              </button>
              <button
                onClick={() => void handleBatchDelete()}
                disabled={pendingAction}
                className="px-3 py-1.5 rounded-sm bg-rose-600 hover:bg-rose-700 text-white text-xs font-sans font-medium transition-colors press-feedback disabled:opacity-50"
              >
                {t('confirm_batch_delete')}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ─── Selected Delete Confirmation Dialog ─── */}
      {confirmSelectedDelete && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50 backdrop-blur-2xs">
          <div className="bg-card border border-border rounded-md shadow-lg max-w-md w-full p-5 space-y-3 animate-in fade-in-50 zoom-in-95 duration-150">
            <h2 className="text-base font-semibold text-foreground font-sans">
              {t('confirm_delete')}
            </h2>
            <p className="text-xs text-muted-foreground font-sans leading-relaxed">
              {tx(`确定要删除已选中的 ${selectedIds.size} 项任务吗？此操作无法撤销。`, `Delete ${selectedIds.size} selected tasks? This action cannot be undone.`)}
            </p>
            {deleteError && (
              <p className="text-xs font-mono text-rose-600 dark:text-rose-400">{deleteError}</p>
            )}
            <div className="flex justify-end gap-2 pt-2">
              <button
                onClick={() => {
                  setConfirmSelectedDelete(false)
                  setDeleteError(null)
                }}
                className="px-3 py-1.5 rounded-sm border border-border bg-card hover:bg-muted text-xs font-sans font-medium transition-colors"
              >
                {t('cancel')}
              </button>
              <button
                onClick={() => void handleSelectedDelete()}
                disabled={pendingAction}
                className="px-3 py-1.5 rounded-sm bg-rose-600 hover:bg-rose-700 text-white text-xs font-sans font-medium transition-colors press-feedback disabled:opacity-50"
              >
                {t('delete')}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
