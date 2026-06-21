import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { Link, useNavigate, useLocation, useSearchParams } from 'react-router-dom'
import { api, type ThreadDetail as ThreadDetailType, type ContentBlock, type ThreadImage, type JobSummary } from '../api/client'
import { Badge, ContentBadge } from '../components/Badge'
import { PaginationControls } from '../components/PaginationControls'
import { useI18n } from '../context/I18nContext'
import { useTheme } from '../context/ThemeContext'
import { formatDateTime } from '../utils/time'
import { getArchiveBreakdown, getPartialArchiveReason, hasPartialArchiveBreakdown } from '../utils/archiveSummary'

const FORUM_NAMES: Record<number, string> = {}

const JOB_STAGE_TRACKS: Record<string, string[]> = {
  sync_thread: ['acquired', 'parse', 'staging', 'validate', 'download_images', 'db_commit', 'materialize'],
  export_thread: ['acquired', 'precheck', 'export_write', 'db_commit', 'finalize'],
}

const ACTIVE_JOB_STATUSES = new Set(['queued', 'running', 'retrying', 'cancel_requested', 'interrupted'])

type ReadingPresetKey = 'paper' | 'sepia' | 'night' | 'ink'

type ThemeVariant = 'light' | 'dark'
type ReadingPlatform = 'desktop' | 'mobile'

type ReadingPreset = {
  label: string
  light: ReadingPresetVariant
  dark: ReadingPresetVariant
  preferredFontLabel?: string
}

type ReadingPresetVariant = {
  bg: string
  text: string
  muted: string
  accent: string
  sidebarBg: string
  quoteBg: string
  lineHeight: number
  indent: number
}

type ReadingMode = ReadingPresetKey | 'custom'

type ReadingConfig = {
  mode: ReadingMode
  textSize: number
  fontFamily: string
  bg: string
  text: string
  muted: string
  accent: string
  sidebarBg: string
  quoteBg: string
  lineHeight: number
  indent: number
}

const READING_PRESETS: Record<ReadingPresetKey, ReadingPreset> = {
  paper: {
    label: '纸本',
    light: {
      bg: '#f6f1e8',
      text: '#2c2620',
      muted: '#6e6258',
      accent: '#8a5b3d',
      sidebarBg: '#ece2d3',
      quoteBg: '#efe7db',
      lineHeight: 1.92,
      indent: 2,
    },
    dark: {
      bg: '#1e1914',
      text: '#e8ddd1',
      muted: '#b6aa9d',
      accent: '#d0a172',
      sidebarBg: '#2a231d',
      quoteBg: '#26201a',
      lineHeight: 1.92,
      indent: 2,
    },
    preferredFontLabel: '文黑体',
  },
  sepia: {
    label: '琥珀',
    light: {
      bg: '#efe3cf',
      text: '#31271f',
      muted: '#74685e',
      accent: '#936a33',
      sidebarBg: '#e6d6bf',
      quoteBg: '#ece0cb',
      lineHeight: 1.98,
      indent: 2,
    },
    dark: {
      bg: '#241b13',
      text: '#e7d8c3',
      muted: '#b8aa98',
      accent: '#d6aa68',
      sidebarBg: '#2e241a',
      quoteBg: '#2a2118',
      lineHeight: 1.98,
      indent: 2,
    },
    preferredFontLabel: '仓耳华新体',
  },
  night: {
    label: '夜读',
    light: {
      bg: '#f5f7fb',
      text: '#27303b',
      muted: '#647084',
      accent: '#597fe8',
      sidebarBg: '#e7edf5',
      quoteBg: '#edf2f8',
      lineHeight: 2.02,
      indent: 2.2,
    },
    dark: {
      bg: '#1f2228',
      text: '#e6e0d5',
      muted: '#b4aa9b',
      accent: '#8fb4ff',
      sidebarBg: '#171a1f',
      quoteBg: '#252a33',
      lineHeight: 2.02,
      indent: 2.2,
    },
    preferredFontLabel: '攸望轻吟体',
  },
  ink: {
    label: '墨痕',
    light: {
      bg: '#f8f8f6',
      text: '#181818',
      muted: '#676767',
      accent: '#6a4cf2',
      sidebarBg: '#efefec',
      quoteBg: '#f2f2f0',
      lineHeight: 1.88,
      indent: 1.8,
    },
    dark: {
      bg: '#111214',
      text: '#ecebea',
      muted: '#b7b7b2',
      accent: '#a395ff',
      sidebarBg: '#1a1b1f',
      quoteBg: '#1d2024',
      lineHeight: 1.88,
      indent: 1.8,
    },
    preferredFontLabel: '九九儷中黑 v1.6',
  },
}

const READING_CONFIG_STORAGE_KEYS: Record<ReadingPlatform, Record<ThemeVariant, string>> = {
  desktop: {
    light: 'yamibo.reading-config.desktop.light.v4',
    dark: 'yamibo.reading-config.desktop.dark.v4',
  },
  mobile: {
    light: 'yamibo.reading-config.mobile.light.v4',
    dark: 'yamibo.reading-config.mobile.dark.v4',
  },
}
const THREAD_DETAIL_CACHE_PREFIX = 'yamibo.thread-detail-cache.v1'

