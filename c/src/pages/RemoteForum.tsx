import { useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api, type RemoteForum as RemoteForumType, type RemoteForumListResponse } from '../api/client'
import { Badge } from '../components/Badge'
import { PaginationControls } from '../components/PaginationControls'
import { useI18n } from '../context/I18nContext'
import { formatThreadListTitle } from '../utils/threadTitle'
import '../styles/catalog-lists.css'

const DEFAULT_FORUM_ID = 30
const LIST_CACHE_KEY = 'yamibo.remote-forum-cache.v1'
const CACHE_MAX_AGE_MS = 120_000  // 2 minutes

function _cacheKey(forumId: number, page: number, order: string): string {
  return `${LIST_CACHE_KEY}:${forumId}:${page}:${order}`
}

function _readCache(key: string): RemoteForumListResponse | null {
  try {
    const raw = sessionStorage.getItem(key)
    if (!raw) return null
    const entry = JSON.parse(raw)
    // Support both old format (plain response) and new format ({ ts, data }).
    if (entry._ts != null) return entry._data as RemoteForumListResponse
    return entry as RemoteForumListResponse
  } catch { return null }
}

function _isCacheFresh(key: string): boolean {
  try {
    const raw = sessionStorage.getItem(key)
    if (!raw) return false
    const entry = JSON.parse(raw)
    return typeof entry._ts === 'number' && Date.now() - entry._ts < CACHE_MAX_AGE_MS
  } catch { return false }
}

function _writeCache(key: string, data: RemoteForumListResponse): void {
  try { sessionStorage.setItem(key, JSON.stringify({ _ts: Date.now(), _data: data })) } catch { /* ignore */ }
}

function formatDateTimeStacked(value: string | null) {
  if (!value) return '-'
  const [datePart, timePart, ...rest] = value.split(' ')
  if (!datePart || !timePart || rest.length > 0) return value
  return (
    <span className="rag-date-time">
      <span>{datePart}</span>
      <span>{timePart}</span>
    </span>
  )
}

