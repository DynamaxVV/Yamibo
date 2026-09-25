import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import {
  ArrowLeft,
  RotateCcw,
  Pause,
  Play,
  RefreshCw,
  AlertTriangle,
  Clock,
  Layers,
  FileCode,
  ListOrdered,
} from 'lucide-react'
import { api, type JobSummary, type JobEvent } from '../api/client'
import { Badge } from '../components/Badge'
import { LoadingIndicator } from '../components/LoadingIndicator'
import { useI18n } from '../context/I18nContext'
import { formatDateTime } from '../utils/time'
import { getArchiveBreakdown, getPartialArchiveReason, hasPartialArchiveBreakdown } from '../utils/archiveSummary'
import { formatJobErrorMessage, formatJobFailureKind, getJobFailureKind, renderBbsLinks } from '../utils/jobMessages'

const asRecord = (value: unknown): Record<string, unknown> =>
  value !== null && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}

export function JobDetail() {
  const { t, lang, tx } = useI18n()
  const navigate = useNavigate()
  const desc = (j: JobSummary) =>
    j.job_type === 'image_backfill' && j.tid ? (lang === 'en' ? `Recover images for #${j.tid}` : `为 #${j.tid} 补全图片`) : lang === 'en' ? j.description_en : j.description
  const id = window.location.pathname.split('/').pop() || ''
  const [job, setJob] = useState<JobSummary | null>(null)
  const [events, setEvents] = useState<JobEvent[]>([])
  const [eventFilter, setEventFilter] = useState('all')
  const [eventLimit, setEventLimit] = useState(20)
  const [eventError, setEventError] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [refreshNotice, setRefreshNotice] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [rerunSubmitting, setRerunSubmitting] = useState(false)
  const [resyncSubmitting, setResyncSubmitting] = useState(false)
  const jobRef = useRef<JobSummary | null>(null)

  useEffect(() => {
    let active = true
    setError(null)
    setEvents([])
    setEventLimit(20)
    setEventError(null)
    api
      .job(id)
      .then(next => {
        if (!active) return
        setJob(next)
        jobRef.current = next
      })
      .catch(e => {
        if (active) setError(e.message)
      })
    api
      .jobEvents(id, { order: 'desc' })
      .then(next => {
        if (!active) return
        setEvents(next)
      })
      .catch(e => { if (active) setEventError(e.message) })
    return () => {
      active = false
    }
  }, [id])

  useEffect(() => {
    let active = true
    const poll = window.setInterval(async () => {
      try {
        const next = await api.job(id)
        if (!active) return
        const prev = jobRef.current
        if (
          prev &&
          (prev.status !== next.status ||
            prev.stage !== next.stage ||
            prev.updated_at !== next.updated_at)
        ) {
          setRefreshNotice(tx(`${t('job_status_updated')} ${t(next.status)}。`, `${t('job_status_updated')} ${t(next.status)}.`))
        }
        jobRef.current = next
        setJob(next)
        try {
          const nextEvents = await api.jobEvents(id, { order: 'desc' })
          if (active) { setEvents(nextEvents); setEventError(null) }
        } catch (e: any) { if (active) setEventError(e.message) }
      } catch {}
    }, 4000)
    return () => {
      active = false
      window.clearInterval(poll)
    }
  }, [id, t])

  if (error) {
    return (
      <div className="p-4 rounded-md bg-rose-50 border border-rose-200 text-rose-800 dark:bg-rose-950/40 dark:border-rose-900/60 dark:text-rose-300 font-mono text-sm">
        {error}
      </div>
    )
  }

  if (!job) {
    return (
      <LoadingIndicator label={t('loading')} className="min-h-24" />
    )
  }

  const isImageBackfill = job.job_type === 'image_backfill'
  const remoteImageCount = job.artifacts?.remote_image_count
  const applyNeedFetchCount = job.artifacts?.apply_need_fetch_count
  const canRerun = job.status === 'partial' || job.status === 'failed' || job.status === 'interrupted'
  const canResync =
    job.status === 'failed' &&
    isImageBackfill &&
    job.stage === 'load_local' &&
    !!job.tid &&
    (job.error_message || '').startsWith('local floor sequence is invalid; full resync required')

  const sortedEvents = [...events].sort((a, b) => b.event_id - a.event_id)
  const filteredEvents = sortedEvents.filter(e => eventFilter === 'all' || (eventFilter === 'failure'
    ? /failed|error|partial|interrupted/i.test(`${e.event_type} ${e.status}`)
    : /status|queued|started|running|paused|resumed|succeeded|completed|failed|interrupted/i.test(e.event_type)))
  const visibleEvents = filteredEvents.slice(0, eventLimit)

  const payloadEntries = Object.entries(job.payload || {})
  const artifactEntries = Object.entries(job.artifacts || {})
  const failureContext = (job.artifacts?.failure_context as Record<string, unknown> | undefined) || null
  const remoteFetch = (failureContext?.remote_fetch as Record<string, unknown> | undefined) || null
  const failureKind = job.failure_kind || getJobFailureKind(job)
  const imageFailureGuidance = isImageBackfill ? (
    failureKind === 'image_http_404' ? tx('旧图片地址返回 404。系统会核对帖子当前地址；若地址仍无效，需确认源站图片状态。', 'The image URL returned 404. The system checks the current thread URL; if it still fails, verify the source image.') :
    failureKind === 'image_upstream_throttled' ? tx('源站限流或暂不可用。等待退避后重试，避免立即重复请求。', 'The source is rate limited or temporarily unavailable. Retry after the delay.') :
    failureKind === 'image_validation_failed' ? tx('已收到图片响应，但文件未通过校验；不能当作已归档图片。', 'An image response arrived but failed validation and was not archived.') :
    failureKind === 'image_truncated_transfer' ? tx('收到的字节少于响应声明长度，可在稍后重试或续传。', 'Fewer bytes arrived than the response declared. Retry or resume the transfer later.') : null
  ) : null
  const archiveBreakdown = getArchiveBreakdown(null, job.artifacts)
  const showPartialSummary =
    (job.status === 'partial' || job.artifacts?.archive_status === 'partial') &&
    hasPartialArchiveBreakdown(archiveBreakdown)
  const downloadedCount = Number(job.artifacts?.downloaded_image_count || 0)
  const nonExportCount = Number(job.artifacts?.non_export_image_count || 0)
  const sharedCount = Number(job.artifacts?.shared_image_count || 0)
  const skippedCount = Number(job.artifacts?.skipped_image_count || 0)
  const missingCount = Number(job.artifacts?.missing_image_count || 0)
  const missingSharedCount = Number(job.artifacts?.missing_shared_image_count || 0)
  const imageSummary = asRecord(job.artifacts?.image_download_summary)
  const imageDiagnostics = Array.isArray(job.artifacts?.image_download_diagnostics)
    ? job.artifacts.image_download_diagnostics.map(asRecord)
    : []
  const failedImages = imageDiagnostics.filter(item => item.status !== 'ok')
  const refreshEvent = sortedEvents.find(event => event.event_type === 'image.url_refresh_requested')
  const imageUrlRefresh = Object.keys(asRecord(job.artifacts?.image_url_refresh)).length > 0
    ? asRecord(job.artifacts?.image_url_refresh)
    : asRecord(refreshEvent?.payload)
  const refreshInitial = Array.isArray(imageUrlRefresh.initial_diagnostics)
    ? imageUrlRefresh.initial_diagnostics.map(asRecord)
    : []
  const diagnosticValue = (value: unknown) => value === null || value === undefined || value === '' ? '—' : String(value)

  const renderValue = (value: unknown) => {
    if (value == null || value === '') return '-'
    if (typeof value === 'object') {
      return (
        <pre className="m-0 p-2 rounded-sm bg-muted/50 border border-border/60 text-xs font-mono whitespace-pre-wrap break-all">
          {renderBbsLinks(JSON.stringify(value, null, 2))}
        </pre>
      )
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
      setRefreshNotice(tx(`任务已提交：${result.job_id}`, `Task queued: ${result.job_id}`))
      navigate(`/jobs/${result.job_id}`)
    } catch (e: any) {
      setActionError(e.message || String(e))
    } finally {
      setRerunSubmitting(false)
    }
  }

  const handleResync = async () => {
    setActionError(null)
    setResyncSubmitting(true)
    try {
      const result = await api.resyncFailedJob(job.job_id)
      setEvents([])
      setJob(null)
      jobRef.current = null
      setRefreshNotice(tx(`任务已提交：${result.job_id}`, `Task queued: ${result.job_id}`))
      navigate(`/jobs/${result.job_id}`)
    } catch (e: any) {
      setActionError(e.message || String(e))
    } finally {
      setResyncSubmitting(false)
    }
  }

  return (
    <div className="space-y-6">
      {/* ─── Top Navigation & Action Header ─── */}
      <div className="flex flex-wrap items-center justify-between gap-4 pb-4 border-b border-border">
        <div className="flex items-start gap-3 min-w-0">
          <Link
            to="/jobs"
            className="p-1.5 rounded-md border border-border bg-card hover:bg-muted text-muted-foreground hover:text-foreground transition-colors press-feedback"
            title={t('job_list')}
          >
            <ArrowLeft className="w-4 h-4" />
          </Link>
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-mono text-xs text-muted-foreground break-all">{job.job_id}</span>
              <Badge status={job.archive_status || job.status} />
              {failureKind && ['retrying', 'failed', 'partial', 'interrupted'].includes(job.status) && (
                <span className="text-xs font-mono px-2 py-0.5 rounded-sm bg-muted text-muted-foreground border border-border">
                  {formatJobFailureKind(failureKind, lang)}
                </span>
              )}
            </div>
            <h1 className="text-2xl break-words font-semibold text-foreground tracking-tight mt-0.5">
              {desc(job)}
            </h1>
          </div>
        </div>

        {/* Action Controls */}
        <div className="flex items-center gap-2">
          {canRerun && !canResync && (
            <button
              onClick={() => void handleRerun()}
              disabled={rerunSubmitting}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium font-sans bg-yamibo-burgundy hover:bg-yamibo-burgundy-hover text-white transition-colors press-feedback disabled:opacity-50"
            >
              <RotateCcw className="w-3.5 h-3.5" />
              <span>{rerunSubmitting ? t('running') : t('rerun')}</span>
            </button>
          )}

          {(job.status === 'queued' ||
            job.status === 'running' ||
            job.status === 'retrying' ||
            job.status === 'paused') && (
            <button
              onClick={async () => {
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
              }}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium font-sans border border-border bg-card hover:bg-muted text-foreground transition-colors press-feedback"
            >
              {job.status === 'paused' ? <Play className="w-3.5 h-3.5" /> : <Pause className="w-3.5 h-3.5" />}
              <span>{job.status === 'paused' ? t('resume') : t('pause')}</span>
            </button>
          )}
        </div>
      </div>

      {/* ─── Resync Notice Banner ─── */}
      {canResync && (
        <div className="p-4 rounded-md bg-amber-50 border border-amber-200 dark:bg-amber-950/40 dark:border-amber-900/60 space-y-2">
          <p className="text-xs text-amber-900 dark:text-amber-200">
            {lang === 'en'
              ? 'Local floor numbering is invalid. Queue a full thread sync and clear all failed jobs for this thread.'
              : tx('本地楼层编号异常，需要重新同步帖子。提交成功后将清除同帖子的所有失败任务，由后台执行同步。', 'Local floor numbering is invalid. A successful resync will clear all failed tasks for this thread and queue a full sync.')}
          </p>
          <button
            onClick={() => void handleResync()}
            disabled={resyncSubmitting}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium font-sans bg-amber-600 hover:bg-amber-700 text-white transition-colors press-feedback disabled:opacity-50"
          >
            <RefreshCw className="w-3.5 h-3.5" />
            <span>{resyncSubmitting ? t('loading') : tx('重新同步帖子', 'Resync thread')}</span>
          </button>
        </div>
      )}

      {/* Action Error Banner */}
      {actionError && (
        <div className="p-3 rounded-md bg-rose-50 border border-rose-200 text-rose-800 dark:bg-rose-950/40 dark:border-rose-900/60 dark:text-rose-300 text-xs font-mono">
          {actionError}
        </div>
      )}

      {/* Status Live Notification */}
      {refreshNotice && (
        <div className="flex items-center justify-between p-3 rounded-md bg-muted/60 border border-border text-xs font-mono">
          <span>{refreshNotice}</span>
          <button
            onClick={() => setRefreshNotice(null)}
            className="text-muted-foreground hover:text-foreground"
          >
            {t('dismiss')}
          </button>
        </div>
      )}

      <div className="flex flex-wrap gap-x-6 gap-y-2 text-sm text-muted-foreground">
        <span>{t('progress')}：{job.progress_current} / {job.progress_total ?? '?'}</span>
        <span>{t('updated')}：{formatDateTime(job.updated_at)}</span>
        {job.tid && <Link className="underline" to={`/threads/${job.tid}`}>{tx('关联帖子', 'Related thread')} #{job.tid}</Link>}
      </div>
        {/* Error message detail if exists */}
        {(job.error_message || job.active_error?.message) && (
          <div className="p-4 rounded-md bg-rose-50 border border-rose-200 dark:bg-rose-950/40 dark:border-rose-900/60 text-sm text-rose-800 dark:text-rose-300">
            <div className="font-semibold font-sans mb-1 flex items-center gap-1.5">
              <AlertTriangle className="w-3.5 h-3.5" />
              <span>{formatJobFailureKind(failureKind, lang) || tx('任务受阻', 'Task blocked')}</span>
            </div>
            <p className="mb-2">{tx('发生阶段', 'Stage')}: {job.stage || '—'} · {imageFailureGuidance || (canResync ? tx('请重新同步帖子', 'Resync the thread.') : job.status === 'paused' ? tx('任务已暂停。确认服务及远端资源恢复后，可点击上方“继续”。', 'The task is paused. Resume it above after the service and remote resources recover.') : canRerun ? tx('可通过上方重试操作创建新任务；若重复失败，请展开技术原文排查。', 'Use Retry above to create a new task. If it fails again, expand the technical details.') : tx('请查看最近事件，确认服务和远端资源状态。', 'Check recent events and verify the service and remote resource status.'))}</p>
            <details><summary className="cursor-pointer font-medium">{lang === 'en' ? 'Technical details' : '技术原文'}</summary><div className="whitespace-pre-wrap break-all mt-2 font-mono text-xs">
              {job.active_error?.message || job.error_message}
            </div></details>
          </div>
        )}
      {/* ─── Job Details Parameters Card ─── */}
      <details className="p-5 bg-card border border-border rounded-md shadow-2xs space-y-4"><summary className="cursor-pointer font-medium">{tx('任务详细属性', 'Task properties')}</summary>
        <h2 className="text-xs font-mono uppercase tracking-wider text-muted-foreground font-semibold flex items-center gap-2">
          <Layers className="w-3.5 h-3.5 text-yamibo-burgundy dark:text-yamibo-coral" />
          <span>{tx('任务核心属性', 'Job specification')}</span>
        </h2>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-x-8 gap-y-3 text-xs divide-y md:divide-y-0 divide-border/60">
          <div className="flex justify-between gap-3 break-words py-1.5 border-b border-border/50">
            <span className="text-muted-foreground font-sans">{t('tid')}</span>
            <span className="font-mono text-foreground">
              {job.tid ? (
                <Link to={`/threads/${job.tid}`} className="hover:text-yamibo-burgundy dark:hover:text-yamibo-coral font-medium">
                  #{job.tid}
                </Link>
              ) : (
                '-'
              )}
            </span>
          </div>

          <div className="flex justify-between gap-3 break-words py-1.5 border-b border-border/50">
            <span className="text-muted-foreground font-sans">{t('stage')}</span>
            <span className="font-mono text-foreground">{job.stage || '-'}</span>
          </div>

          <div className="flex justify-between gap-3 break-words py-1.5 border-b border-border/50">
            <span className="text-muted-foreground font-sans">{t('progress')}</span>
            <span className="font-mono text-foreground font-semibold">
              {job.progress_current} / {job.progress_total ?? '?'}
            </span>
          </div>

          <div className="flex justify-between gap-3 break-words py-1.5 border-b border-border/50">
            <span className="text-muted-foreground font-sans">{t('worker')}</span>
            <span className="font-mono text-foreground">{job.worker_id || '-'}</span>
          </div>

          <div className="flex justify-between gap-3 break-words py-1.5 border-b border-border/50">
            <span className="text-muted-foreground font-sans">{t('created_at')}</span>
            <span className="font-mono text-muted-foreground">{formatDateTime(job.created_at)}</span>
          </div>

          <div className="flex justify-between gap-3 break-words py-1.5 border-b border-border/50">
            <span className="text-muted-foreground font-sans">{t('updated')}</span>
            <span className="font-mono text-muted-foreground">{formatDateTime(job.updated_at)}</span>
          </div>

          <div className="flex justify-between gap-3 break-words py-1.5 border-b border-border/50">
            <span className="text-muted-foreground font-sans">{t('finished_at')}</span>
            <span className="font-mono text-muted-foreground">{formatDateTime(job.finished_at)}</span>
          </div>

          <div className="flex justify-between gap-3 break-words py-1.5 border-b border-border/50">
            <span className="text-muted-foreground font-sans">{tx('重试状态', 'Retry count')}</span>
            <span className="font-mono text-foreground">
              {job.retry_count} / {job.max_retries}
            </span>
          </div>

          {job.url && (
            <div className="col-span-full flex items-center justify-between py-1.5 border-b border-border/50">
              <span className="text-muted-foreground font-sans">{t('original_url')}</span>
              <a
                href={job.url}
                target="_blank"
                rel="noreferrer"
                className="font-mono text-xs text-yamibo-burgundy dark:text-yamibo-coral hover:underline break-all min-w-0 text-right"
              >
                {job.url}
              </a>
            </div>
          )}

          {isImageBackfill && (
            <>
              <div className="flex justify-between gap-3 break-words py-1.5 border-b border-border/50">
                <span className="text-muted-foreground font-sans">{tx('远程图片数', 'Remote images')}</span>
                <span className="font-mono text-foreground">{remoteImageCount != null ? String(remoteImageCount) : '-'}</span>
              </div>
              <div className="flex justify-between gap-3 break-words py-1.5 border-b border-border/50">
                <span className="text-muted-foreground font-sans">{tx('待回填图片', 'Images pending recovery')}</span>
                <span className="font-mono text-foreground font-bold text-yamibo-burgundy dark:text-yamibo-coral">
                  {applyNeedFetchCount != null ? String(applyNeedFetchCount) : '-'}
                </span>
              </div>
            </>
          )}
        </div>


      </details>

      {/* ─── Failure Diagnostics (if failure context exists) ─── */}
      {failureContext && (
        <details className="p-5 bg-card border border-border rounded-md shadow-2xs space-y-3"><summary className="cursor-pointer font-medium">{lang === 'en' ? 'Failure diagnostics' : '失败诊断详情'}</summary>
          <h2 className="text-xs font-mono uppercase tracking-wider text-rose-700 dark:text-rose-400 font-semibold flex items-center gap-2">
            <AlertTriangle className="w-3.5 h-3.5" />
            <span>{tx('失败诊断', 'Failure diagnostics')}</span>
          </h2>
          <div className="space-y-2 text-xs font-mono">
            <div className="p-2 rounded-sm bg-muted/50 border border-border/60">
              <span className="text-muted-foreground block text-xs">EXCEPTION TYPE</span>
              <span className="text-foreground">{renderValue(failureContext.exception_type)}</span>
            </div>
            <div className="p-2 rounded-sm bg-muted/50 border border-border/60">
              <span className="text-muted-foreground block text-xs">MESSAGE</span>
              <span className="text-foreground">{renderValue(failureContext.message)}</span>
            </div>
            {remoteFetch && (
              <div className="p-2 rounded-sm bg-muted/50 border border-border/60">
                <span className="text-muted-foreground block text-xs">REMOTE FETCH</span>
                <span className="text-foreground">{renderValue(remoteFetch)}</span>
              </div>
            )}
          </div>
        </details>
      )}

      {/* The final download and the preceding stored-URL attempt are separate evidence. */}
      {isImageBackfill && (imageDiagnostics.length > 0 || refreshInitial.length > 0) && (
        <section className="p-5 bg-card border border-border rounded-md shadow-2xs space-y-4">
          <h2 className="text-sm font-semibold text-foreground">{tx('图片下载诊断', 'Image download diagnostics')}</h2>
          {imageDiagnostics.length > 0 && (
            <p className="text-xs text-muted-foreground">
              {tx('本次尝试', 'This attempt')}: {diagnosticValue(imageSummary.attempted)} ·
              {' '}{tx('成功', 'Succeeded')} {diagnosticValue(imageSummary.succeeded)} ·
              {' '}{tx('失败', 'Failed')} {diagnosticValue(imageSummary.failed)} ·
              {' '}{tx('可重试', 'Retryable')} {diagnosticValue(imageSummary.retryable_failed)}
              {imageSummary.stopped_reason ? ` · ${tx('停止原因', 'Stopped')}: ${diagnosticValue(imageSummary.stopped_reason)}` : ''}
            </p>
          )}
          {refreshInitial.length > 0 && (
            <div className="rounded-md border border-amber-300/60 bg-amber-50/50 dark:bg-amber-950/20 p-3 space-y-2 text-xs">
              <p className="font-medium">{tx('旧地址请求与地址刷新', 'Stored URL attempt and refresh')} · {diagnosticValue(imageUrlRefresh.resolution)} · {tx('抓取页面', 'Pages fetched')} {diagnosticValue(imageUrlRefresh.pages_fetched)}</p>
              {refreshInitial.map((item, index) => (
                <p key={index} className="font-mono break-all text-muted-foreground">
                  {diagnosticValue(item.url_host)}{diagnosticValue(item.url_path)}{item.remote_identity ? ` #${diagnosticValue(item.remote_identity)}` : ''} · HTTP {diagnosticValue(item.http_status)} · {diagnosticValue(item.error_type)}
                </p>
              ))}
            </div>
          )}
          {failedImages.length > 0 && (
            <div className="space-y-2">
              <h3 className="text-xs font-semibold">{tx('失败图片', 'Failed images')} ({failedImages.length})</h3>
              {failedImages.map((item, index) => (
                <details key={index} className="rounded-md border border-border p-3 text-xs">
                  <summary className="cursor-pointer break-all font-mono">
                    {diagnosticValue(item.url_host)}{diagnosticValue(item.url_path)}{item.remote_identity ? ` #${diagnosticValue(item.remote_identity)}` : ''} · HTTP {diagnosticValue(item.http_status)} · {diagnosticValue(item.error_type)}
                  </summary>
                  <div className="mt-3 grid grid-cols-1 sm:grid-cols-2 gap-2 text-muted-foreground">
                    <span>{tx('响应字节', 'Bytes received')}: {diagnosticValue(item.bytes)} / {diagnosticValue(item.content_length)} · {tx('长度匹配', 'Length matches')}: {diagnosticValue(item.content_length_matches)}</span>
                    <span>{tx('响应类型', 'Content type')}: {diagnosticValue(item.content_type)} · {tx('格式', 'Format')}: {diagnosticValue(item.body_format)}</span>
                    <span>{tx('请求次数', 'Attempts')}: {diagnosticValue(item.attempts)} · {tx('耗时', 'Duration')}: {diagnosticValue(item.duration_ms)} ms · {tx('可重试', 'Retryable')}: {diagnosticValue(item.retryable)}</span>
                    <span>Range: {diagnosticValue(item.range_attempted)} / {diagnosticValue(item.range_recovered)} · Content-Range: {diagnosticValue(item.content_range)}</span>
                    <span>SHA-256: {diagnosticValue(item.body_sha256)}</span>
                    <span className="sm:col-span-2 break-all">{tx('错误', 'Error')}: {diagnosticValue(item.error_message)}</span>
                    {Array.isArray(item.attempt_history) && item.attempt_history.length > 0 && (
                      <div className="sm:col-span-2 break-all">{tx('逐次请求', 'Attempt history')}: {renderValue(item.attempt_history)}</div>
                    )}
                  </div>
                </details>
              ))}
            </div>
          )}
        </section>
      )}

      {/* ─── Partial Archive Breakdown ─── */}
      {showPartialSummary && archiveBreakdown && (
        <div className="p-5 bg-card border border-border rounded-md shadow-2xs space-y-3">
          <h2 className="text-xs font-mono uppercase tracking-wider text-amber-700 dark:text-amber-400 font-semibold">
            {t('archive_partial_detail')}
          </h2>
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3 text-xs font-mono">
            <div className="p-2 rounded-sm bg-muted/40 border border-border/60">
              <span className="text-muted-foreground block text-xs">{t('archive_success')}</span>
              <span className="font-semibold text-foreground">
                {downloadedCount + nonExportCount + sharedCount + skippedCount}
              </span>
            </div>
            <div className="p-2 rounded-sm bg-muted/40 border border-border/60">
              <span className="text-muted-foreground block text-xs">{t('archive_failed')}</span>
              <span className="font-semibold text-rose-600 dark:text-rose-400">
                {missingCount + missingSharedCount}
              </span>
            </div>
            <div className="p-2 rounded-sm bg-muted/40 border border-border/60">
              <span className="text-muted-foreground block text-xs">{t('downloaded')}</span>
              <span className="font-semibold text-foreground">{downloadedCount}</span>
            </div>
            <div className="p-2 rounded-sm bg-muted/40 border border-border/60">
              <span className="text-muted-foreground block text-xs">{t('downloaded_shared')}</span>
              <span className="font-semibold text-foreground">{sharedCount}</span>
            </div>
            <div className="p-2 rounded-sm bg-muted/40 border border-border/60">
              <span className="text-muted-foreground block text-xs">{t('non_export')}</span>
              <span className="font-semibold text-foreground">{nonExportCount}</span>
            </div>
            <div className="p-2 rounded-sm bg-muted/40 border border-border/60">
              <span className="text-muted-foreground block text-xs">{t('skipped')}</span>
              <span className="font-semibold text-foreground">{skippedCount}</span>
            </div>
          </div>
          <p className="text-xs text-muted-foreground font-sans">
            {getPartialArchiveReason(archiveBreakdown, lang)}
          </p>
        </div>
      )}

      {/* ─── Event Timeline ─── */}
      {(events.length > 0 || eventError) && (
        <div className="p-5 bg-card border border-border rounded-md shadow-2xs space-y-4">
          <div className="flex items-center justify-between border-b border-border pb-2.5">
            <h2 className="text-xs font-mono uppercase tracking-wider text-muted-foreground font-semibold flex items-center gap-2">
              <ListOrdered className="w-3.5 h-3.5 text-yamibo-burgundy dark:text-yamibo-coral" />
              <span>{t('event_timeline')}</span>
            </h2>
          <span className="text-xs font-mono text-muted-foreground">{tx(`最近 ${events.length} 条事件`, `${events.length} recent events`)}</span>
          </div>

          {eventError && <p role="alert" className="text-sm text-rose-800 dark:text-rose-300 break-words">{tx('事件加载失败：', 'Unable to load events: ')}{eventError}</p>}
          <div className="flex flex-wrap items-center gap-3 text-sm">
            <label>{tx('事件筛选', 'Event filter')} <select value={eventFilter} onChange={e => { setEventFilter(e.target.value); setEventLimit(20) }}><option value="all">{tx('全部', 'All')}</option><option value="failure">{tx('失败 / 异常', 'Failures')}</option><option value="status">{tx('状态变更', 'Status changes')}</option></select></label>
            <span className="text-muted-foreground">{tx('最近优先', 'Newest first')} · {visibleEvents.length} / {filteredEvents.length} {tx('条', 'events')}</span>
          </div>
          {filteredEvents.length === 0 && <p className="text-sm text-muted-foreground">{tx('没有符合筛选的事件。', 'No events match this filter.')}</p>}
          <div className="relative pl-6 space-y-6 before:absolute before:left-2 before:top-2 before:bottom-2 before:w-[1px] before:bg-border">
            {visibleEvents.map(e => (
              <div key={e.event_id} className="relative group">
                <span className="absolute -left-5 top-1 w-2 h-2 rounded-full bg-border group-hover:bg-yamibo-burgundy dark:group-hover:bg-yamibo-coral transition-colors" />
                <div className="flex flex-wrap items-center gap-2 text-xs font-mono">
                  <Badge status={e.status} />
                  <span className="font-bold text-foreground">{e.event_type}</span>
                  {e.stage && <span className="text-muted-foreground">[{e.stage}]</span>}
                  <span className="text-muted-foreground/80 text-xs ml-auto flex items-center gap-1">
                    <Clock className="w-3 h-3" />
                    <span>{formatDateTime(e.created_at)}</span>
                  </span>
                </div>
                {e.payload && (
                  <details className="mt-2 text-xs break-all"><summary className="cursor-pointer text-muted-foreground">{tx('事件负载', 'Event payload')}</summary>{renderValue(e.payload)}</details>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {filteredEvents.length > eventLimit && <button className="btn-subtle" onClick={() => setEventLimit(n => n + 20)}>{tx('再显示 20 条事件', 'Show 20 more events')}</button>}
      {/* ─── Raw Payload & Artifacts ─── */}
      {(payloadEntries.length > 0 || artifactEntries.length > 0) && (
        <details className="p-4 bg-card border border-border rounded-md"><summary className="cursor-pointer font-medium">{tx('技术属性与任务负载', 'Technical properties and task payload')}</summary><div className="grid grid-cols-1 lg:grid-cols-2 gap-4 mt-4">
          {payloadEntries.length > 0 && (
            <div className="p-4 bg-card border border-border rounded-md shadow-2xs space-y-2">
              <h3 className="text-xs font-mono uppercase tracking-wider text-muted-foreground font-semibold flex items-center gap-1.5">
                <FileCode className="w-3.5 h-3.5" />
                <span>{t('payload')}</span>
              </h3>
              <div className="overflow-x-auto rounded-sm border border-border/70 text-xs font-mono divide-y divide-border/60">
                {payloadEntries.map(([k, v]) => (
                  <div key={k} className="p-2 flex justify-between gap-4">
                    <span className="text-muted-foreground shrink-0">{k}</span>
                    <span className="text-foreground text-right break-all">{renderValue(v)}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {artifactEntries.length > 0 && (
            <div className="p-4 bg-card border border-border rounded-md shadow-2xs space-y-2">
              <h3 className="text-xs font-mono uppercase tracking-wider text-muted-foreground font-semibold flex items-center gap-1.5">
                <FileCode className="w-3.5 h-3.5" />
                <span>{t('artifacts')}</span>
              </h3>
              <div className="overflow-x-auto rounded-sm border border-border/70 text-xs font-mono divide-y divide-border/60">
                {artifactEntries.map(([k, v]) => (
                  <div key={k} className="p-2 flex justify-between gap-4">
                    <span className="text-muted-foreground shrink-0">{k}</span>
                    <span className="text-foreground text-right break-all">{renderValue(v)}</span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div></details>
      )}
    </div>
  )
}