function buildReadingConfig(mode: ReadingPresetKey, variant: ThemeVariant, textSize = 100, fontFamily = 'system'): ReadingConfig {
  const preset = READING_PRESETS[mode][variant]
  return {
    mode,
    textSize,
    fontFamily,
    bg: preset.bg,
    text: preset.text,
    muted: preset.muted,
    accent: preset.accent,
    sidebarBg: preset.sidebarBg,
    quoteBg: preset.quoteBg,
    lineHeight: preset.lineHeight,
    indent: preset.indent,
  }
}

function getDefaultReadingTextSize(platform: ReadingPlatform): number {
  return platform === 'mobile' ? 125 : 100
}

function isReadingPresetKey(value: unknown): value is ReadingPresetKey {
  return value === 'paper' || value === 'sepia' || value === 'night' || value === 'ink'
}

function isReadingMode(value: unknown): value is ReadingMode {
  return isReadingPresetKey(value) || value === 'custom'
}

function loadReadingConfig(platform: ReadingPlatform, variant: ThemeVariant): ReadingConfig | null {
  if (typeof window === 'undefined') return null
  try {
    const raw = window.localStorage.getItem(READING_CONFIG_STORAGE_KEYS[platform][variant])
    if (!raw) return null
    const parsed = JSON.parse(raw) as Partial<ReadingConfig>
    const mode = isReadingMode(parsed.mode) && parsed.mode !== 'custom' ? parsed.mode : 'paper'
    const preset = READING_PRESETS[mode][variant]
    return {
      mode: parsed.mode === 'custom' ? 'custom' : mode,
      textSize: typeof parsed.textSize === 'number' && Number.isFinite(parsed.textSize) ? parsed.textSize : 100,
      fontFamily: typeof parsed.fontFamily === 'string' && parsed.fontFamily ? parsed.fontFamily : 'system',
      bg: typeof parsed.bg === 'string' && parsed.bg ? parsed.bg : preset.bg,
      text: typeof parsed.text === 'string' && parsed.text ? parsed.text : preset.text,
      muted: typeof parsed.muted === 'string' && parsed.muted ? parsed.muted : preset.muted,
      accent: typeof parsed.accent === 'string' && parsed.accent ? parsed.accent : preset.accent,
      sidebarBg: typeof parsed.sidebarBg === 'string' && parsed.sidebarBg ? parsed.sidebarBg : preset.sidebarBg,
      quoteBg: typeof parsed.quoteBg === 'string' && parsed.quoteBg ? parsed.quoteBg : preset.quoteBg,
      lineHeight: typeof parsed.lineHeight === 'number' && Number.isFinite(parsed.lineHeight) ? parsed.lineHeight : preset.lineHeight,
      indent: typeof parsed.indent === 'number' && Number.isFinite(parsed.indent) ? parsed.indent : preset.indent,
    }
  } catch {
    return null
  }
}

function readingConfigMatchesPreset(config: ReadingConfig, mode: ReadingPresetKey, variant: ThemeVariant, textSize: number): boolean {
  const preset = READING_PRESETS[mode][variant]
  return config.mode === mode
    && config.textSize === textSize
    && config.bg === preset.bg
    && config.text === preset.text
    && config.muted === preset.muted
    && config.accent === preset.accent
    && config.sidebarBg === preset.sidebarBg
    && config.quoteBg === preset.quoteBg
    && config.lineHeight === preset.lineHeight
    && config.indent === preset.indent
}

function deriveReadingMode(config: ReadingConfig, variant: ThemeVariant): ReadingMode {
  for (const mode of Object.keys(READING_PRESETS) as ReadingPresetKey[]) {
    if (readingConfigMatchesPreset(config, mode, variant, 100)) return mode
  }
  return 'custom'
}

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
  } catch {
    return null
  }
}

function saveThreadDetailCache(tid: number, page: number, data: ThreadDetailCache): void {
  if (typeof window === 'undefined') return
  try {
    window.sessionStorage.setItem(`${THREAD_DETAIL_CACHE_PREFIX}:${tid}:${page}`, JSON.stringify(data))
  } catch { /* ignore */ }
}

function isThreadDetailCacheFresh(cache: ThreadDetailCache, maxAgeMs = 120_000): boolean {
  if (!cache.cached_at) return false
  const cachedAt = Date.parse(cache.cached_at)
  if (!Number.isFinite(cachedAt)) return false
  return Date.now() - cachedAt < maxAgeMs
}

function renderNovelParagraphs(text: string, isMobile: boolean) {
  const paragraphs = splitNovelParagraphs(text, isMobile)
  return paragraphs.map((part, index) => (
    <p
      key={`${index}-${part.text.slice(0, 12)}`}
      className={part.kind === 'heading' ? 'floor-paragraph floor-paragraph-heading' : 'floor-paragraph'}
    >
      {part.text}
    </p>
  ))
}

