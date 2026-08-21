import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate, useLocation, useSearchParams } from 'react-router-dom'
import { api, type ThreadDetail as ThreadDetailType, type ContentBlock, type ThreadImage, type JobSummary } from '../api/client'
import { Badge, ContentBadge } from '../components/Badge'
import { ThreadReader } from '../components/ThreadReader'
import { useI18n } from '../context/I18nContext'
import { formatDateTime } from '../utils/time'
import { getArchiveBreakdown, getPartialArchiveReason, hasPartialArchiveBreakdown } from '../utils/archiveSummary'
import { formatJobErrorMessage, renderBbsLinks } from '../utils/jobMessages'

const FORUM_NAMES: Record<number, string> = {}

const JOB_STAGE_TRACKS: Record<string, string[]> = {
  sync_thread: ['acquired', 'parse', 'staging', 'validate', 'download_images', 'db_commit', 'materialize'],
  export_thread: ['acquired', 'precheck', 'export_write', 'db_commit', 'finalize'],
}

const ACTIVE_JOB_STATUSES = new Set(['queued', 'running', 'retrying', 'paused', 'cancel_requested', 'interrupted'])

const THREAD_DETAIL_CACHE_PREFIX = 'yamibo.thread-detail-cache.v2'

type ThreadDetailCache = {
  thread: ThreadDetailType | null
  blocks: ContentBlock[]
  images: ThreadImage[]
  cached_at: string
}

function loadThreadDetailCache(tid: number, page: number): ThreadDetailCache | null {
  if (typeof window === 'undefined') return null
  try {
    const raw = window.sessionStorage.getItem(`${THREAD_DETAIL_CACHE_PREFIX}:${tid}:${page}`)
    if (!raw) return null
    const parsed = JSON.parse(raw) as Partial<ThreadDetailCache>
    if (!Array.isArray(parsed.blocks) || !Array.isArray(parsed.images)) return null
    return {
      thread: parsed.thread ?? null,
      blocks: parsed.blocks,
      images: parsed.images,
      cached_at: typeof parsed.cached_at === 'string' ? parsed.cached_at : '',
    }
  } catch { return null }
}

function saveThreadDetailCache(tid: number, page: number, data: ThreadDetailCache): void {
  if (typeof window === 'undefined') return
  try { window.sessionStorage.setItem(`${THREAD_DETAIL_CACHE_PREFIX}:${tid}:${page}`, JSON.stringify(data)) } catch { /* ignore */ }
}

function isThreadDetailCacheFresh(cache: ThreadDetailCache, maxAgeMs = 120_000): boolean {
  if (!cache.cached_at) return false
  const cachedAt = Date.parse(cache.cached_at)
  if (!Number.isFinite(cachedAt)) return false
  return Date.now() - cachedAt < maxAgeMs
}

function normalizePreviewPage(page: number): number {
  return Number.isFinite(page) && page > 0 ? Math.floor(page) : 1
}

function getJobStageTrack(jobType: string | null | undefined, stage: string | null | undefined): string[] {
  const preset = jobType ? JOB_STAGE_TRACKS[jobType] : null
  if (preset && preset.length > 0) return preset
  return stage ? [stage] : []
}

