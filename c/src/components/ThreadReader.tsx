import { useEffect, useLayoutEffect, useRef, useState, type CSSProperties } from 'react'
import { useI18n } from '../context/I18nContext'
import { useTheme } from '../context/ThemeContext'
import { type ThreadImage, type FloorSummary, type FontAsset, api } from '../api/client'
import { PaginationControls } from './PaginationControls'
import { formatDateTime } from '../utils/time'

// ── Reading Preset Types ──────────────────────────────────────────────

type ReadingPresetKey = 'paper' | 'sepia' | 'night' | 'ink'
type ThemeVariant = 'light' | 'dark'
type ReadingPlatform = 'desktop' | 'mobile'

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

type ReadingPreset = {
  label: string
  light: ReadingPresetVariant
  dark: ReadingPresetVariant
  preferredFontLabel?: string
}

type ReadingMode = ReadingPresetKey | 'custom'

export type ReadingConfig = {
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

// ── Reading Presets ───────────────────────────────────────────────────

const READING_PRESETS: Record<ReadingPresetKey, ReadingPreset> = {
  paper: {
    label: '纸本',
    light: { bg: '#f6f1e8', text: '#2c2620', muted: '#6e6258', accent: '#8a5b3d', sidebarBg: '#ece2d3', quoteBg: '#efe7db', lineHeight: 1.92, indent: 2 },
    dark: { bg: '#1e1914', text: '#e8ddd1', muted: '#b6aa9d', accent: '#d0a172', sidebarBg: '#2a231d', quoteBg: '#26201a', lineHeight: 1.92, indent: 2 },
    preferredFontLabel: '文黑体',
  },
  sepia: {
    label: '琥珀',
    light: { bg: '#efe3cf', text: '#31271f', muted: '#74685e', accent: '#936a33', sidebarBg: '#e6d6bf', quoteBg: '#ece0cb', lineHeight: 1.98, indent: 2 },
    dark: { bg: '#241b13', text: '#e7d8c3', muted: '#b8aa98', accent: '#d6aa68', sidebarBg: '#2e241a', quoteBg: '#2a2118', lineHeight: 1.98, indent: 2 },
    preferredFontLabel: '仓耳华新体',
  },
  night: {
    label: '夜读',
    light: { bg: '#f5f7fb', text: '#27303b', muted: '#647084', accent: '#597fe8', sidebarBg: '#e7edf5', quoteBg: '#edf2f8', lineHeight: 2.02, indent: 2.2 },
    dark: { bg: '#1f2228', text: '#e6e0d5', muted: '#b4aa9b', accent: '#8fb4ff', sidebarBg: '#171a1f', quoteBg: '#252a33', lineHeight: 2.02, indent: 2.2 },
    preferredFontLabel: '攸望轻吟体',
  },
  ink: {
    label: '墨痕',
    light: { bg: '#f8f8f6', text: '#181818', muted: '#676767', accent: '#6a4cf2', sidebarBg: '#efefec', quoteBg: '#f2f2f0', lineHeight: 1.88, indent: 1.8 },
    dark: { bg: '#111214', text: '#ecebea', muted: '#b7b7b2', accent: '#a395ff', sidebarBg: '#1a1b1f', quoteBg: '#1d2024', lineHeight: 1.88, indent: 1.8 },
    preferredFontLabel: '九九儷中黑 v1.6',
  },
}

// ── Reading Config Helpers ────────────────────────────────────────────

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

function buildReadingConfig(mode: ReadingPresetKey, variant: ThemeVariant, textSize = 100, fontFamily = 'system'): ReadingConfig {
  const preset = READING_PRESETS[mode][variant]
  return { mode, textSize, fontFamily, bg: preset.bg, text: preset.text, muted: preset.muted, accent: preset.accent, sidebarBg: preset.sidebarBg, quoteBg: preset.quoteBg, lineHeight: preset.lineHeight, indent: preset.indent }
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
  } catch { return null }
}

