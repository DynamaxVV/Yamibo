import { useEffect, useState } from 'react'
import { Link, useNavigate, useLocation } from 'react-router-dom'
import { api, type ThreadDetail as ThreadDetailType, type ContentBlock, type ThreadImage } from '../api/client'
import { Badge, ContentBadge } from '../components/Badge'
import { useI18n } from '../context/I18nContext'
import { formatDateTime } from '../utils/time'

const FORUM_NAMES: Record<number, string> = {}

interface FloorGroup {
  pid: number
  floor_no: number
  publisher: string | null
  pub_time: string | null
  content: string
  contentImages: ThreadImage[]
  smallImages: ThreadImage[]
  quoteText: string | null
  replyText: string | null
}

export function ThreadDetail() {
  const { t, lang } = useI18n()
  const tid = parseInt(window.location.pathname.split('/').pop() || '0')
  const navigate = useNavigate()
  const location = useLocation()
  const [thread, setThread] = useState<ThreadDetailType | null>(null)
  const [blocks, setBlocks] = useState<ContentBlock[]>([])
  const [images, setImages] = useState<ThreadImage[]>([])
  const [error, setError] = useState<string | null>(null)
  const [actionLoading, setActionLoading] = useState<string | null>(null)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const isMobile = typeof window !== 'undefined' && window.innerWidth <= 768
  const [imgWidth, setImgWidth] = useState(isMobile ? 100 : 75)
  const [fontSize, setFontSize] = useState(isMobile ? 125 : 100)
  const [pendingSync, setPendingSync] = useState(false)
  const [editingChapter, setEditingChapter] = useState(false)
  const [chapterForm, setChapterForm] = useState({ chapter_name: '', chapter_index: '', author_guess: '', group_name: '' })

  useEffect(() => {
    api.thread(tid).then(setThread).catch(e => setError(e.message))
    api.threadBlocks(tid).then(setBlocks).catch(() => {})
    api.threadImages(tid).then(setImages).catch(() => {})
    api.forums().then(fs => fs.forEach(f => { FORUM_NAMES[f.forum_id] = lang === 'en' ? (f.name_en || f.name) : f.name })).catch(() => {})
  }, [tid, lang])

  useEffect(() => {
    if (!pendingSync) return
    const poll = setInterval(async () => {
      try {
        const jobs = await api.jobs('running')
        const active = jobs.some(j => j.tid === tid)
        if (!active) {
          setPendingSync(false)
          api.thread(tid).then(setThread).catch(() => {})
          api.threadImages(tid).then(setImages).catch(() => {})
        }
      } catch { /* ignore */ }
    }, 3000)
    return () => clearInterval(poll)
  }, [pendingSync, tid])

  const handleResync = async () => {
    setActionLoading('resync')
    try { await api.resyncThread(tid, thread?.forum_id ?? undefined) } catch { /* ignore */ }
    setActionLoading(null)
    setPendingSync(true)
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
      const refreshed = await api.thread(tid)
      setThread(refreshed)
      setEditingChapter(false)
    } catch { /* ignore */ }
    setActionLoading(null)
  }

  const handleExport = async () => {
    setActionLoading('export')
    try { await api.exportThread(tid, 'sync_if_stale', thread?.forum_id ?? undefined) } catch { /* ignore */ }
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

  if (error) return <div className="panel" style={{ color: 'var(--status-error)' }}>{error}</div>
  if (!thread) return <div className="panel" style={{ color: 'var(--text-tertiary)' }}>{t('loading')}</div>

  const forumName = FORUM_NAMES[thread.forum_id ?? 0] || String(thread.forum_id ?? '-')
  const isComic = thread.content_kind === 'comic'
  const isNovel = thread.content_kind === 'novel'
  const isExportable = thread.forum_id === 30 || thread.forum_id === 55
  const fromSeries = location.state?.from === 'series'
  const seriesId = location.state?.seriesId as number | undefined

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

  const floorGroups: FloorGroup[] = (thread.floors || []).map(f => {
    const imgGroup = imagesByPid.get(f.pid) || { content: [], small: [] }
    return {
      pid: f.pid,
      floor_no: f.floor_no,
      publisher: f.publisher,
      pub_time: f.pub_time,
      content: f.content || '',
      contentImages: imgGroup.content,
      smallImages: imgGroup.small,
      quoteText: f.quote_text || null,
      replyText: f.reply_text || null,
    }
  })

  return (
    <>
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
            [t('archive_status'), pendingSync ? <Badge status="running">{t('resyncing')}</Badge> : <Badge status={thread.archive_status} />],
            [t('validation_status'), <Badge status={thread.validation_status} />],
          ].map(([k, v], i) => <tr key={i}><th style={{ width: 120 }}>{k}</th><td style={{ textAlign: 'left' }}>{v}</td></tr>)}
        </tbody>
      </table></div>

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
          <h2>{t('read_preview')}</h2>
          {isComic && (
            <div className="reading-toolbar">
              <span className="toolbar-label">{t('image_width')}</span>
              {[50, 75, 100].map(w => (
                <button key={w} className={imgWidth === w ? 'active' : ''} onClick={() => setImgWidth(w)}>{w}%</button>
              ))}
            </div>
          )}
          <div className="reading-toolbar">
            <span className="toolbar-label">{t('font_size')}</span>
            {[75, 100, 125, 150, 200].map(s => (
              <button key={s} className={fontSize === s ? 'active' : ''} onClick={() => setFontSize(s)}>{s}%</button>
            ))}
          </div>
          <div className="reading-view" style={{ '--img-pct': `${imgWidth}%`, fontSize: `${fontSize}%` } as React.CSSProperties}>
            {floorGroups.map(fg => (
              <div key={fg.pid} className="floor-row">
                <div className="floor-sidebar">
                  <div className="floor-no">{fg.floor_no}F</div>
                  <div className="floor-publisher">{fg.publisher || '-'}</div>
                  {fg.pub_time && <div className="floor-time">{formatDateTime(fg.pub_time)}</div>}
                  <div className="floor-pid">#{fg.pid}</div>
                </div>
                <div className="floor-content">
                  {fg.quoteText && (
                    <div className="floor-quote">
                      <blockquote>{fg.quoteText}</blockquote>
                    </div>
                  )}
                  {(fg.replyText || (!fg.quoteText && fg.content)) && <div className="floor-text">{fg.replyText || fg.content}</div>}
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
    </>
  )
}