export function RemoteForum() {
  const { t, lang } = useI18n()
  const [searchParams, setSearchParams] = useSearchParams()
  const forumId = parseInt(searchParams.get('forum_id') || String(DEFAULT_FORUM_ID), 10)
  const page = Math.max(1, parseInt(searchParams.get('page') || '1', 10))
  const order = searchParams.get('order') || 'default'

  const cacheKey = _cacheKey(forumId, page, order)
  const [forums, setForums] = useState<RemoteForumType[]>([])
  const [data, setData] = useState<RemoteForumListResponse | null>(() => _readCache(cacheKey))
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(!data)
  const [refreshing, setRefreshing] = useState(false)
  const [actionLoading, setActionLoading] = useState<string | null>(null)
  const [actionMessage, setActionMessage] = useState<string | null>(null)
  const [actionJobId, setActionJobId] = useState<string | null>(null)

  useEffect(() => {
    api.remoteForums().then(fs => {
      setForums(fs)
      try { sessionStorage.setItem('yamibo.remote-forums.v1', JSON.stringify(fs)) } catch { /* ignore */ }
    }).catch(() => {
      const cached = sessionStorage.getItem('yamibo.remote-forums.v1')
      if (cached) { try { setForums(JSON.parse(cached)) } catch { /* ignore */ } }
    })
  }, [])

  useEffect(() => {
    let active = true
    setError(null)
    if (_isCacheFresh(cacheKey)) {
      // Back navigation — cache is fresh, skip fetch.
      setData(_readCache(cacheKey))
      setLoading(false)
      setRefreshing(false)
      return
    }
    const cached = _readCache(cacheKey)
    setData(cached)
    setRefreshing(!!cached)
    setLoading(!cached)
    api.remoteForum({ forum_id: forumId, page, order })
      .then(d => {
        if (!active) return
        setData(d)
        _writeCache(cacheKey, d)
      })
      .catch(e => { if (active) setError(e.message) })
      .finally(() => { if (active) { setLoading(false); setRefreshing(false) } })
    return () => { active = false }
  }, [forumId, page, order])

  const setParam = (key: string, value: string) => {
    const next = new URLSearchParams(searchParams)
    if (value) next.set(key, value)
    else next.delete(key)
    if (key !== 'page') next.delete('page')
    setSearchParams(next, { replace: true })
  }

  const handleAction = async (tid: number, archived: boolean) => {
    setActionLoading(`${archived ? 'resync' : 'archive'}-${tid}`)
    setActionMessage(null)
    setActionJobId(null)
    try {
      if (archived) {
        const result = await api.resyncThread(tid, forumId)
        setActionJobId(result.job_id)
      } else {
        const result = await api.createThreadArchiveBatch({ tids: [tid], forum_id: forumId })
        setActionJobId(result.created_job_ids[0] || result.reused_job_ids[0] || null)
      }
      setActionMessage(lang === 'en' ? `Task queued for #${tid}.` : `已为 #${tid} 提交任务，等待处理。`)
    } catch (e) {
      setActionMessage(e instanceof Error ? e.message : String(e))
    } finally { setActionLoading(null) }
  }

  const forumName = (fid: number) => {
    const f = forums.find(x => x.forum_id === fid)
    return f ? (lang === 'en' ? (f.name_en || f.name) : f.name) : String(fid)
  }

  return (
    <div className="catalog-page">
      <header className="catalog-heading"><h1>{lang === 'en' ? 'Forum browser' : '论坛漫游'}</h1><p>{lang === 'en' ? 'Browse remote threads and queue an archive task.' : '浏览远端帖子，按需创建归档任务。'}</p><Link to="/forums">{lang === 'en' ? 'Forum status' : '版块与连接状态'}</Link></header>
      <div className="panel">
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 8 }}>
          <div className="catalog-filter-tabs" aria-label={t('forum')}>
            {forums.filter(f => f.enabled).map(f => (
              <button
                key={f.forum_id}
                className={`forum-tag${f.forum_id === forumId ? ' active' : ''}`}
                aria-pressed={f.forum_id === forumId}
                onClick={() => setParam('forum_id', String(f.forum_id))}
              >
                {lang === 'en' ? (f.name_en || f.name) : f.name}
              </button>
            ))}
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
            <span style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>{t('order')}:</span>
            {(['default', 'dateline'] as const).map(o => (
              <button
                key={o}
                className={`forum-tag${order === o ? ' active' : ''}`}
                aria-pressed={order === o}
                onClick={() => setParam('order', o)}
              >
                {o === 'default' ? (lang === 'en' ? 'Default' : '默认') : (lang === 'en' ? 'Dateline' : '按发布时间')}
              </button>
            ))}
          </div>
        </div>
      </div>

      <p className="catalog-result-count">{lang === 'en' ? 'Current forum' : '当前版块'}：{forumName(forumId)}</p>
      <div id="threads-pagination-top" />

      {actionMessage && <div className="panel catalog-feedback" role="status">{actionMessage} {actionJobId && <Link to={`/jobs/${actionJobId}`}>{lang === 'en' ? 'View task' : '查看任务'}</Link>}</div>}
      {error && <div className="panel" style={{ color: 'var(--status-error)' }}>{error}</div>}
      {loading && <div className="panel" style={{ color: 'var(--text-tertiary)' }}>{t('loading')}</div>}
      {refreshing && <div className="loading-bar" />}

      {data && !loading && (
        <>
          <PaginationControls page={data.page} totalPages={data.total_pages} onPageChange={p => setParam('page', p > 1 ? String(p) : '')} scrollTargetId="threads-pagination-top" />

          <div className="catalog-list" aria-label={lang === 'en' ? 'Remote threads' : '远端帖子'}>
            {data.items.length === 0 ? <div className="panel">{t('no_data')}</div> : data.items.map(item => {
              const titleText = formatThreadListTitle(item)
              const archived = !!item.local_thread?.archived
              const pending = actionLoading === `${archived ? 'resync' : 'archive'}-${item.tid}`
              return <article className="catalog-row" key={item.tid}>
                <div className="catalog-main">
                  <Link className="catalog-title" to={`/forum/${item.tid}?forum_id=${forumId}&page=1`}>{titleText}</Link>
                  {titleText.length > 42 && <details className="catalog-title-details"><summary>{lang === 'en' ? 'Full title' : '完整标题'}</summary><p>{titleText}</p></details>}
                  <div className="catalog-meta">
                    <span className="mono">#{item.tid}</span><span>{forumName(forumId)}</span>
                    {item.category && <span>{item.category}</span>}
                    <span>{t('publisher')}: {item.publisher || '—'}</span>
                    <span>{t('pub_time')}: {formatDateTimeStacked(item.posted_at || null)}</span>
                    <span>{t('reply_count')}: {item.reply_count ?? '—'}</span>
                  </div>
                </div>
                <div className="catalog-row-actions">
                  {archived ? <Badge status={item.archive_status || 'complete'} /> : <Badge status="none">{lang === 'en' ? 'Not archived' : '未归档'}</Badge>}
                  {archived && <Link className="btn-subtle" to={`/threads/${item.tid}`}>{lang === 'en' ? 'Read local' : '本地阅读'}</Link>}
                  <button className="btn-subtle" disabled={actionLoading !== null} onClick={() => void handleAction(item.tid, archived)}>{pending ? t('running') : archived ? t('resync') : t('archive')}</button>
                </div>
              </article>
            })}
          </div>

          <PaginationControls page={data.page} totalPages={data.total_pages} onPageChange={p => setParam('page', p > 1 ? String(p) : '')} scrollTargetId="threads-pagination-top" />
        </>
      )}
    </div>
  )
}
