import { useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api, type RemoteForum as RemoteForumType, type RemoteForumListResponse } from '../api/client'
import { Badge } from '../components/Badge'
import { PaginationControls } from '../components/PaginationControls'
import { useI18n } from '../context/I18nContext'
import { formatThreadListTitle } from '../utils/threadTitle'

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
      if (refreshing) setRefreshing(false)
      return
    }
    if (data) {
      setRefreshing(true)
    } else {
      setLoading(true)
    }
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

  const handleArchive = async (tid: number) => {
    setActionLoading(`archive-${tid}`)
    try { await api.createThreadArchiveBatch({ tids: [tid], forum_id: forumId }) } catch { /* ignore */ }
    setActionLoading(null)
  }

  const handleResync = async (tid: number) => {
    setActionLoading(`resync-${tid}`)
    try { await api.resyncThread(tid, forumId) } catch { /* ignore */ }
    setActionLoading(null)
  }

  const forumName = (fid: number) => {
    const f = forums.find(x => x.forum_id === fid)
    return f ? (lang === 'en' ? (f.name_en || f.name) : f.name) : String(fid)
  }

  return (
    <>
      <div className="panel">
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 8 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
            {forums.filter(f => f.enabled).map(f => (
              <button
                key={f.forum_id}
                className={`forum-tag${f.forum_id === forumId ? ' active' : ''}`}
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
                onClick={() => setParam('order', o)}
              >
                {o === 'default' ? (lang === 'en' ? 'Default' : '默认') : (lang === 'en' ? 'Dateline' : '按发布时间')}
              </button>
            ))}
          </div>
        </div>
      </div>

      <div id="threads-pagination-top" />

      {error && <div className="panel" style={{ color: 'var(--status-error)' }}>{error}</div>}
      {loading && <div className="panel" style={{ color: 'var(--text-tertiary)' }}>{t('loading')}</div>}
      {refreshing && <div className="loading-bar" />}

      {data && !loading && (
        <>
          <PaginationControls page={data.page} totalPages={data.total_pages} onPageChange={p => setParam('page', p > 1 ? String(p) : '')} scrollTargetId="threads-pagination-top" />

          <div className="table-wrap"><table className="remote-forum-table">
            <thead><tr>
              <th className="col-tid">{t('tid')}</th>
              <th className="col-title">{t('title')}</th>
              <th className="col-forum">{t('forum')}</th>
              <th className="col-category hide-mobile">{t('category')}</th>
              <th className="col-publisher">{t('publisher')}</th>
              <th className="col-time">{t('pub_time')}</th>
              <th className="col-replies">{t('reply_count')}</th>
              <th className="col-status">{t('archive_status')}</th>
              <th className="col-action">{t('action')}</th>
            </tr></thead>
            <tbody>
              {data.items.length === 0 ? (
                <tr><td colSpan={9} style={{ textAlign: 'center', color: 'var(--text-tertiary)' }}>{t('no_data')}</td></tr>
              ) : data.items.map(item => (
                (() => {
                  const titleText = formatThreadListTitle(item)
                  return (
                    <tr key={item.tid}>
                      <td className="mono col-tid">
                        <Link to={`/forum/${item.tid}?forum_id=${forumId}&page=1`}>{item.tid}</Link>
                      </td>
                      <td className="truncate col-title" title={titleText}>
                        <Link to={`/forum/${item.tid}?forum_id=${forumId}&page=1`}>{titleText}</Link>
                      </td>
                      <td className="nowrap col-forum">{forumName(forumId)}</td>
                      <td className="nowrap hide-mobile col-category">{item.category || '-'}</td>
                      <td className="nowrap col-publisher">{item.publisher || '-'}</td>
                      <td className="col-time">{formatDateTimeStacked(item.posted_at || null)}</td>
                      <td className="col-replies" style={{ textAlign: 'center' }}>{item.reply_count ?? '-'}</td>
                      <td className="col-status">
                        {item.local_thread?.archived ? (
                          <div style={{ display: 'flex', flexDirection: 'column', gap: 2, alignItems: 'flex-start' }}>
                            <Badge status={item.archive_status || 'complete'} />
                            <Link to={`/threads/${item.tid}`} className="btn-subtle" style={{ fontSize: 11, padding: '2px 6px' }}>
                              {lang === 'en' ? 'Local' : '本地'}
                            </Link>
                          </div>
                        ) : (
                          <Badge status="none" />
                        )}
                      </td>
                      <td className="col-action" style={{ textAlign: 'center' }}>
                        <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', justifyContent: 'center' }}>
                          {!item.local_thread?.archived ? (
                            <button className="btn-subtle" style={{ fontSize: 11 }} disabled={actionLoading === `archive-${item.tid}`} onClick={() => handleArchive(item.tid)}>
                              {t('archive')}
                            </button>
                          ) : (
                            <button className="btn-subtle" style={{ fontSize: 11 }} disabled={actionLoading === `resync-${item.tid}`} onClick={() => handleResync(item.tid)}>
                              {t('resync')}
                            </button>
                          )}
                        </div>
                      </td>
                    </tr>
                  )
                })()
              ))}
            </tbody>
          </table></div>

          <PaginationControls page={data.page} totalPages={data.total_pages} onPageChange={p => setParam('page', p > 1 ? String(p) : '')} scrollTargetId="threads-pagination-top" />
        </>
      )}
    </>
  )
}