function JobProgressDialog({ jobId, title, onClose }: { jobId: string; title: string; onClose: () => void }) {
  const { t, lang } = useI18n()
  const [job, setJob] = useState<JobSummary | null>(null)
  const [events, setEvents] = useState<import('../api/client').JobEvent[]>([])

  useEffect(() => {
    let active = true
    const poll = setInterval(async () => {
      try {
        const [j, es] = await Promise.all([api.job(jobId), api.jobEvents(jobId)])
        if (!active) return
        setJob(j)
        setEvents(es)
        if (j.status === 'succeeded' || j.status === 'failed' || j.status === 'interrupted') {
          clearInterval(poll)
          setTimeout(onClose, j.status === 'succeeded' ? 1500 : 3000)
        }
      } catch { /* ignore */ }
    }, 1500)
    Promise.all([api.job(jobId), api.jobEvents(jobId)]).then(([j, es]) => {
      if (!active) return
      setJob(j)
      setEvents(es)
    }).catch(() => {})
    return () => { active = false; clearInterval(poll) }
  }, [jobId, onClose])

  const progress = job?.progress_total ? Math.min(100, Math.round((job.progress_current / job.progress_total) * 100)) : 0
  const stage = job?.stage || ''
  const statusKey = job?.status || 'running'
  const isError = statusKey === 'failed' || statusKey === 'interrupted'
  const isDone = statusKey === 'succeeded'
  const stageTrack = getJobStageTrack(job?.job_type, job?.stage)
  const currentStageIndex = stageTrack.findIndex(s => s === stage)

  return (
    <div className="confirm-overlay" onClick={e => { if (e.target === e.currentTarget) onClose() }}>
      <div className="confirm-dialog job-progress-dialog" onClick={e => e.stopPropagation()}>
        <div className="job-progress-header">
          <div>
            <h2 style={{ margin: 0, fontSize: 15, color: 'var(--text-primary)', textTransform: 'none', letterSpacing: 0 }}>{title}</h2>
            <div className="job-progress-subtitle">
              <span>{job?.job_type || '-'}</span>
              <span className="job-progress-dot">•</span>
              <span className="mono">{job?.job_id || jobId}</span>
            </div>
          </div>
          <Badge status={job?.status || 'running'} />
        </div>
        <div className="job-progress-grid">
          <section className="job-progress-panel">
            <div className="job-progress-meta">
              <div><span>{t('tid')}</span><strong>{job?.tid ?? '-'}</strong></div>
              <div><span>{t('worker')}</span><strong>{job?.worker_id || '-'}</strong></div>
              <div><span>{t('current_node')}</span><strong>{t(stage || statusKey)}</strong></div>
              <div><span>{t('progress')}</span><strong>{job?.progress_total ? `${job.progress_current}/${job.progress_total}` : '-'}</strong></div>
            </div>
            <div className="job-progress-bar">
              <div className="job-progress-bar-fill" style={{
                width: isError ? '100%' : `${progress || (isDone ? 100 : 0)}%`,
                background: isError ? 'var(--status-error)' : isDone ? 'var(--status-ok, #1a7f37)' : 'var(--accent)',
              }} />
            </div>
            {job?.error_message && isError && (
              <div className="job-progress-error">{renderBbsLinks(formatJobErrorMessage(job.error_code, job.error_message, lang))}</div>
            )}
            <div className="job-stage-track">
              {stageTrack.length > 0 ? stageTrack.map((item, index) => {
                const done = currentStageIndex >= 0 && index < currentStageIndex
                const current = index === currentStageIndex || (!stage && index === stageTrack.length - 1)
                return (
                  <div key={item} className={`job-stage-step${done ? ' done' : ''}${current ? ' current' : ''}`}>
                    <span className="job-stage-index">{index + 1}</span>
                    <span className="job-stage-label">{t(item)}</span>
                  </div>
                )
              }) : (
                <div className="job-stage-step current">
                  <span className="job-stage-index">1</span>
                  <span className="job-stage-label">{t(stage || statusKey)}</span>
                </div>
              )}
            </div>
          </section>
          <section className="job-progress-panel job-progress-events">
            <div className="job-progress-panel-title">{t('job_timeline')}</div>
            <div className="job-event-list">
              {events.length === 0 ? (
                <div className="job-event-empty">{t('loading')}</div>
              ) : events.map(ev => (
                <div key={ev.event_id} className="job-event-item">
                  <div className="job-event-head">
                    <span>{ev.stage || ev.event_type}</span>
                    <span className="job-event-time">{formatDateTime(ev.created_at)}</span>
                  </div>
                  <div className="job-event-body">
                    <span className="job-event-type">{ev.event_type}</span>
                    {ev.status && <Badge status={ev.status} />}
                  </div>
                </div>
              ))}
            </div>
          </section>
        </div>
        <div className="job-progress-footer">
          <div className="job-progress-footer-left"><span>{job?.stage || t(statusKey)}</span></div>
          <button className="btn-subtle" onClick={onClose}>{isDone || isError ? t('close') : t('background')}</button>
        </div>
      </div>
    </div>
  )
}