function readingConfigMatchesPreset(config: ReadingConfig, mode: ReadingPresetKey, variant: ThemeVariant, textSize: number): boolean {
  const preset = READING_PRESETS[mode][variant]
  return config.mode === mode && config.textSize === textSize && config.bg === preset.bg && config.text === preset.text && config.muted === preset.muted && config.accent === preset.accent && config.sidebarBg === preset.sidebarBg && config.quoteBg === preset.quoteBg && config.lineHeight === preset.lineHeight && config.indent === preset.indent
}

function deriveReadingMode(config: ReadingConfig, variant: ThemeVariant): ReadingMode {
  for (const mode of Object.keys(READING_PRESETS) as ReadingPresetKey[]) {
    if (readingConfigMatchesPreset(config, mode, variant, 100)) return mode
  }
  return 'custom'
}

// ── Novel Paragraph Helpers ───────────────────────────────────────────

function looksLikeNovelHeading(line: string): boolean {
  const value = line.trim()
  if (!value) return false
  if (/^(Episode\s*\d+|第\s*[0-9一二三四五六七八九十百千零]+[话章节卷篇])/i.test(value)) return true
  return false
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
    if (!value) { flushBuffer(); continue }
    if (looksLikeNovelHeading(value)) { flushBuffer(); paragraphs.push({ text: value, kind: 'heading' }); continue }
    if (isMobile) { buffer.push(value); continue }
    paragraphs.push({ text: value, kind: 'paragraph' })
  }
  flushBuffer()
  return paragraphs
}

function renderNovelParagraphs(text: string, isMobile: boolean) {
  const paragraphs = splitNovelParagraphs(text, isMobile)
  return paragraphs.map((part, index) => (
    <p key={`${index}-${part.text.slice(0, 12)}`} className={part.kind === 'heading' ? 'floor-paragraph floor-paragraph-heading' : 'floor-paragraph'}>
      {part.text}
    </p>
  ))
}

// ── Types ─────────────────────────────────────────────────────────────

interface ImageSlot {
  remote_url: string
  local_path: string | null
  status: string
  asset_id?: string
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
  imageSlots: ImageSlot[]
}

export interface ThreadReaderProps {
  tid: number
  source: 'local' | 'remote'
  contentKind: string | null
  floors: FloorSummary[]
  images?: ThreadImage[]
  page: number
  totalPages: number | null
  onPageChange?: (page: number) => void
  onImageRecovered?: () => Promise<void> | void
}

// ── Small Image Detection ────────────────────────────────────────────

function _isSmallImageUrl(url: string): boolean {
  const lower = url.toLowerCase()
  return /smiley|smilies|emot|icon_|\.gif\b/.test(lower)
    || lower.includes('/static/image/')
    || lower.includes('images/smilies')
}

// ── LazyImage ─────────────────────────────────────────────────────────

function _fileNameFromUrl(url: string): string {
  try {
    const path = url.split('?')[0]
    const name = path.split('/').pop() || ''
    return decodeURIComponent(name) || name
  } catch { return '' }
}

function LazyImage({ src, alt, className }: { src: string; alt: string; className?: string }) {
  const [loaded, setLoaded] = useState(false)
  const [error, setError] = useState(false)
  const fileName = _fileNameFromUrl(src)

  if (error) {
    return (
      <span className={`lazy-image-placeholder lazy-image-error${className ? ` ${className}` : ''}`}>
        <span className="lazy-image-label">{fileName || alt || '⚠'}</span>
      </span>
    )
  }
  return (
    <span style={{ display: loaded ? 'contents' : 'inline-block', position: 'relative' }}>
      {!loaded && (
        <span className={`lazy-image-placeholder${className ? ` ${className}` : ''}`}>
          <span className="lazy-image-label">{fileName}</span>
        </span>
      )}
      <img
        src={src}
        alt={alt}
        loading="lazy"
        className={className}
        style={loaded ? undefined : { position: 'absolute', opacity: 0, pointerEvents: 'none', width: '100%', height: '100%', top: 0, left: 0 }}
        onLoad={() => setLoaded(true)}
        onError={() => setError(true)}
      />
    </span>
  )
}