function splitNovelParagraphs(text: string, isMobile: boolean): Array<{ text: string; kind: 'paragraph' | 'heading' }> {
  const paragraphs: Array<{ text: string; kind: 'paragraph' | 'heading' }> = []
  const lines = text.replace(/\r\n?/g, '\n').split('\n')
  let buffer: string[] = []

  const flushBuffer = () => {
    if (buffer.length === 0) return
    const raw = buffer.join(' ').replace(/\s+/g, ' ').trim()
    buffer = []
    if (!raw) return
    if (looksLikeNovelHeading(raw)) {
      paragraphs.push({ text: raw, kind: 'heading' })
    } else {
      paragraphs.push({ text: raw, kind: 'paragraph' })
    }
  }

  for (const line of lines) {
    const value = line.trim()
    if (!value) {
      flushBuffer()
      continue
    }
    if (looksLikeNovelHeading(value)) {
      flushBuffer()
      paragraphs.push({ text: value, kind: 'heading' })
      continue
    }
    if (isMobile) {
      buffer.push(value)
      continue
    }
    paragraphs.push({ text: value, kind: 'paragraph' })
  }
  flushBuffer()
  return paragraphs
}

function looksLikeNovelHeading(line: string): boolean {
  const value = line.trim()
  if (!value) return false
  if (/^(Episode\s*\d+|第\s*[0-9一二三四五六七八九十百千零]+[话章节卷篇])/i.test(value)) return true
  return false
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
  const { t } = useI18n()
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
              <div
                className="job-progress-bar-fill"
                style={{
                  width: isError ? '100%' : `${progress || (isDone ? 100 : 0)}%`,
                  background: isError ? 'var(--status-error)' : isDone ? 'var(--status-ok, #1a7f37)' : 'var(--accent)',
                }}
              />
            </div>

            {job?.error_message && isError && (
              <div className="job-progress-error">{job.error_message}</div>
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
          <div className="job-progress-footer-left">
            <span>{job?.stage || t(statusKey)}</span>
          </div>
          <button className="btn-subtle" onClick={onClose}>{isDone || isError ? t('close') : t('background')}</button>
        </div>
      </div>
    </div>
  )
}

interface FloorGroup {
  pid: number
  floor_no: number
  publisher: string | null
  publisher_uid: string | null
  pub_time: string | null
  content: string
  contentImages: ThreadImage[]
  smallImages: ThreadImage[]
  quoteText: string | null
  replyText: string | null
  richBodyHtml: string | null
}

export function ThreadDetail() {
  const { t, lang } = useI18n()
  const { dark } = useTheme()
  const tid = parseInt(window.location.pathname.split('/').pop() || '0')
  const navigate = useNavigate()
  const location = useLocation()
  const [searchParams, setSearchParams] = useSearchParams()
  const isMobile = typeof window !== 'undefined' && window.innerWidth <= 768
  const readingPlatform: ReadingPlatform = isMobile ? 'mobile' : 'desktop'
  const previewPageParam = normalizePreviewPage(parseInt(searchParams.get('preview_page') || '1', 10))
  const previewThreadParams = { preview_page: previewPageParam, preview_page_size: 10 }
  const initialCache = loadThreadDetailCache(tid, previewPageParam)
  const [thread, setThread] = useState<ThreadDetailType | null>(() => initialCache?.thread ?? null)
  const [blocks, setBlocks] = useState<ContentBlock[]>(() => initialCache?.blocks ?? [])
  const [images, setImages] = useState<ThreadImage[]>(() => initialCache?.images ?? [])
  const [error, setError] = useState<string | null>(null)
  const [actionLoading, setActionLoading] = useState<string | null>(null)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [imgWidth, setImgWidth] = useState(isMobile ? 100 : 75)
  const [readingModeOpen, setReadingModeOpen] = useState(false)
  const themeVariant: ThemeVariant = dark ? 'dark' : 'light'
  const [readingConfig, setReadingConfig] = useState<ReadingConfig>(() => loadReadingConfig(readingPlatform, themeVariant) ?? buildReadingConfig('paper', themeVariant, getDefaultReadingTextSize(readingPlatform)))
  const [fontAssets, setFontAssets] = useState<import('../api/client').FontAsset[]>([])
  const [editingChapter, setEditingChapter] = useState(false)
  const [chapterForm, setChapterForm] = useState({ chapter_name: '', chapter_index: '', author_guess: '', group_name: '' })
  const [activeSyncStatus, setActiveSyncStatus] = useState<string | null>(null)
  const [statusRefreshNotice, setStatusRefreshNotice] = useState<string | null>(null)
  const readingConfigRef = useRef(readingConfig)
  const activeSyncStatusRef = useRef<string | null>(null)
  const floorRefs = useRef(new Map<number, HTMLDivElement | null>())
  const scrollSuppressed = useRef(false)
  const previewScrollSuppressed = useRef(false)
  const [pendingFloorNo, setPendingFloorNo] = useState<number | null>(null)
  const [currentFloorNo, setCurrentFloorNo] = useState<number | null>(null)
  const [activeJob, setActiveJob] = useState<{ jobId: string; title: string } | null>(null)
  const customModeLabel = `${t('custom')}（${dark ? t('night_mode') : t('day_mode')}）`

  useEffect(() => {
    activeSyncStatusRef.current = activeSyncStatus
  }, [activeSyncStatus])

  useEffect(() => {
    const loaded = loadReadingConfig(readingPlatform, themeVariant)
    if (loaded) {
      setReadingConfig(loaded)
      return
    }
    const current = readingConfigRef.current
    if (current.mode === 'custom') {
      setReadingConfig(buildReadingConfig('paper', themeVariant, getDefaultReadingTextSize(readingPlatform)))
      return
    }
    setReadingConfig(buildReadingConfig(current.mode, themeVariant, current.textSize, current.fontFamily))
  }, [readingPlatform, themeVariant])

  useEffect(() => {
    const cached = loadThreadDetailCache(tid, previewPageParam)
    if (cached) {
      if (cached.thread) setThread(cached.thread)
      setBlocks(cached.blocks)
      setImages(cached.images)
    }
    let active = true
    const refreshThreadMeta = async () => {
      const [nextThread, fs] = await Promise.all([
        api.thread(tid, previewThreadParams),
        api.forums(),
      ])
      if (!active) return
      setThread(nextThread)
      fs.forEach(f => { FORUM_NAMES[f.forum_id] = lang === 'en' ? (f.name_en || f.name) : f.name })
      if (!cached) return
      saveThreadDetailCache(tid, previewPageParam, {
        thread: nextThread,
        blocks: cached.blocks,
        images: cached.images,
        cached_at: new Date().toISOString(),
      })
    }
    const shouldRefreshBody = !cached || !isThreadDetailCacheFresh(cached)
    const refreshThreadBody = async () => {
      const [nextThread, fs] = await Promise.all([
        api.thread(tid, previewThreadParams),
        api.forums(),
      ])
      if (!active) return
      setThread(nextThread)
      fs.forEach(f => { FORUM_NAMES[f.forum_id] = lang === 'en' ? (f.name_en || f.name) : f.name })
      let nextBlocks: ContentBlock[] = []
      let nextImages: ThreadImage[] = []
      if (nextThread.content_kind === 'novel') {
        setBlocks(nextBlocks)
        setImages(nextImages)
      } else {
        const [fetchedBlocks, fetchedImages] = await Promise.all([api.threadBlocks(tid), api.threadImages(tid)])
        if (!active) return
        nextBlocks = fetchedBlocks
        nextImages = fetchedImages
        setBlocks(nextBlocks)
        setImages(nextImages)
      }
      saveThreadDetailCache(tid, previewPageParam, {
        thread: nextThread,
        blocks: nextBlocks,
        images: nextImages,
        cached_at: new Date().toISOString(),
      })
    }
    const refreshPromise = shouldRefreshBody ? refreshThreadBody() : refreshThreadMeta()
    refreshPromise.catch(e => {
      if (active && !cached) setError(e.message)
    })
    return () => { active = false }
  }, [tid, lang, previewPageParam])

  useEffect(() => {
    let active = true
    api.fonts().then(async list => {
      if (!active) return
      setFontAssets(list)
      if (list.length === 0) return
      for (const font of list) {
        try {
          const face = new FontFace(font.family, `url(${font.url})`)
          await face.load()
          if (!active) return
          document.fonts.add(face)
        } catch { /* ignore individual font failures */ }
      }
      if (readingConfigRef.current.mode !== 'custom' && readingConfigRef.current.fontFamily === 'system') {
        const currentMode = readingConfigRef.current.mode
        const preferred = list.find(font => font.label === READING_PRESETS[currentMode].preferredFontLabel) || list[0]
        if (preferred) {
          setReadingConfig(prev => ({ ...prev, fontFamily: preferred.family }))
        }
      }
    }).catch(() => {})
    return () => { active = false }
  }, [])

  useEffect(() => {
    readingConfigRef.current = readingConfig
    if (typeof window !== 'undefined') {
      window.localStorage.setItem(READING_CONFIG_STORAGE_KEYS[readingPlatform][themeVariant], JSON.stringify(readingConfig))
    }
  }, [readingConfig, readingPlatform, themeVariant])

  useEffect(() => {
    if (fontAssets.length === 0) return
    const current = readingConfigRef.current
    if (current.mode === 'custom' || current.fontFamily !== 'system') return
    const presetMode = current.mode as ReadingPresetKey
    const preferred = fontAssets.find(font => font.label === READING_PRESETS[presetMode].preferredFontLabel) || fontAssets[0]
    if (preferred) {
      setReadingConfig(prev => prev.mode === 'custom' || prev.fontFamily !== 'system' ? prev : { ...prev, fontFamily: preferred.family })
    }
  }, [fontAssets, themeVariant, readingPlatform])

  const applyReadingPreset = (presetKey: ReadingPresetKey) => {
    const preset = READING_PRESETS[presetKey]
    const variantPreset = preset[themeVariant]
    const preferred = fontAssets.find(font => font.label === preset.preferredFontLabel)
    setReadingConfig({
      mode: presetKey,
      textSize: 100,
      fontFamily: preferred?.family || 'system',
      bg: variantPreset.bg,
      text: variantPreset.text,
      muted: variantPreset.muted,
      accent: variantPreset.accent,
      sidebarBg: variantPreset.sidebarBg,
      quoteBg: variantPreset.quoteBg,
      lineHeight: variantPreset.lineHeight,
      indent: variantPreset.indent,
    })
  }

  const updateReadingConfig = (patch: Partial<ReadingConfig>) => {
    setReadingConfig(prev => {
      const next = { ...prev, ...patch }
      next.mode = deriveReadingMode(next, themeVariant)
      return next
    })
  }

  const setCustomReadingMode = () => {
    setReadingConfig(prev => ({ ...prev, mode: 'custom' }))
  }

  useEffect(() => {
    let active = true
    let poll: ReturnType<typeof window.setInterval> | null = null
    const refresh = async () => {
      try {
        const jobs = await api.jobs()
        if (!active) return
        const activeJob = jobs.find(j => j.tid === tid && j.job_type === 'sync_thread' && ACTIVE_JOB_STATUSES.has(j.status))
        if (!activeJob) {
          setActiveSyncStatus(null)
          if (poll != null) {
            window.clearInterval(poll)
            poll = null
          }
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
          poll = window.setInterval(() => {
            void refresh()
          }, 3000)
        }
      } catch { /* ignore */ }
    }
    void refresh()
    return () => {
      active = false
      if (poll != null) window.clearInterval(poll)
    }
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
    setChapterForm({
      chapter_name: thread?.chapter_name || '',
      chapter_index: thread?.chapter_index != null ? String(thread.chapter_index) : '',
      author_guess: thread?.author_guess || '',
      group_name: thread?.group_name || '',
    })
    setEditingChapter(true)
  }

  const handleSaveChapter = async () => {
    setActionLoading('chapter')
    try {
      await api.updateChapter(
        tid,
        chapterForm.chapter_name || null,
        chapterForm.chapter_index ? Number(chapterForm.chapter_index) : null,
        chapterForm.author_guess || null,
        chapterForm.group_name || null,
      )
      const refreshed = await api.thread(tid, previewThreadParams)
      setThread(refreshed)
      setEditingChapter(false)
    } catch { /* ignore */ }
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
  const totalFloorCount = thread?.floor_count ?? floorSource.length
  const pageSize = isNovel ? (thread?.floor_page_size ?? 10) : Math.max(1, totalFloorCount)
  const totalPages = isNovel
    ? (thread?.floor_total_pages ?? Math.max(1, Math.ceil(totalFloorCount / pageSize)))
    : 1
  const requestedPreviewPage = isNovel
    ? Math.min(Math.max(1, thread?.floor_page ?? previewPageParam), totalPages)
    : 1

  const imagesByPid = new Map<number, { content: ThreadImage[]; small: ThreadImage[] }>()
  for (const img of images) {
    if (img.is_shared) continue
    const entry = imagesByPid.get(img.pid) || { content: [], small: [] }
    if (isComic || img.is_content) {
      entry.content.push(img)
    } else {
      entry.small.push(img)
    }
    imagesByPid.set(img.pid, entry)
  }

  const floorGroups: FloorGroup[] = floorSource.map(f => {
    const imgGroup = imagesByPid.get(f.pid) || { content: [], small: [] }
    return {
      pid: f.pid,
      floor_no: f.floor_no,
      publisher: f.publisher,
      publisher_uid: f.publisher_uid,
      pub_time: f.pub_time,
      content: f.content || '',
      contentImages: imgGroup.content,
      smallImages: imgGroup.small,
      quoteText: f.quote_text || null,
      replyText: f.reply_text || null,
      richBodyHtml: f.rich_body_html || null,
    }
  })

  const effectiveFloorGroups = floorGroups
  const effectiveTotalPages = totalPages
  const effectivePreviewPage = requestedPreviewPage
  const visibleFloorGroups = isNovel && thread?.floor_page_size == null && thread?.floor_page == null
    ? effectiveFloorGroups.slice((effectivePreviewPage - 1) * pageSize, effectivePreviewPage * pageSize)
    : effectiveFloorGroups

  const setPreviewPage = (page: number) => {
    const next = new URLSearchParams(searchParams)
    if (page <= 1) {
      next.delete('preview_page')
    } else {
      next.set('preview_page', String(page))
    }
    setSearchParams(next, { replace: true })
  }

  useEffect(() => {
    if (!isNovel || requestedPreviewPage === effectivePreviewPage) return
    const next = new URLSearchParams(searchParams)
    if (effectivePreviewPage <= 1) {
      next.delete('preview_page')
    } else {
      next.set('preview_page', String(effectivePreviewPage))
    }
    setSearchParams(next, { replace: true })
  }, [isNovel, requestedPreviewPage, effectivePreviewPage, searchParams, setSearchParams])

  useEffect(() => {
    if (visibleFloorGroups.length === 0) {
      setCurrentFloorNo(null)
      return
    }
    setCurrentFloorNo(prev => {
      if (pendingFloorNo != null && visibleFloorGroups.some(floor => floor.floor_no === pendingFloorNo)) return pendingFloorNo
      if (prev != null && visibleFloorGroups.some(floor => floor.floor_no === prev)) return prev
      return visibleFloorGroups[0].floor_no
    })
  }, [visibleFloorGroups, pendingFloorNo])

  useLayoutEffect(() => {
    if (visibleFloorGroups.length === 0) return
    let ticking = false
    const updateCurrentFloor = () => {
      ticking = false
      if (scrollSuppressed.current) return
      const threshold = 140
      let candidate = visibleFloorGroups[0].floor_no
      let bestDistance = Number.POSITIVE_INFINITY
      for (const floor of visibleFloorGroups) {
        const node = floorRefs.current.get(floor.floor_no)
        if (!node) continue
        const rect = node.getBoundingClientRect()
        if (rect.bottom <= threshold) continue
        const distance = Math.abs(rect.top - threshold)
        if (distance < bestDistance) {
          bestDistance = distance
          candidate = floor.floor_no
        }
      }
      setCurrentFloorNo(candidate)
    }
    const onScroll = () => {
      if (ticking) return
      ticking = true
      window.requestAnimationFrame(updateCurrentFloor)
    }
    updateCurrentFloor()
    window.addEventListener('scroll', onScroll, { passive: true })
    window.addEventListener('resize', onScroll)
    return () => {
      window.removeEventListener('scroll', onScroll)
      window.removeEventListener('resize', onScroll)
    }
  }, [visibleFloorGroups])

  useLayoutEffect(() => {
    if (pendingFloorNo == null) { scrollSuppressed.current = false; return }
    let cancelled = false
    const attemptScroll = () => {
      if (cancelled) return
      const node = floorRefs.current.get(pendingFloorNo)
      if (!node) {
        window.requestAnimationFrame(attemptScroll)
        return
      }
      node.scrollIntoView({ behavior: 'smooth', block: 'start' })
      setCurrentFloorNo(pendingFloorNo)
      scrollSuppressed.current = false
      setPendingFloorNo(null)
    }
    attemptScroll()
    return () => { cancelled = true }
  }, [pendingFloorNo, visibleFloorGroups])

  const jumpToFloor = (targetFloorNo: number) => {
    const normalized = Math.max(1, Math.floor(targetFloorNo))
    scrollSuppressed.current = true
    setPendingFloorNo(normalized)
    const targetPage = isNovel ? Math.min(totalPages, Math.max(1, Math.ceil(normalized / pageSize))) : 1
    if (isNovel && targetPage !== effectivePreviewPage) {
      previewScrollSuppressed.current = true
      setPreviewPage(targetPage)
      return
    }
    const node = floorRefs.current.get(normalized)
    if (node) {
      node.scrollIntoView({ behavior: 'smooth', block: 'start' })
      setCurrentFloorNo(normalized)
      setPendingFloorNo(null)
    }
  }

  const wheelPrevFloor = currentFloorNo != null ? (currentFloorNo > 1 ? currentFloorNo - 1 : null) : null
  const wheelNextFloor = currentFloorNo != null ? (currentFloorNo < totalFloorCount ? currentFloorNo + 1 : null) : null

  if (error) return <div className="panel" style={{ color: 'var(--status-error)' }}>{error}</div>
  if (!thread) return <div className="panel" style={{ color: 'var(--text-tertiary)' }}>{t('loading')}</div>

  const forumName = FORUM_NAMES[thread.forum_id ?? 0] || String(thread.forum_id ?? '-')
  const isExportable = thread.forum_id === 30 || thread.forum_id === 55
  const fromSeries = location.state?.from === 'series'
  const seriesId = location.state?.seriesId as number | undefined
  const displayArchiveStatus = activeSyncStatus || thread.archive_status
  const archiveBreakdown = getArchiveBreakdown(thread.archive_summary, null)
  const showArchiveSummary = displayArchiveStatus === 'partial' || hasPartialArchiveBreakdown(archiveBreakdown)
  const readingViewStyle = {
    '--reading-bg': readingConfig.bg,
    '--reading-text': readingConfig.text,
    '--reading-muted': readingConfig.muted,
    '--reading-accent': readingConfig.accent,
    '--reading-sidebar-bg': readingConfig.sidebarBg,
    '--reading-quote-bg': readingConfig.quoteBg,
    '--reading-line-height': String(readingConfig.lineHeight),
    '--reading-indent': `${readingConfig.indent}em`,
    '--reading-font-family': readingConfig.fontFamily === 'system'
      ? 'var(--font-serif, Georgia, "Times New Roman", serif)'
      : readingConfig.fontFamily,
  } as React.CSSProperties

  return (
    <>
      {effectiveFloorGroups.length > 0 && currentFloorNo != null && (
        <div
          className="floor-wheel"
          onWheel={e => {
            e.preventDefault()
            if (e.deltaY > 0 && wheelNextFloor != null && wheelNextFloor !== currentFloorNo) {
              jumpToFloor(wheelNextFloor)
            } else if (e.deltaY < 0 && wheelPrevFloor != null && wheelPrevFloor !== currentFloorNo) {
              jumpToFloor(wheelPrevFloor)
            }
          }}
        >
          <button
            className="floor-wheel-btn floor-wheel-btn-prev"
            disabled={wheelPrevFloor == null || wheelPrevFloor === currentFloorNo}
            onClick={() => wheelPrevFloor != null && jumpToFloor(wheelPrevFloor)}
          >
            {wheelPrevFloor ?? currentFloorNo}F
          </button>
          <div className="floor-wheel-current">
            <span className="floor-wheel-label">{t('floor_jump')}</span>
            <strong>{currentFloorNo}F</strong>
          </div>
          <button
            className="floor-wheel-btn floor-wheel-btn-next"
            disabled={wheelNextFloor == null || wheelNextFloor === currentFloorNo}
            onClick={() => wheelNextFloor != null && jumpToFloor(wheelNextFloor)}
          >
            {wheelNextFloor ?? currentFloorNo}F
          </button>
        </div>
      )}
      <div className="panel">
        <div className="row-actions">
          <Link to={fromSeries && seriesId ? `/series/${seriesId}` : '/threads'} className="btn-subtle">← {fromSeries ? t('series_detail') : t('thread_list')}</Link>
          <button className="btn-subtle" disabled={actionLoading === 'resync'} onClick={handleResync}>{t('resync')}</button>
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
          <button onClick={handleEditChapter} style={{ background: 'none', border: 'none', cursor: 'pointer', padding: 0, fontSize: 11, lineHeight: 1 }} title={t('edit')}>
            🛠️
          </button>
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
            [t('image_count'), String(thread.image_count ?? 0)],
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
              <div className="archive-summary-list">
                <span>{t('missing_image_urls')}</span>
                <ul>{archiveBreakdown.missing_image_urls.map(url => <li key={url}>{url}</li>)}</ul>
              </div>
            ) : null}
            {archiveBreakdown.missing_shared_image_urls?.length ? (
              <div className="archive-summary-list">
                <span>{t('missing_shared_image_urls')}</span>
                <ul>{archiveBreakdown.missing_shared_image_urls.map(url => <li key={url}>{url}</li>)}</ul>
              </div>
            ) : null}
          </div>
        </details>
      )}

      {editingChapter && (
        <div className="inline-edit">
          <div className="inline-edit-grid">
            <label>{t('chapter_number')}<input type="number" value={chapterForm.chapter_index} onChange={e => setChapterForm(f => ({ ...f, chapter_index: e.target.value }))} /></label>
            <label>{t('chapter_name')}<input value={chapterForm.chapter_name} onChange={e => setChapterForm(f => ({ ...f, chapter_name: e.target.value }))} /></label>
            <label>{t('author')}<input value={chapterForm.author_guess} onChange={e => setChapterForm(f => ({ ...f, author_guess: e.target.value }))} /></label>
            <label>{t(isComic ? 'scanlation_group' : 'translator')}<input value={chapterForm.group_name} onChange={e => setChapterForm(f => ({ ...f, group_name: e.target.value }))} /></label>
          </div>
          <div className="inline-edit-actions">
            <button className="btn-primary" disabled={actionLoading === 'chapter'} onClick={handleSaveChapter}>{t('save')}</button>
            <button className="btn-subtle" onClick={() => setEditingChapter(false)}>{t('cancel')}</button>
          </div>
        </div>
      )}

      {floorGroups.length > 0 ? (
        <>
          <div id="thread-reading-top" />
          <h2>{t('read_preview')}</h2>
          <div className="reading-mode-panel">
            <div className="reading-mode-summary">
              <button type="button" className="reading-mode-toggle" onClick={() => setReadingModeOpen(v => !v)}>
                <span>{t('reading_mode')}</span>
                <span className="reading-mode-arrow">{readingModeOpen ? '▾' : '▸'}</span>
              </button>
              <label className="reading-mode-select-wrap">
                <span>{t('current_scheme')}</span>
                <select
                  className="reading-mode-select"
                  value={readingConfig.mode}
                  onChange={e => {
                    if (e.target.value === 'custom') {
                      setCustomReadingMode()
                    } else if (isReadingPresetKey(e.target.value)) {
                      applyReadingPreset(e.target.value)
                    }
                  }}
                >
                  {(Object.entries(READING_PRESETS) as Array<[ReadingPresetKey, ReadingPreset]>).map(([key, preset]) => (
                    <option key={key} value={key}>{preset.label}</option>
                  ))}
                  <option value="custom">{customModeLabel}</option>
                </select>
              </label>
              <div className="reading-mode-inline-size">
                <span>{t('font_size')}</span>
                <input
                  type="range"
                  min="75"
                  max="200"
                  step="5"
                  value={readingConfig.textSize}
                  onChange={e => updateReadingConfig({ textSize: Number(e.target.value) })}
                />
                <strong>{readingConfig.textSize}%</strong>
              </div>
            </div>
            {readingModeOpen && (
              <div className="reading-mode-body">
                <div className="reading-mode-presets">
                  {(Object.entries(READING_PRESETS) as Array<[ReadingPresetKey, ReadingPreset]>).map(([key, preset]) => (
                    <button
                      type="button"
                      key={key}
                      className={readingConfig.mode === key ? 'active' : ''}
                      onClick={() => applyReadingPreset(key)}
                    >
                      {preset.label}
                    </button>
                  ))}
                  <button
                    type="button"
                    className={readingConfig.mode === 'custom' ? 'active' : ''}
                    onClick={setCustomReadingMode}
                  >
                    {customModeLabel}
                  </button>
                </div>
                <div className="reading-mode-controls">
                  <label>
                    字体
                    <select value={readingConfig.fontFamily} onChange={e => updateReadingConfig({ fontFamily: e.target.value })}>
                      <option value="system">系统默认</option>
                      {fontAssets.map(font => (
                        <option key={font.family} value={font.family}>{font.label}</option>
                      ))}
                    </select>
                  </label>
                  <label>
                    背景
                    <input type="color" value={readingConfig.bg} onChange={e => updateReadingConfig({ bg: e.target.value })} />
                  </label>
                  <label>
                    文字
                    <input type="color" value={readingConfig.text} onChange={e => updateReadingConfig({ text: e.target.value })} />
                  </label>
                  <label>
                    辅助
                    <input type="color" value={readingConfig.muted} onChange={e => updateReadingConfig({ muted: e.target.value })} />
                  </label>
                  <label>
                    侧栏
                    <input type="color" value={readingConfig.sidebarBg} onChange={e => updateReadingConfig({ sidebarBg: e.target.value })} />
                  </label>
                  <label>
                    引文
                    <input type="color" value={readingConfig.quoteBg} onChange={e => updateReadingConfig({ quoteBg: e.target.value })} />
                  </label>
                  <label>
                    强调
                    <input type="color" value={readingConfig.accent} onChange={e => updateReadingConfig({ accent: e.target.value })} />
                  </label>
                  <label>
                    段落首行缩进
                    <input type="number" min="0" max="6" step="0.1" value={readingConfig.indent} onChange={e => updateReadingConfig({ indent: Number(e.target.value) })} />
                  </label>
                  <label>
                    行距
                    <input type="number" min="1.2" max="3" step="0.05" value={readingConfig.lineHeight} onChange={e => updateReadingConfig({ lineHeight: Number(e.target.value) })} />
                  </label>
                </div>
              </div>
            )}
          </div>
          <PaginationControls page={effectivePreviewPage} totalPages={effectiveTotalPages} onPageChange={setPreviewPage} scrollTargetId="thread-reading-top" suppressScrollRef={previewScrollSuppressed} className="reading-pagination pagination-controls" />
          {isComic && (
            <div className="reading-toolbar">
              <span className="toolbar-label">{t('image_width')}</span>
              {[50, 75, 100].map(w => (
                <button key={w} className={imgWidth === w ? 'active' : ''} onClick={() => setImgWidth(w)}>{w}%</button>
              ))}
            </div>
          )}
          <div className="reading-view" style={{ '--img-pct': `${imgWidth}%`, fontSize: `${readingConfig.textSize}%`, ...readingViewStyle } as React.CSSProperties}>
            {visibleFloorGroups.map(fg => (
              <div
                key={fg.pid}
                className="floor-row"
                ref={node => {
                  floorRefs.current.set(fg.floor_no, node)
                }}
              >
                <div className="floor-sidebar">
                  <div className="floor-no">{fg.floor_no}F</div>
                  <div className="floor-publisher">{fg.publisher || '-'}</div>
                  {fg.pub_time && <div className="floor-time">{formatDateTime(fg.pub_time)}</div>}
                  <div className="floor-pid">#{fg.pid}</div>
                </div>
                <div className={`floor-content${isNovel ? ' floor-content-novel' : ''}`}>
                  {fg.richBodyHtml ? (
                    <div className={`floor-text floor-rich-body${isNovel ? ' floor-rich-body-novel' : ''}`} dangerouslySetInnerHTML={{ __html: fg.richBodyHtml }} />
                  ) : (
                    <>
                      {fg.quoteText && (
                        <div className="floor-quote">
                          <blockquote>{fg.quoteText}</blockquote>
                        </div>
                      )}
                      {(fg.replyText || (!fg.quoteText && fg.content)) && (
                        <div className="floor-text">
                          {isNovel ? renderNovelParagraphs(fg.replyText || fg.content, isMobile) : (fg.replyText || fg.content)}
                        </div>
                      )}
                    </>
                  )}
                  {fg.contentImages.length > 0 && (
                    <div className="floor-images">
                      {fg.contentImages.map((img, i) => {
                        const src = `/media/threads/${tid}/${img.url}`
                        return (
                          <a key={i} href={src} target="_blank" rel="noreferrer" className="floor-image-link">
                            <img src={src} loading="lazy" alt="" />
                          </a>
                        )
                      })}
                    </div>
                  )}
                  {fg.smallImages.length > 0 && (
                    <div className="floor-small-images">
                      {fg.smallImages.map((img, i) => {
                        const src = `/media/threads/${tid}/${img.url}`
                        return <img key={i} src={src} loading="lazy" alt="" className="small-img" />
                      })}
                    </div>
                  )}
                </div>
              </div>
            ))}
          </div>
          {isNovel && <PaginationControls page={effectivePreviewPage} totalPages={effectiveTotalPages} onPageChange={setPreviewPage} scrollTargetId="thread-reading-top" suppressScrollRef={previewScrollSuppressed} className="reading-pagination pagination-controls" />}
        </>
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
        <JobProgressDialog
          jobId={activeJob.jobId}
        title={activeJob.title}
        onClose={() => {
          setActiveJob(null)
          setActiveSyncStatus(null)
          void refreshThreadSnapshot()
        }}
      />
      )}
    </>
  )
}
