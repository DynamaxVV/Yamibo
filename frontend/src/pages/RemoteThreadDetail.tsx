import { useEffect, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { api, type RemoteThreadDetail as RemoteThreadDetailType, type RemoteForum } from '../api/client'
import { Badge } from '../components/Badge'
import { ThreadReader } from '../components/ThreadReader'
import { useI18n } from '../context/I18nContext'

export function RemoteThreadDetail() {
  const { t, lang } = useI18n()
  const { tid: tidParam } = useParams<{ tid: string }>()
  const tid = parseInt(tidParam || '0', 10)
  const [searchParams, setSearchParams] = useSearchParams()
  const forumId = parseInt(searchParams.get('forum_id') || '0', 10) || undefined
  const page = Math.max(1, parseInt(searchParams.get('page') || '1', 10))

  const [forums, setForums] = useState<RemoteForum[]>([])
  const [data, setData] = useState<RemoteThreadDetailType | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
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

  const handleArchive = async () => {
    setActionLoading('archive')
    try {
      await api.createThreadArchiveBatch({ tids: [tid], forum_id: data?.forum_id ?? undefined })
    } catch { /* ignore */ }
    setActionLoading(null)
  }

  const handleResync = async () => {
    setActionLoading('resync')
    try {
      await api.resyncThread(tid, data?.forum_id ?? undefined)
    } catch { /* ignore */ }
    setActionLoading(null)
  }

  const forumName = (fid: number | null) => {
    if (fid == null) return '-'
    const f = forums.find(x => x.forum_id === fid)
    return f ? (lang === 'en' ? (f.name_en || f.name) : f.name) : String(fid)
  }

  if (loading) return <div className="panel" style={{ color: 'var(--text-tertiary)' }}>{t('loading')}</div>
  if (error) return <div className="panel" style={{ color: 'var(--status-error)' }}>{error}</div>
  if (!data) return <div className="panel" style={{ color: 'var(--text-tertiary)' }}>{t('no_data')}</div>

  const isArchived = data.local_thread?.archived

  return (
    <>
      <div className="panel">
        <div className="row-actions">
          <Link to={`/forum?forum_id=${data.forum_id || 30}&page=1`} className="btn-subtle">← {t('remoteForum')}</Link>
          {isArchived && (
            <Link to={`/threads/${tid}`} className="btn-subtle">{lang === 'en' ? 'Local Detail' : '本地详情'}</Link>
          )}
          {isArchived ? (
            <button className="btn-subtle" disabled={actionLoading === 'resync'} onClick={handleResync}>{t('resync')}</button>
          ) : (
            <button className="btn-subtle" disabled={actionLoading === 'archive'} onClick={handleArchive}>{t('archive')}</button>
          )}
        </div>
      </div>

      <h2>{t('thread_detail')}</h2>
      <div className="table-wrap"><table>
        <tbody>
          {[
            ['TID', <span className="mono">{data.tid}</span>],
            [t('raw_title'), data.raw_title],
            [t('title'), data.display_title],
            [t('publisher'), data.publisher || '-'],
            [t('forum'), forumName(data.forum_id)],
            [t('pub_time'), data.pub_time || '-'],
            [t('content_kind'), data.content_kind || '-'],
            [t('original_url'), data.url ? <a href={data.url} target="_blank" rel="noreferrer">{data.url}</a> : '-'],
            [t('archive_status'), isArchived ? (
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <Badge status={data.archive_status || 'complete'} />
                <span style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>{lang === 'en' ? 'Archived locally' : '已归档'}</span>
              </div>
            ) : <Badge status="none" />],
          ].map(([k, v], i) => <tr key={i}><th style={{ width: 120 }}>{k}</th><td style={{ textAlign: 'left' }}>{v}</td></tr>)}
        </tbody>
      </table></div>

      {data.floors.length > 0 && (
        <ThreadReader
          tid={tid}
          source="remote"
          contentKind={data.content_kind}
          floors={data.floors}
          page={data.page}
          totalPages={data.total_pages}
          onPageChange={setPage}
        />
      )}
    </>
  )
}