// ── Component ─────────────────────────────────────────────────────────

export function ThreadReader({ tid, source, contentKind, floors: floorSource, images, page, totalPages: totalPagesProp, onPageChange, onImageRecovered }: ThreadReaderProps) {
  const { t, lang } = useI18n()
  const { dark } = useTheme()
  const isMobile = typeof window !== 'undefined' && window.innerWidth <= 768
  const readingPlatform: ReadingPlatform = isMobile ? 'mobile' : 'desktop'
  const themeVariant: ThemeVariant = dark ? 'dark' : 'light'
  const isComic = contentKind === 'comic'
  const isNovel = contentKind === 'novel'

  const [imgWidth, setImgWidth] = useState(isMobile ? 100 : 75)
  const [readingModeOpen, setReadingModeOpen] = useState(false)
  const [readingConfig, setReadingConfig] = useState<ReadingConfig>(() => loadReadingConfig(readingPlatform, themeVariant) ?? buildReadingConfig('paper', themeVariant, getDefaultReadingTextSize(readingPlatform)))
  const [fontAssets, setFontAssets] = useState<FontAsset[]>([])
  const readingConfigRef = useRef(readingConfig)
  const floorRefs = useRef(new Map<number, HTMLDivElement | null>())
  const scrollSuppressed = useRef(false)
  const previewScrollSuppressed = useRef(false)
  const [pendingFloorNo, setPendingFloorNo] = useState<number | null>(null)
  const [currentFloorNo, setCurrentFloorNo] = useState<number | null>(null)
  const [retryStatuses, setRetryStatuses] = useState<Record<string, string>>({})
  const retryTimers = useRef(new Map<string, ReturnType<typeof window.setTimeout>>())
  const customModeLabel = `${t('custom')}（${dark ? t('night_mode') : t('day_mode')}）`

  useEffect(() => () => {
    retryTimers.current.forEach(timer => window.clearTimeout(timer))
    retryTimers.current.clear()
  }, [])

  const totalFloorCount = floorSource.length
  const pageSize = isNovel ? 10 : Math.max(1, totalFloorCount)
  const totalPages = isNovel ? (totalPagesProp ?? Math.max(1, Math.ceil(totalFloorCount / pageSize))) : 1
  const effectivePreviewPage = isNovel ? Math.min(Math.max(1, page), totalPages) : 1

  const imagesByPid = new Map<number, { content: ThreadImage[]; small: ThreadImage[] }>()
  for (const img of images || []) {
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
      imageSlots: (f.image_slots || []).filter(slot => slot && typeof slot.remote_url === 'string'),
    }
  })

  const visibleFloorGroups = isNovel && totalPagesProp != null
    ? floorGroups.slice((effectivePreviewPage - 1) * pageSize, effectivePreviewPage * pageSize)
    : floorGroups
  const floorTargetPidByNo = new Map<number, number>()
  for (const floor of visibleFloorGroups) {
    if (!floorTargetPidByNo.has(floor.floor_no)) floorTargetPidByNo.set(floor.floor_no, floor.pid)
  }

  // 只要帖子有图片就在阅读预览下显示图片宽度调节
  const showImageWidthToolbar = floorGroups.some(fg => fg.imageSlots.length > 0 || fg.contentImages.length > 0)

  // Sync readingConfig when theme/platform changes
  useEffect(() => {
    readingConfigRef.current = readingConfig
    if (typeof window !== 'undefined') {
      window.localStorage.setItem(READING_CONFIG_STORAGE_KEYS[readingPlatform][themeVariant], JSON.stringify(readingConfig))
    }
  }, [readingConfig, readingPlatform, themeVariant])

  useEffect(() => {
    const loaded = loadReadingConfig(readingPlatform, themeVariant)
    if (loaded) { setReadingConfig(loaded); return }
    const current = readingConfigRef.current
    if (current.mode === 'custom') {
      setReadingConfig(buildReadingConfig('paper', themeVariant, getDefaultReadingTextSize(readingPlatform)))
      return
    }
    setReadingConfig(buildReadingConfig(current.mode, themeVariant, current.textSize, current.fontFamily))
  }, [readingPlatform, themeVariant])

  // Load fonts
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
        } catch { /* ignore */ }
      }
      if (readingConfigRef.current.mode !== 'custom' && readingConfigRef.current.fontFamily === 'system') {
        const currentMode = readingConfigRef.current.mode
        const preferred = list.find(font => font.label === READING_PRESETS[currentMode].preferredFontLabel) || list[0]
        if (preferred) setReadingConfig(prev => ({ ...prev, fontFamily: preferred.family }))
      }
    }).catch(() => {})
    return () => { active = false }
  }, [])

  useEffect(() => {
    if (fontAssets.length === 0) return
    const current = readingConfigRef.current
    if (current.mode === 'custom' || current.fontFamily !== 'system') return
    const presetMode = current.mode as ReadingPresetKey
    const preferred = fontAssets.find(font => font.label === READING_PRESETS[presetMode].preferredFontLabel) || fontAssets[0]
    if (preferred) setReadingConfig(prev => prev.mode === 'custom' || prev.fontFamily !== 'system' ? prev : { ...prev, fontFamily: preferred.family })
  }, [fontAssets, themeVariant, readingPlatform])

  const applyReadingPreset = (presetKey: ReadingPresetKey) => {
    const preset = READING_PRESETS[presetKey]
    const variantPreset = preset[themeVariant]
    const preferred = fontAssets.find(font => font.label === preset.preferredFontLabel)
    setReadingConfig({
      mode: presetKey, textSize: 100, fontFamily: preferred?.family || 'system',
      bg: variantPreset.bg, text: variantPreset.text, muted: variantPreset.muted,
      accent: variantPreset.accent, sidebarBg: variantPreset.sidebarBg,
      quoteBg: variantPreset.quoteBg, lineHeight: variantPreset.lineHeight, indent: variantPreset.indent,
    })
  }

  const updateReadingConfig = (patch: Partial<ReadingConfig>) => {
    setReadingConfig(prev => {
      const next = { ...prev, ...patch }
      next.mode = deriveReadingMode(next, themeVariant)
      return next
    })
  }

  const setCustomReadingMode = () => setReadingConfig(prev => ({ ...prev, mode: 'custom' }))

  const setPreviewPage = (p: number) => {
    if (onPageChange) {
      onPageChange(p)
    }
  }

  const retryMissingImage = async (slot: ImageSlot) => {
    if (!slot.asset_id) return
    const current = retryStatuses[slot.remote_url]
    if (current === 'checking' || current === 'queued' || current === 'downloading') return
    setRetryStatuses(prev => ({ ...prev, [slot.remote_url]: 'checking' }))
    try {
      const created = await api.retryThreadImage(tid, slot.asset_id)
      setRetryStatuses(prev => ({ ...prev, [slot.remote_url]: created.status === 'running' ? 'downloading' : 'queued' }))
      const poll = async () => {
        try {
          const job = await api.job(created.job_id)
          if (job.status === 'succeeded') {
            setRetryStatuses(prev => ({ ...prev, [slot.remote_url]: 'succeeded' }))
            // Update only the thread/image state. A full reload would follow
            // the page's #top fragment and discard the reader's position.
            try {
              await onImageRecovered?.()
            } catch {
              // The backfill job already succeeded; a transient UI refresh
              // failure must not turn the image status into a false failure.
            }
            return
          }
          if (job.status === 'failed' || job.status === 'partial' || job.status === 'interrupted') {
            setRetryStatuses(prev => ({ ...prev, [slot.remote_url]: 'failed' }))
            return
          }
          setRetryStatuses(prev => ({
            ...prev,
            [slot.remote_url]: job.stage?.includes('download') ? 'downloading' : (job.status === 'queued' ? 'queued' : 'checking'),
          }))
          const timer = window.setTimeout(() => { void poll() }, 1000)
          retryTimers.current.set(slot.remote_url, timer)
        } catch {
          setRetryStatuses(prev => ({ ...prev, [slot.remote_url]: 'failed' }))
        }
      }
      void poll()
    } catch {
      setRetryStatuses(prev => ({ ...prev, [slot.remote_url]: 'failed' }))
    }
  }

  // Current floor tracking
  useEffect(() => {
    if (visibleFloorGroups.length === 0) { setCurrentFloorNo(null); return }
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
        if (distance < bestDistance) { bestDistance = distance; candidate = floor.floor_no }
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
      if (!node) { window.requestAnimationFrame(attemptScroll); return }
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
    if (isNovel && targetPage !== effectivePreviewPage && onPageChange) {
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
  } as CSSProperties

  const renderImageSlot = (slot: ImageSlot, i: number) => {
    const url = slot.local_path ? `/media/threads/${tid}/${slot.local_path}` : slot.remote_url
    const isSmall = _isSmallImageUrl(url)

    if (slot.local_path) {
      const src = `/media/threads/${tid}/${slot.local_path}`
      if (isSmall) return <LazyImage key={`${slot.remote_url}-${i}`} src={src} alt="" className="small-img" />
      return (
        <a key={`${slot.remote_url}-${i}`} href={src} target="_blank" rel="noreferrer" className="floor-image-link">
          <LazyImage src={src} alt="" />
        </a>
      )
    }
    if (source === 'remote' && slot.status === 'remote') {
      if (isSmall) return <LazyImage key={`${slot.remote_url}-${i}`} src={slot.remote_url} alt="" className="small-img" />
      return (
        <a key={`${slot.remote_url}-${i}`} href={slot.remote_url} target="_blank" rel="noreferrer" className="floor-image-link">
          <LazyImage src={slot.remote_url} alt="" />
        </a>
      )
    }
    return (
      <div key={`${slot.remote_url}-${i}`} className="missing-image-url-card">
        <div className="missing-image-url-label">{lang === 'en' ? 'Missing image URL' : '缺失图片 URL'}</div>
        <a href={slot.remote_url} target="_blank" rel="noreferrer">{slot.remote_url}</a>
        <button
          type="button"
          className="btn-subtle"
          disabled={!slot.asset_id || ['checking', 'queued', 'downloading'].includes(retryStatuses[slot.remote_url] || '')}
          onClick={() => { void retryMissingImage(slot) }}
        >
          {retryStatuses[slot.remote_url] === 'checking' ? t('retry_image_checking')
            : retryStatuses[slot.remote_url] === 'queued' ? t('retry_image_queued')
              : retryStatuses[slot.remote_url] === 'downloading' ? t('retry_image_downloading')
                : retryStatuses[slot.remote_url] === 'succeeded' ? t('retry_image_succeeded')
                  : retryStatuses[slot.remote_url] === 'failed' ? t('retry_image_failed')
                    : t('retry_image')}
        </button>
      </div>
    )
  }

  if (floorGroups.length === 0) return null

  return (
    <>
      {visibleFloorGroups.length > 0 && currentFloorNo != null && (
        <div className="floor-wheel" onWheel={e => {
          e.preventDefault()
          if (e.deltaY > 0 && wheelNextFloor != null && wheelNextFloor !== currentFloorNo) jumpToFloor(wheelNextFloor)
          else if (e.deltaY < 0 && wheelPrevFloor != null && wheelPrevFloor !== currentFloorNo) jumpToFloor(wheelPrevFloor)
        }}>
          <button className="floor-wheel-btn floor-wheel-btn-prev" disabled={wheelPrevFloor == null || wheelPrevFloor === currentFloorNo}
            onClick={() => wheelPrevFloor != null && jumpToFloor(wheelPrevFloor)}>
            {wheelPrevFloor ?? currentFloorNo}F
          </button>
          <div className="floor-wheel-current">
            <span className="floor-wheel-label">{t('floor_jump')}</span>
            <strong>{currentFloorNo}F</strong>
          </div>
          <button className="floor-wheel-btn floor-wheel-btn-next" disabled={wheelNextFloor == null || wheelNextFloor === currentFloorNo}
            onClick={() => wheelNextFloor != null && jumpToFloor(wheelNextFloor)}>
            {wheelNextFloor ?? currentFloorNo}F
          </button>
        </div>
      )}

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
            <select className="reading-mode-select" value={readingConfig.mode}
              onChange={e => {
                if (e.target.value === 'custom') setCustomReadingMode()
                else if (isReadingPresetKey(e.target.value)) applyReadingPreset(e.target.value)
              }}>
              {(Object.entries(READING_PRESETS) as Array<[ReadingPresetKey, ReadingPreset]>).map(([key, preset]) => (
                <option key={key} value={key}>{preset.label}</option>
              ))}
              <option value="custom">{customModeLabel}</option>
            </select>
          </label>
          <div className="reading-mode-inline-size">
            <span>{t('font_size')}</span>
            <input type="range" min="75" max="200" step="5" value={readingConfig.textSize}
              onChange={e => updateReadingConfig({ textSize: Number(e.target.value) })} />
            <strong>{readingConfig.textSize}%</strong>
          </div>
        </div>
        {readingModeOpen && (
          <div className="reading-mode-body">
            <div className="reading-mode-presets">
              {(Object.entries(READING_PRESETS) as Array<[ReadingPresetKey, ReadingPreset]>).map(([key, preset]) => (
                <button type="button" key={key} className={readingConfig.mode === key ? 'active' : ''}
                  onClick={() => applyReadingPreset(key)}>{preset.label}</button>
              ))}
              <button type="button" className={readingConfig.mode === 'custom' ? 'active' : ''}
                onClick={setCustomReadingMode}>{customModeLabel}</button>
            </div>
            <div className="reading-mode-controls">
              <label>字体<select value={readingConfig.fontFamily} onChange={e => updateReadingConfig({ fontFamily: e.target.value })}>
                <option value="system">系统默认</option>
                {fontAssets.map(font => <option key={font.family} value={font.family}>{font.label}</option>)}
              </select></label>
              <label>背景<input type="color" value={readingConfig.bg} onChange={e => updateReadingConfig({ bg: e.target.value })} /></label>
              <label>文字<input type="color" value={readingConfig.text} onChange={e => updateReadingConfig({ text: e.target.value })} /></label>
              <label>辅助<input type="color" value={readingConfig.muted} onChange={e => updateReadingConfig({ muted: e.target.value })} /></label>
              <label>侧栏<input type="color" value={readingConfig.sidebarBg} onChange={e => updateReadingConfig({ sidebarBg: e.target.value })} /></label>
              <label>引文<input type="color" value={readingConfig.quoteBg} onChange={e => updateReadingConfig({ quoteBg: e.target.value })} /></label>
              <label>强调<input type="color" value={readingConfig.accent} onChange={e => updateReadingConfig({ accent: e.target.value })} /></label>
              <label>段落首行缩进<input type="number" min="0" max="6" step="0.1" value={readingConfig.indent} onChange={e => updateReadingConfig({ indent: Number(e.target.value) })} /></label>
              <label>行距<input type="number" min="1.2" max="3" step="0.05" value={readingConfig.lineHeight} onChange={e => updateReadingConfig({ lineHeight: Number(e.target.value) })} /></label>
            </div>
          </div>
        )}
      </div>

      <PaginationControls page={effectivePreviewPage} totalPages={totalPages} onPageChange={setPreviewPage}
        scrollTargetId="thread-reading-top" suppressScrollRef={previewScrollSuppressed}
        className="reading-pagination pagination-controls" />

      {showImageWidthToolbar && (
        <div className="reading-toolbar">
          <span className="toolbar-label">{t('image_width')}</span>
          {[50, 75, 100].map(w => (
            <button key={w} className={imgWidth === w ? 'active' : ''} onClick={() => setImgWidth(w)}>{w}%</button>
          ))}
        </div>
      )}

      <div className="reading-view" style={{ '--img-pct': `${imgWidth}%`, fontSize: `${readingConfig.textSize}%`, ...readingViewStyle } as CSSProperties}>
        {visibleFloorGroups.map(fg => {
          const isFloorTarget = floorTargetPidByNo.get(fg.floor_no) === fg.pid
          return (
            <div key={fg.pid} className="floor-row" id={isFloorTarget ? `floor-${fg.floor_no}` : `floor-${fg.floor_no}-${fg.pid}`}
              ref={node => { if (isFloorTarget) floorRefs.current.set(fg.floor_no, node) }}>
              <div className="floor-sidebar">
                <div className="floor-no">{fg.floor_no}F</div>
                {fg.publisher_uid && (
                  <img
                    className="floor-avatar"
                    src={`https://bbs.yamibo.com/uc_server/avatar.php?uid=${fg.publisher_uid}&size=middle`}
                    loading="lazy"
                    alt=""
                  />
                )}
                <div className="floor-publisher">{fg.publisher || '-'}</div>
                {fg.pub_time && <div className="floor-time">{formatDateTime(fg.pub_time)}</div>}
                <div className="floor-pid" id={`pid-${fg.pid}`}>#{fg.pid}</div>
              </div>
              <div className={`floor-content${isNovel ? ' floor-content-novel' : ''}`}>
              {fg.richBodyHtml ? (
                <div className={`floor-text floor-rich-body${isNovel ? ' floor-rich-body-novel' : ''}`}
                  dangerouslySetInnerHTML={{ __html: fg.richBodyHtml }} />
              ) : (
                <>
                  {fg.quoteText && (
                    <div className="floor-quote"><blockquote>{fg.quoteText}</blockquote></div>
                  )}
                  {(fg.replyText || (!fg.quoteText && fg.content)) && (
                    <div className="floor-text">
                      {isNovel ? renderNovelParagraphs(fg.replyText || fg.content, isMobile) : (fg.replyText || fg.content)}
                    </div>
                  )}
                </>
              )}
              {fg.imageSlots.length > 0 ? (
                <div className="floor-images">
                  {fg.imageSlots
                    .filter(slot => slot.status !== 'shared' && slot.status !== 'skipped')
                    .map((slot, i) => renderImageSlot(slot, i))}
                </div>
              ) : fg.contentImages.length > 0 ? (
                <div className="floor-images">
                  {fg.contentImages.map((img, i) => {
                    const src = `/media/threads/${tid}/${img.url}`
                    return (
                      <a key={i} href={src} target="_blank" rel="noreferrer" className="floor-image-link">
                        <LazyImage src={src} alt="" />
                      </a>
                    )
                  })}
                </div>
              ) : null}
              {fg.imageSlots.length === 0 && fg.smallImages.length > 0 && (
                <div className="floor-small-images">
                  {fg.smallImages.map((img, i) => {
                    const src = `/media/threads/${tid}/${img.url}`
                    return <LazyImage key={i} src={src} alt="" className="small-img" />
                  })}
                </div>
              )}
              </div>
            </div>
          )
        })}
      </div>

      {isNovel && (
        <PaginationControls page={effectivePreviewPage} totalPages={totalPages} onPageChange={setPreviewPage}
          scrollTargetId="thread-reading-top" suppressScrollRef={previewScrollSuppressed}
          className="reading-pagination pagination-controls" />
      )}
    </>
  )
}
