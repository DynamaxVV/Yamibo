import { useEffect, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { api, type RemoteThreadDetail as RemoteThreadDetailType, type RemoteForum } from '../api/client'
import { Badge } from '../components/Badge'
import { LoadingIndicator } from '../components/LoadingIndicator'
import { ThreadReader } from '../components/ThreadReader'
import { useI18n } from '../context/I18nContext'

export function RemoteThreadDetail() {
  const { t, lang, tx } = useI18n()
  const { tid: tidParam } = useParams<{ tid: string }>()
  const tid = parseInt(tidParam || '0', 10)
  const [searchParams, setSearchParams] = useSearchParams()
  const forumId = parseInt(searchParams.get('forum_id') || '0', 10) || undefined
  const page = Math.max(1, parseInt(searchParams.get('page') || '1', 10))

  const [forums, setForums] = useState<RemoteForum[]>([])
  const [data, setData] = useState<RemoteThreadDetailType | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [actionError, setActionError] = useState<string | null>(null)
  const [createdJobId, setCreatedJobId] = useState<string | null>(null)
  const [actionLoading, setActionLoading] = useState<string | null>(null)

  useEffect(() => {
    api.remoteForums().then(setForums).catch(() => {})
  }, [])

  useEffect(() => {
    let active = true
    setError(null)
    if (!data) setLoading(true)
    api.remoteThread(tid, { forum_id: forumId, page })
      .then(d => { if (active) setData(d) })
      .catch(e => { if (active) setError(e.message) })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [tid, forumId, page])

  const setPage = (p: number) => {
    const next = new URLSearchParams(searchParams)
    if (p > 1) next.set('page', String(p))
    else next.delete('page')
    setSearchParams(next, { replace: true })
  }

  const handleArchive = async (mode: 'text_only' | 'full') => {
    setActionError(null)
    setCreatedJobId(null)
    setActionLoading('archive')
    try {
      const result = await api.createThreadArchiveBatch({ tids: [tid], forum_id: data?.forum_id ?? undefined, mode })
      setCreatedJobId(result.created_job_ids[0] || result.reused_job_ids[0] || null)
    } catch (e) { setActionError(e instanceof Error ? e.message : String(e)) }
    setActionLoading(null)
  }

  const handleResync = async () => {
    setActionError(null)
    setCreatedJobId(null)
    setActionLoading('resync')
    try {
      const result = await api.resyncThread(tid, data?.forum_id ?? undefined)
      setCreatedJobId(result.job_id)
    } catch (e) { setActionError(e instanceof Error ? e.message : String(e)) }
    setActionLoading(null)
  }

  const forumName = (fid: number | null) => {
    if (fid == null) return '-'
    const f = forums.find(x => x.forum_id === fid)
    return f ? (lang === 'en' ? (f.name_en || f.name) : f.name) : String(fid)
  }

  if (loading) return <LoadingIndicator label={t('loading')} />
  if (error) return <div className="panel" style={{ color: 'var(--status-error)' }}>{error}</div>
  if (!data) return <div className="panel" style={{ color: 'var(--text-tertiary)' }}>{t('no_data')}</div>

  const isArchived = data.local_thread?.archived
  const isComic = data.forum_id === 30
  const isTextOnly = data.local_thread?.capture_mode === 'text_only'

  return (
    <>
      <div className="panel">
        <div className="row-actions">
          <Link to={`/forum?forum_id=${data.forum_id || 30}&page=1`} className="btn-subtle">← {t('remoteForum')}</Link>
          {isArchived && (
            <Link to={`/threads/${tid}`} className="btn-subtle">{lang === 'en' ? 'Local Detail' : '本地详情'}</Link>
          )}
          {isArchived && <button className="btn-subtle" disabled={actionLoading !== null} onClick={handleResync}>{t('resync')}</button>}
          {isComic && !isArchived && (
            <button className="btn-subtle" disabled={actionLoading !== null} onClick={() => void handleArchive('text_only')}>
              {lang === 'en' ? 'Archive text' : '仅归档文字'}
            </button>
          )}
          {(!isArchived || isTextOnly) && (
            <button className="btn-subtle" disabled={actionLoading !== null} onClick={() => void handleArchive('full')}>
              {isTextOnly ? (lang === 'en' ? 'Download images' : '下载图片，升级完整归档') : t('archive')}
            </button>
          )}
        </div>
      </div>

      <header className="space-y-3 my-5">
        <h1 className="text-2xl font-semibold break-words">{data.display_title || data.raw_title}</h1>
        <div className="flex flex-wrap gap-3 text-sm text-muted-foreground"><span>#{data.tid}</span><span>{forumName(data.forum_id)}</span><span>{data.publisher || '—'}</span><span>{data.pub_time || '—'}</span><Badge status={isArchived ? (data.archive_status || 'complete') : 'none'} />{isTextOnly && <span>{lang === 'en' ? 'Text only · images not saved' : '仅文字 · 图片未保存'}</span>}</div>
        <p className="text-sm text-muted-foreground">{lang === 'en' ? 'Archive and resync create background tasks. Open the task to follow progress.' : '归档和重新同步会创建后台任务，可进入任务详情查看进度。'}</p>
        {actionError && <p role="alert" className="text-sm text-rose-800 dark:text-rose-300 break-words">{actionError}</p>}
        {createdJobId && <p role="status" className="text-sm break-all">{tx('任务已提交：', 'Task queued: ')}<Link className="underline" to={`/jobs/${createdJobId}`}>{createdJobId}</Link></p>}
      </header>
      {data.floors.length > 0 && (
        <ThreadReader
          tid={tid}
          source="remote"
          contentKind={data.content_kind}
          forumId={data.forum_id}
          floors={data.floors}
          page={data.page}
          totalPages={data.total_pages}
          onPageChange={setPage}
        />
      )}      {data.floors.length === 0 && <div className="panel">{tx('当前未返回可阅读的楼层。', 'No readable floors were returned.')}</div>}
      <details className="panel mt-6"><summary className="cursor-pointer font-medium">{tx('原帖信息', 'Source information')}</summary>
        <dl className="space-y-3 mt-4 text-sm break-words">
          {data.raw_title !== data.display_title && <div><dt className="text-muted-foreground">{t('raw_title')}</dt><dd>{data.raw_title}</dd></div>}
          <div><dt className="text-muted-foreground">{t('content_kind')}</dt><dd>{data.content_kind || '—'}</dd></div>
          {data.url && <div><dt className="text-muted-foreground">{t('original_url')}</dt><dd className="break-all"><a href={data.url} target="_blank" rel="noreferrer">{data.url}</a></dd></div>}
        </dl>
      </details>

    </>
  )
}
