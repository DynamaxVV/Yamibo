import { useEffect, useState } from 'react'
import { Link, useNavigate, useLocation } from 'react-router-dom'
import { api, type ThreadDetail as ThreadDetailType, type ContentBlock, type ThreadImage } from '../api/client'
import { Badge, ContentBadge } from '../components/Badge'

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
    api.forums().then(fs => fs.forEach(f => { FORUM_NAMES[f.forum_id] = f.name })).catch(() => {})
  }, [tid])

  const handleResync = async () => {
    setActionLoading('resync')
    try { const res = await api.resyncThread(tid); navigate(`/jobs/${res.job_id}`) } catch { /* ignore */ }
    setActionLoading(null)
  }

  const handleExport = async () => {
    setActionLoading('export')
    try { const res = await api.exportThread(tid, 'sync_if_stale'); navigate(`/jobs/${res.job_id}`) } catch { /* ignore */ }
    setActionLoading(null)
  }

  const handleDelete = async () => {
    if (!confirmDelete) { setConfirmDelete(true); return }
    setActionLoading('delete')
    try { await api.deleteThread(tid); navigate('/threads') } catch { /* ignore */ }
    setActionLoading(null)
    setConfirmDelete(false)
  }

  if (error) return <div className="panel" style={{ color: 'var(--status-error)' }}>{error}</div>
  if (!thread) return <div className="panel" style={{ color: 'var(--text-tertiary)' }}>Loading...</div>

  const forumName = FORUM_NAMES[thread.forum_id ?? 0] || String(thread.forum_id ?? '-')
  const isComic = thread.content_kind === 'comic'
  const fromSeries = location.state?.from === 'series'

  // Group images by pid
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

  // Build floor groups
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
          <Link to={fromSeries ? '/series' : '/threads'} className="btn-subtle">← {fromSeries ? '系列列表' : '贴子列表'}</Link>
          <button className="btn-subtle" disabled={actionLoading === 'resync'} onClick={handleResync}>重新同步</button>
          <button className="btn-subtle" disabled={actionLoading === 'export'} onClick={handleExport}>导出</button>
          {confirmDelete ? (
            <>
              <button className="btn-danger" disabled={actionLoading === 'delete'} onClick={handleDelete}>确认删除？</button>
              <button className="btn-subtle" onClick={() => setConfirmDelete(false)}>取消</button>
            </>
          ) : (
            <button className="btn-danger-outline" onClick={() => setConfirmDelete(true)}>删除</button>
          )}
        </div>
      </div>

      <h2>归档信息</h2>
      <div className="table-wrap"><table>
        <tbody>
          {[
            ['TID', <span className="mono">{thread.tid}</span>],
            ['原贴网址', thread.url ? <a href={thread.url} target="_blank" rel="noreferrer">{thread.url}</a> : '-'],
            ['原始标题', thread.raw_title],
            ['发布者', thread.publisher || '-'],
            ['版块', forumName],
            ['内容类型', <ContentBadge kind={thread.content_kind} />],
            ['归档状态', <Badge status={thread.archive_status} />],
            ['校验状态', <Badge status={thread.validation_status} />],
            ['图片数', String(thread.image_count ?? 0)],
            ['上下文路径', thread.context_path || '-'],
            ['导出路径', thread.export_path || '-'],
          ].map(([k, v], i) => <tr key={i}><th style={{ width: 120 }}>{k}</th><td>{v}</td></tr>)}
        </tbody>
      </table></div>

      {floorGroups.length > 0 ? (
        <>
          <h2>阅读预览</h2>
          <div className="reading-toolbar">
            <span className="toolbar-label">图片宽度</span>
            {[50, 75, 100].map(w => (
              <button key={w} className={imgWidth === w ? 'active' : ''} onClick={() => setImgWidth(w)}>{w}%</button>
            ))}
          </div>
          <div className="reading-view" style={{ '--img-pct': `${imgWidth}%` } as React.CSSProperties}>
            {floorGroups.map(fg => (
              <div key={fg.pid} className="floor-row">
                <div className="floor-sidebar">
                  <div className="floor-no">{fg.floor_no}F</div>
                  <div className="floor-publisher">{fg.publisher || '-'}</div>
                  {fg.pub_time && <div className="floor-time">{fg.pub_time}</div>}
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
            有 {thread.image_count} 张图片但元数据中未找到路径，可能需要重新同步。
          </div>
        )
      )}

      {blocks.length > 0 && (
        <>
          <h2>内容块 ({blocks.length})</h2>
          <div className="table-wrap"><table>
            <thead><tr><th>序号</th><th>PID</th><th>类型</th><th>内容</th></tr></thead>
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
