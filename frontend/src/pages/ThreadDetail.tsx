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
  const [imgWidth, setImgWidth] = useState(75)

  useEffect(() => {
    api.thread(tid).then(setThread).catch(e => setError(e.message))
    api.threadBlocks(tid).then(setBlocks).catch(() => {})
    api.threadImages(tid).then(setImages).catch(() => {})
    api.forums().then(fs => fs.forEach(f => { FORUM_NAMES[f.forum_id] = lang === 'en' ? (f.name_en || f.name) : f.name })).catch(() => {})
  }, [tid, lang])

  const handleResync = async () => {
    setActionLoading('resync')
    try { await api.resyncThread(tid, thread?.forum_id ?? undefined) } catch { /* ignore */ }
    setActionLoading(null)
  }

  const handleExport = async () => {
    setActionLoading('export')
    try { await api.exportThread(tid, 'sync_if_stale', thread?.forum_id ?? undefined) } catch { /* ignore */ }
    setActionLoading(null)
  }

  const [seriesDeleted, setSeriesDeleted] = useState<number | null>(null)

  const handleDelete = async () => {
    if (!confirmDelete) { setConfirmDelete(true); return }
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
    }
  })

  return (
    <>
      <div className="panel">
        <div className="row-actions">
          <Link to={fromSeries && seriesId ? `/series/${seriesId}` : '/threads'} className="btn-subtle">← {fromSeries ? t('series_detail') : t('thread_list')}</Link>
          <button className="btn-subtle" disabled={actionLoading === 'resync'} onClick={handleResync}>{t('resync')}</button>
          <button className="btn-subtle" disabled={actionLoading === 'export'} onClick={handleExport}>{t('export_action')}</button>
          {confirmDelete ? (
            <>
              <button className="btn-danger" disabled={actionLoading === 'delete'} onClick={handleDelete}>{t('confirm_delete')}</button>
              <button className="btn-subtle" onClick={() => setConfirmDelete(false)}>{t('cancel')}</button>
              {thread.series_id && <span style={{ fontSize: 11, color: 'var(--text-tertiary)', marginLeft: 4 }}>{t('auto_delete_empty_series')}</span>}
            </>
          ) : (
            <button className="btn-danger-outline" onClick={() => setConfirmDelete(true)}>{t('delete')}</button>
          )}
        </div>
      </div>

      {seriesDeleted && (
        <div className="panel" style={{ color: 'var(--status-warn)', borderLeft: '3px solid var(--status-warn)' }}>
          {t('series_deleted')} (series_id={seriesDeleted})
        </div>
      )}

      <h2>{t('archive_info')}</h2>
      <div className="table-wrap"><table>
        <tbody>
          {[
            ['TID', <span className="mono">{thread.tid}</span>],
            [t('original_url'), thread.url ? <a href={thread.url} target="_blank" rel="noreferrer">{thread.url}</a> : '-'],
            [t('raw_title'), thread.raw_title],
            [t('publisher'), thread.publisher || '-'],
            [t('forum'), forumName],
            [t('content_kind'), <ContentBadge kind={thread.content_kind} />],
            [t('category'), thread.category || '-'],
            [t('archive_status'), <Badge status={thread.archive_status} />],
            [t('validation_status'), <Badge status={thread.validation_status} />],
            [t('image_count'), String(thread.image_count ?? 0)],
            [t('context_path'), thread.context_path || '-'],
            [t('export_path'), thread.export_path || '-'],
          ].map(([k, v], i) => <tr key={i}><th style={{ width: 120 }}>{k}</th><td style={{ textAlign: 'left' }}>{v}</td></tr>)}
        </tbody>
      </table></div>

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
          <div className="reading-view" style={{ '--img-pct': `${imgWidth}%` } as React.CSSProperties}>
            {floorGroups.map(fg => (
              <div key={fg.pid} className="floor-row">
                <div className="floor-sidebar">
                  <div className="floor-no">{fg.floor_no}F</div>
                  <div className="floor-publisher">{fg.publisher || '-'}</div>
                  {fg.pub_time && <div className="floor-time">{formatDateTime(fg.pub_time)}</div>}
                  <div className="floor-pid">#{fg.pid}</div>
                </div>
                <div className="floor-content">
                  {fg.content && <div className="floor-text">{fg.content}</div>}
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
    </>
  )
}