export function ThreadDetail() {
  const { t, lang } = useI18n()
  const tid = parseInt(window.location.pathname.split('/').pop() || '0')
  const navigate = useNavigate()
  const location = useLocation()
  const [searchParams, setSearchParams] = useSearchParams()
  const previewPageParam = normalizePreviewPage(parseInt(searchParams.get('preview_page') || '1', 10))
  const previewThreadParams = { preview_page: previewPageParam, preview_page_size: 10 }
  const initialCache = loadThreadDetailCache(tid, previewPageParam)
  const [thread, setThread] = useState<ThreadDetailType | null>(() => initialCache?.thread ?? null)
  const [blocks, setBlocks] = useState<ContentBlock[]>(() => initialCache?.blocks ?? [])
  const [images, setImages] = useState<ThreadImage[]>(() => initialCache?.images ?? [])
  const [error, setError] = useState<string | null>(null)
  const [actionLoading, setActionLoading] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [editingChapter, setEditingChapter] = useState(false)
  const [chapterForm, setChapterForm] = useState({ display_title: '', chapter_name: '', chapter_index: '', author_guess: '', group_name: '' })
  const [activeSyncStatus, setActiveSyncStatus] = useState<string | null>(null)
  const [statusRefreshNotice, setStatusRefreshNotice] = useState<string | null>(null)
  const activeSyncStatusRef = useRef<string | null>(null)
  const [activeJob, setActiveJob] = useState<{ jobId: string; title: string } | null>(null)

  useEffect(() => { activeSyncStatusRef.current = activeSyncStatus }, [activeSyncStatus])

  // Fetch thread data
  useEffect(() => {
    const cached = loadThreadDetailCache(tid, previewPageParam)
    if (cached) {
      if (cached.thread) setThread(cached.thread)
      setBlocks(cached.blocks)
      setImages(cached.images)
    }
    let active = true
    const refreshThreadMeta = async () => {
      const [nextThread, fs] = await Promise.all([api.thread(tid, previewThreadParams), api.forums()])
      if (!active) return
      setThread(nextThread)
      fs.forEach(f => { FORUM_NAMES[f.forum_id] = lang === 'en' ? (f.name_en || f.name) : f.name })
      if (!cached) return
      saveThreadDetailCache(tid, previewPageParam, { thread: nextThread, blocks: cached.blocks, images: cached.images, cached_at: new Date().toISOString() })
    }
    const shouldRefreshBody = !cached || !isThreadDetailCacheFresh(cached)
    const refreshThreadBody = async () => {
      const [nextThread, fs] = await Promise.all([api.thread(tid, previewThreadParams), api.forums()])
      if (!active) return
      setThread(nextThread)
      fs.forEach(f => { FORUM_NAMES[f.forum_id] = lang === 'en' ? (f.name_en || f.name) : f.name })
      let nextBlocks: ContentBlock[] = []
      const [fetchedImages, fetchedBlocks] = await Promise.all([
        api.threadImages(tid),
        nextThread.content_kind === 'novel' ? Promise.resolve([] as ContentBlock[]) : api.threadBlocks(tid),
      ])
      if (!active) return
      nextBlocks = fetchedBlocks
      setBlocks(nextBlocks)
      setImages(fetchedImages)
      saveThreadDetailCache(tid, previewPageParam, { thread: nextThread, blocks: nextBlocks, images: fetchedImages, cached_at: new Date().toISOString() })
    }
    const refreshPromise = shouldRefreshBody ? refreshThreadBody() : refreshThreadMeta()
    refreshPromise.catch(e => { if (active && !cached) setError(e.message) })
    return () => { active = false }
  }, [tid, lang, previewPageParam])

  // Poll active sync jobs
  useEffect(() => {
    let active = true
    let poll: ReturnType<typeof window.setInterval> | null = null
    const refresh = async () => {
      try {
        const activeJob = (await api.activeSyncJob(tid)).job
        if (!active) return
        if (!activeJob || !ACTIVE_JOB_STATUSES.has(activeJob.status)) {
          setActiveSyncStatus(null)
          if (poll != null) { window.clearInterval(poll); poll = null }
          return
        }
        const nextStatus = activeJob.status
        const prevStatus = activeSyncStatusRef.current
        if (prevStatus && prevStatus !== nextStatus) {
          setStatusRefreshNotice(`${t('job_status_updated')} ${t(nextStatus)}。`)
        }
        setActiveSyncStatus(nextStatus)
        const [nextThread, nextImages] = await Promise.all([api.thread(tid, previewThreadParams), api.threadImages(tid)])
        if (!active) return
        setThread(nextThread)
        setImages(nextImages)
        if (poll == null) {
          poll = window.setInterval(() => { void refresh() }, 3000)
        }
      } catch { /* ignore */ }
    }
    void refresh()
    return () => { active = false; if (poll != null) window.clearInterval(poll) }
  }, [tid, activeSyncStatus, previewPageParam, t])

  const handleResync = async () => {
    setActionLoading('resync')
    try {
      const res = await api.resyncThread(tid, thread?.forum_id ?? undefined)
      setActiveSyncStatus('queued')
      setActiveJob({ jobId: res.job_id, title: t('resync_thread') })
    } catch { /* ignore */ }
    setActionLoading(null)
  }

  const handleEditChapter = () => {
    setActionError(null)
    setChapterForm({
      display_title: thread?.display_title || thread?.raw_title || '',
      chapter_name: thread?.chapter_name || '',
      chapter_index: thread?.chapter_index != null ? String(thread.chapter_index) : '',
      author_guess: thread?.author_guess || '',
      group_name: thread?.group_name || '',
    })
    setEditingChapter(true)
  }

  const handleSaveChapter = async () => {
    setActionLoading('chapter')
    setActionError(null)
    try {
      await api.updateChapter(
        tid,
        chapterForm.display_title || thread?.display_title || thread?.raw_title || '',
        chapterForm.chapter_name || null,
        chapterForm.chapter_index ? Number(chapterForm.chapter_index) : null,
        chapterForm.author_guess || null,
        chapterForm.group_name || null,
      )
      const refreshed = await api.thread(tid, previewThreadParams)
      setThread(refreshed)
      setEditingChapter(false)
    } catch (e) {
      setActionError(e instanceof Error ? e.message : String(e))
    }
    setActionLoading(null)
  }

  const refreshThreadSnapshot = async () => {
    const [refreshed, nextImages] = await Promise.all([api.thread(tid, previewThreadParams), api.threadImages(tid)])
    setThread(refreshed)
    setImages(nextImages)
    setStatusRefreshNotice(null)
  }

  const handleExport = async () => {
    setActionLoading('export')
    try {
      const res = await api.exportThread(tid, 'sync_if_stale', thread?.forum_id ?? undefined)
      setActiveJob({ jobId: res.job_id, title: t('export_action') })
    } catch { /* ignore */ }
    setActionLoading(null)
  }

  const handleRagIndex = async () => {
    setActionLoading('rag-index')
    try {
      const result = await api.createRagIndex({ tid, force: true })
      if (result.ok && result.data) {
        setActiveJob({ jobId: result.data.job_id, title: t('rag_create_index') })
        const refreshed = await api.thread(tid, previewThreadParams)
        setThread(refreshed)
      }
    } catch { /* ignore */ }
    setActionLoading(null)
  }

  const [seriesDeleted, setSeriesDeleted] = useState<number | null>(null)

  const handleDelete = async () => {
    setActionLoading('delete')
    try {
      const res = await api.deleteThread(tid)
      if (res.deleted_series_id) setSeriesDeleted(res.deleted_series_id)
      navigate('/threads')
    } catch { /* ignore */ }
    setActionLoading(null)
    setConfirmDelete(false)
  }

  const contentKind = thread?.content_kind ?? null
  const isComic = contentKind === 'comic'
  const isNovel = contentKind === 'novel'
  const floorSource = thread?.floors || []

  if (error) return <div className="panel" style={{ color: 'var(--status-error)' }}>{error}</div>
  if (!thread) return <div className="panel" style={{ color: 'var(--text-tertiary)' }}>{t('loading')}</div>

  const forumName = FORUM_NAMES[thread.forum_id ?? 0] || String(thread.forum_id ?? '-')
  const isExportable = thread.forum_id === 30 || thread.forum_id === 55
  const fromSeries = location.state?.from === 'series'
  const seriesId = location.state?.seriesId as number | undefined
  const displayArchiveStatus = activeSyncStatus || thread.archive_status
  const archiveBreakdown = getArchiveBreakdown(thread.archive_summary, null)
  const showArchiveSummary = displayArchiveStatus === 'partial' || hasPartialArchiveBreakdown(archiveBreakdown)

  return (
    <>
      <div className="panel">
        <div className="row-actions">
          <Link to={fromSeries && seriesId ? `/series/${seriesId}` : '/threads'} className="btn-subtle">← {fromSeries ? t('series_detail') : t('thread_list')}</Link>
          <button className="btn-subtle" disabled={actionLoading === 'resync'} onClick={handleResync}>{t('resync')}</button>
          <button className="btn-subtle" disabled={actionLoading === 'rag-index'} onClick={handleRagIndex}>{t('rag_index_now')}</button>
          {isExportable && <button className="btn-subtle" disabled={actionLoading === 'export'} onClick={handleExport}>{t('export_action')}</button>}
          <button className="btn-danger-outline" onClick={() => setConfirmDelete(true)}>{t('delete')}</button>
        </div>
      </div>

      {seriesDeleted && (
        <div className="panel" style={{ color: 'var(--status-warn)', borderLeft: '3px solid var(--status-warn)' }}>
          {t('series_deleted')} (series_id={seriesDeleted})
        </div>
      )}

      {statusRefreshNotice && (
        <div className="panel notice-panel">
          <div className="notice-panel-body">
            <span>{statusRefreshNotice}</span>
            <div className="notice-actions">
              <button className="btn-subtle" onClick={() => setStatusRefreshNotice(null)}>{t('dismiss')}</button>
              <button className="btn-primary" onClick={() => void refreshThreadSnapshot()}>{t('refresh_content')}</button>
            </div>
          </div>
        </div>
      )}

      <h2 style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        {t('archive_info')}
        {(isComic || isNovel) && !editingChapter && (
          <button onClick={handleEditChapter} style={{ background: 'none', border: 'none', cursor: 'pointer', padding: 0, fontSize: 11, lineHeight: 1 }} title={t('edit')}>🛠️</button>
        )}
      </h2>
      <div className="table-wrap"><table>
        <tbody>
          {[
            ['TID', <span className="mono">{thread.tid}</span>],
            [t('original_url'), thread.url ? <a href={thread.url} target="_blank" rel="noreferrer">{thread.url}</a> : '-'],
            [t('raw_title'), thread.raw_title],
            [t('publisher'), thread.publisher || '-'],
            [t('forum'), forumName],
            [t('series'), thread.series_id ? <Link to={`/series/${thread.series_id}`}>{thread.series_title || `${t('series')} #${thread.series_id}`}</Link> : '-'],
            ...((isComic || isNovel) ? [
              [t('chapter_number'), thread.chapter_index != null ? String(thread.chapter_index) : '-'],
              [t('chapter_name'), thread.chapter_name || '-'],
            ] : []),
            ...((isComic || isNovel) ? [
              [t('author'), thread.author_guess || '-'],
              [t(isComic ? 'scanlation_group' : 'translator'), thread.group_name || '-'],
            ] : []),
            [t('category'), thread.category || '-'],
            [t('reply_count'), thread.reply_count != null ? String(thread.reply_count) : '-'],
            [t('last_reply_time'), formatDateTime(thread.remote_last_reply_at)],
            [t('content_kind'), <ContentBadge kind={thread.content_kind} />],
            [t('archive_status'), displayArchiveStatus === 'running' ? <Badge status="running">{t('resyncing')}</Badge> : <Badge status={displayArchiveStatus} />],
            [t('validation_status'), <Badge status={thread.validation_status} />],
          ].map(([k, v], i) => <tr key={i}><th style={{ width: 120 }}>{k}</th><td style={{ textAlign: 'left' }}>{v}</td></tr>)}
        </tbody>
      </table></div>

      {showArchiveSummary && archiveBreakdown && (
        <details className="archive-summary-card">
          <summary className="archive-summary-title">
            <span>{t('archive_partial_detail')}</span>
            <span className="archive-summary-arrow">▸</span>
          </summary>
          <div className="archive-summary-grid">
            <div><span>{t('archive_success')}</span><strong>
              {Object.values(archiveBreakdown.downloaded_relpaths || {}).reduce((total, items) => total + items.length, 0)
              + Object.values(archiveBreakdown.non_export_relpaths || {}).reduce((total, items) => total + items.length, 0)
              + Object.values(archiveBreakdown.shared_relpaths || {}).reduce((total, items) => total + items.length, 0)
              + Object.values(archiveBreakdown.skipped_relpaths || {}).reduce((total, items) => total + items.length, 0)}
            </strong></div>
            <div><span>{t('archive_failed')}</span><strong>{(archiveBreakdown.missing_image_urls?.length || 0) + (archiveBreakdown.missing_shared_image_urls?.length || 0)}</strong></div>
            <div><span>{t('downloaded')}</span><strong>{Object.values(archiveBreakdown.downloaded_relpaths || {}).reduce((total, items) => total + items.length, 0)}</strong></div>
            <div><span>{t('downloaded_shared')}</span><strong>{Object.values(archiveBreakdown.shared_relpaths || {}).reduce((total, items) => total + items.length, 0)}</strong></div>
            <div><span>{t('non_export')}</span><strong>{Object.values(archiveBreakdown.non_export_relpaths || {}).reduce((total, items) => total + items.length, 0)}</strong></div>
            <div><span>{t('skipped')}</span><strong>{Object.values(archiveBreakdown.skipped_relpaths || {}).reduce((total, items) => total + items.length, 0)}</strong></div>
          </div>
          <p className="archive-summary-reason">{getPartialArchiveReason(archiveBreakdown)}</p>
          <div className="archive-summary-list-group">
            {archiveBreakdown.missing_image_urls?.length ? (
              <div className="archive-summary-list"><span>{t('missing_image_urls')}</span><ul>{archiveBreakdown.missing_image_urls.map(url => <li key={url}>{url}</li>)}</ul></div>
            ) : null}
            {archiveBreakdown.missing_shared_image_urls?.length ? (
              <div className="archive-summary-list"><span>{t('missing_shared_image_urls')}</span><ul>{archiveBreakdown.missing_shared_image_urls.map(url => <li key={url}>{url}</li>)}</ul></div>
            ) : null}
          </div>
        </details>
      )}

      {editingChapter && (
        <div className="inline-edit">
          <div className="inline-edit-grid">
            <label className="inline-edit-title-field">{t('title')}<input value={chapterForm.display_title} onChange={e => setChapterForm(f => ({ ...f, display_title: e.target.value }))} /></label>
            <label>{t('chapter_number')}<input type="number" value={chapterForm.chapter_index} onChange={e => setChapterForm(f => ({ ...f, chapter_index: e.target.value }))} /></label>
            <label>{t('chapter_name')}<input value={chapterForm.chapter_name} onChange={e => setChapterForm(f => ({ ...f, chapter_name: e.target.value }))} /></label>
            <label>{t('author')}<input value={chapterForm.author_guess} onChange={e => setChapterForm(f => ({ ...f, author_guess: e.target.value }))} /></label>
            <label>{t(isComic ? 'scanlation_group' : 'translator')}<input value={chapterForm.group_name} onChange={e => setChapterForm(f => ({ ...f, group_name: e.target.value }))} /></label>
          </div>
          {actionError && <div className="inline-edit-error">{actionError}</div>}
          <div className="inline-edit-actions">
            <button className="btn-primary" disabled={actionLoading === 'chapter'} onClick={handleSaveChapter}>{t('save')}</button>
            <button className="btn-subtle" onClick={() => setEditingChapter(false)}>{t('cancel')}</button>
          </div>
        </div>
      )}

      {floorSource.length > 0 ? (
        <ThreadReader
          tid={tid}
          source="local"
          contentKind={contentKind}
          floors={floorSource}
          images={images}
          page={previewPageParam}
          totalPages={isNovel ? (thread?.floor_total_pages ?? null) : null}
          onPageChange={isNovel ? (p) => {
            const next = new URLSearchParams(searchParams)
            if (p <= 1) next.delete('preview_page')
            else next.set('preview_page', String(p))
            setSearchParams(next, { replace: true })
          } : undefined}
        />
      ) : (
        thread.image_count > 0 && images.length === 0 && (
          <div className="panel" style={{ color: 'var(--text-tertiary)' }}>
            {t('image_missing_msg', { n: thread.image_count })}
          </div>
        )
      )}

      {isComic && blocks.length > 0 && (
        <>
          <h2>{t('content_blocks')} ({blocks.length})</h2>
          <div className="table-wrap"><table>
            <thead><tr><th>{t('seq')}</th><th>PID</th><th>{t('type')}</th><th>{t('content')}</th></tr></thead>
            <tbody>
              {blocks.map(b => (
                <tr key={b.id}>
                  <td>{b.order_index}</td>
                  <td>{b.pid}</td>
                  <td className="nowrap">{b.block_type}</td>
                  <td className="truncate">{b.text?.slice(0, 200) || '-'}</td>
                </tr>
              ))}
            </tbody>
          </table></div>
        </>
      )}

      {confirmDelete && (
        <div className="confirm-overlay" onClick={() => setConfirmDelete(false)}>
          <div className="confirm-dialog" onClick={e => e.stopPropagation()}>
            <h2 style={{ margin: '0 0 16px', fontSize: 15, color: 'var(--text-primary)', textTransform: 'none', letterSpacing: 0 }}>{t('confirm_delete')}</h2>
            <p style={{ fontSize: 13, color: 'var(--text-secondary)', lineHeight: 1.6, margin: '0 0 16px' }}>
              {t('tid')}: {tid} — {thread.raw_title}
            </p>
            {thread.series_id && (
              <p style={{ fontSize: 12, color: 'var(--status-warn)', margin: '0 0 16px' }}>
                {t('auto_delete_empty_series')}
              </p>
            )}
            <div className="confirm-actions">
              <button className="btn-subtle" onClick={() => setConfirmDelete(false)}>{t('cancel')}</button>
              <button className="btn-danger" disabled={actionLoading === 'delete'} onClick={handleDelete}>{t('confirm_execute')}</button>
            </div>
          </div>
        </div>
      )}

      {activeJob && (
        <JobProgressDialog jobId={activeJob.jobId} title={activeJob.title} onClose={() => {
          setActiveJob(null)
          setActiveSyncStatus(null)
          void refreshThreadSnapshot()
        }} />
      )}
    </>
  )
}
