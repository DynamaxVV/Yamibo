import '../styles/tools.css'
import { useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { PaginationControls } from '../components/PaginationControls'
import { api, type Forum, type RagOverview, type RagSearchItem, type RagThreadListResponse, type RagThreadRow } from '../api/client'
import { Badge, ContentBadge } from '../components/Badge'
import { useI18n } from '../context/I18nContext'
import { formatDateTime } from '../utils/time'
import { formatThreadListTitle } from '../utils/threadTitle'

const SEARCH_MODES = ['hybrid', 'keyword', 'vector'] as const
const THREAD_PAGE_SIZES = [10, 25, 50] as const
const DEFAULT_THREAD_PAGE_SIZE = 25
const THREAD_DETAIL_PREVIEW_PAGE_SIZE = 10

type PendingIndexAction = {
  mode: 'single' | 'selected' | 'filtered'
  tids: number[]
  rows: RagThreadRow[]
}

function formatDateTimeStacked(value: string | null) {
  const formatted = formatDateTime(value)
  if (formatted === '-') return formatted
  const [datePart, timePart, ...rest] = formatted.split(' ')
  if (!datePart || !timePart || rest.length > 0) return formatted
  return (
    <span className="rag-date-time">
      <span>{datePart}</span>
      <span>{timePart}</span>
    </span>
  )
}

export function Rag() {
  const { t, lang, tx } = useI18n()
  const [failedOnly, setFailedOnly] = useState(false)
  const [view, setView] = useState<'search' | 'index'>('search')
  const [searching, setSearching] = useState(false)
  const [overviewRetry, setOverviewRetry] = useState(0)
  const [overview, setOverview] = useState<RagOverview | null>(null)
  const [forums, setForums] = useState<Forum[]>([])
  const [unindexedList, setUnindexedList] = useState<RagThreadListResponse | null>(null)
  const [indexedList, setIndexedList] = useState<RagThreadListResponse | null>(null)
  const [selectedTids, setSelectedTids] = useState<Set<number>>(new Set())
  const [threadQuery, setThreadQuery] = useState('')
  const [forumId, setForumId] = useState<string>('all')
  const [threadPageSize, setThreadPageSize] = useState(DEFAULT_THREAD_PAGE_SIZE)
  const [unindexedPage, setUnindexedPage] = useState(1)
  const [indexedPage, setIndexedPage] = useState(1)
  const [indexTid, setIndexTid] = useState('')
  const [indexForce, setIndexForce] = useState(false)
  const [indexSubmitting, setIndexSubmitting] = useState(false)
  const [indexMessage, setIndexMessage] = useState<string | null>(null)
  const [pendingIndexAction, setPendingIndexAction] = useState<PendingIndexAction | null>(null)
  const [searchQuery, setSearchQuery] = useState('')
  const [searchMode, setSearchMode] = useState<string>('hybrid')
  const [searchTopK, setSearchTopK] = useState(8)
  const [searchForumId, setSearchForumId] = useState<string>('all')
  const [searchTid, setSearchTid] = useState('')
  const [searchResults, setSearchResults] = useState<RagSearchItem[]>([])
  const [searchMeta, setSearchMeta] = useState<{ mode: string; count: number } | null>(null)
  const [searchError, setSearchError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [reloadToken, setReloadToken] = useState(0)
  const filterKey = `${threadQuery}::${forumId}::${threadPageSize}::${failedOnly}`
  const lastFilterKeyRef = useRef(filterKey)

  useEffect(() => {
    let active = true
    setLoading(true)
    Promise.all([api.ragOverview(), api.forums()]).then(([nextOverview, nextForums]) => {
      if (!active) return
      setOverview(nextOverview)
      setForums(nextForums)
      setLoading(false)
    }).catch((e: Error) => {
      if (!active) return
      setIndexMessage(e.message)
      setLoading(false)
    })
    return () => { active = false }
  }, [overviewRetry])

  useEffect(() => {
    let active = true
    if (lastFilterKeyRef.current !== filterKey) {
      lastFilterKeyRef.current = filterKey
      setSelectedTids(new Set())
      setUnindexedPage(1)
      setIndexedPage(1)
      setReloadToken(token => token + 1)
      return () => { active = false }
    }
    Promise.all([
      api.ragThreads({
        q: threadQuery || undefined,
        forum_id: forumId === 'all' ? 'all' : Number(forumId),
        index_state: 'unindexed',
        rag_status: failedOnly ? 'failed' : 'all',
        page: unindexedPage,
        page_size: threadPageSize,
      }),
      api.ragThreads({
        q: threadQuery || undefined,
        forum_id: forumId === 'all' ? 'all' : Number(forumId),
        index_state: 'indexed',
        rag_status: failedOnly ? 'failed' : 'all',
        page: indexedPage,
        page_size: threadPageSize,
      }),
    ]).then(([nextUnindexed, nextIndexed]) => {
      if (!active) return
      setUnindexedList(nextUnindexed)
      setIndexedList(nextIndexed)
    }).catch((e: Error) => {
      if (active) setIndexMessage(e.message)
    })
    return () => { active = false }
  }, [failedOnly, filterKey, threadQuery, forumId, threadPageSize, unindexedPage, indexedPage, reloadToken])

  const forumMap = useMemo(() => {
    const map: Record<number, string> = {}
    forums.forEach(forum => {
      map[forum.forum_id] = lang === 'en' ? (forum.name_en || forum.name) : forum.name
    })
    return map
  }, [forums, lang])

  const refreshOverview = async () => {
    const nextOverview = await api.ragOverview()
    setOverview(nextOverview)
  }

  const refreshThreads = async () => {
    const [nextUnindexed, nextIndexed] = await Promise.all([
      api.ragThreads({
        q: threadQuery || undefined,
        forum_id: forumId === 'all' ? 'all' : Number(forumId),
        index_state: 'unindexed',
        page: unindexedPage,
        page_size: threadPageSize,
      }),
      api.ragThreads({
        q: threadQuery || undefined,
        forum_id: forumId === 'all' ? 'all' : Number(forumId),
        index_state: 'indexed',
        page: indexedPage,
        page_size: threadPageSize,
      }),
    ])
    setUnindexedList(nextUnindexed)
    setIndexedList(nextIndexed)
    if (nextUnindexed.page !== unindexedPage) setUnindexedPage(nextUnindexed.page)
    if (nextIndexed.page !== indexedPage) setIndexedPage(nextIndexed.page)
  }

  const visibleRows = unindexedList?.items ?? []
  const visibleIds = visibleRows.map(row => row.tid)
  const allVisibleSelected = visibleIds.length > 0 && visibleIds.every(id => selectedTids.has(id))
  const rowMap = useMemo(() => {
    const map = new Map<number, RagThreadRow>()
    ;[...(unindexedList?.items ?? []), ...(indexedList?.items ?? [])].forEach(row => {
      map.set(row.tid, row)
    })
    return map
  }, [indexedList, unindexedList])

  const resolveRows = (tids: number[]) => tids.map(tid => rowMap.get(tid)).filter((row): row is RagThreadRow => Boolean(row))
  const needsConfirmation = (rows: RagThreadRow[]) => rows.some(row => row.rag_index_state !== 'unindexed')
  const showIndexConfirmation = (action: PendingIndexAction) => {
    setPendingIndexAction(action)
  }

  const buildThreadLink = (tid: number, floorNo?: number | null, pid?: number | null) => {
    if (floorNo == null) {
      return `/threads/${tid}`
    }
    const page = Math.max(1, Math.ceil(floorNo / THREAD_DETAIL_PREVIEW_PAGE_SIZE))
    const hash = pid != null ? `#pid-${pid}` : `#floor-${floorNo}`
    return `/threads/${tid}?preview_page=${page}${hash}`
  }

  const executeCreateIndex = async (tid?: number) => {
    const targetTid = tid ?? Number(indexTid)
    if (!targetTid || Number.isNaN(targetTid)) {
      setIndexMessage(t('rag_tid_required'))
      return
    }
    setIndexSubmitting(true)
    setIndexMessage(null)
    try {
      const result = await api.createRagIndex({ tid: targetTid, force: indexForce })
      if (!result.ok || !result.data) {
        setIndexMessage(result.error?.message || t('error'))
      } else {
        setIndexMessage(t('rag_index_job_created', { jobId: result.data.job_id }))
        setSelectedTids(prev => {
          const next = new Set(prev)
          next.delete(targetTid)
          return next
        })
        await Promise.all([refreshOverview(), refreshThreads()])
      }
    } catch (e: any) {
      setIndexMessage(e.message || String(e))
    } finally {
      setIndexSubmitting(false)
    }
  }

  const executeBatchIndex = async (mode: 'selected' | 'filtered', tidsOverride?: number[]) => {
    setIndexSubmitting(true)
    setIndexMessage(null)
    try {
      const explicitTids = tidsOverride && tidsOverride.length > 0 ? tidsOverride : null
      const result = await api.createRagIndexBatch(
        explicitTids
          ? { tids: explicitTids, force: indexForce }
          : mode === 'selected'
            ? { tids: Array.from(selectedTids), force: indexForce }
          : {
              q: threadQuery || undefined,
              forum_id: forumId === 'all' ? null : Number(forumId),
              index_state: 'unindexed',
              force: indexForce,
            },
      )
      setIndexMessage(
        t('rag_batch_result', {
          target: result.target_count,
          created: result.created_count,
          reused: result.reused_count,
        }),
      )
      await Promise.all([refreshOverview(), refreshThreads()])
      if (mode === 'selected') setSelectedTids(new Set())
    } catch (e: any) {
      setIndexMessage(e.message || String(e))
    } finally {
      setIndexSubmitting(false)
    }
  }

  const requestCreateIndex = async (row: RagThreadRow) => {
    const action = { mode: 'single' as const, tids: [row.tid], rows: [row] }
    if (needsConfirmation(action.rows)) {
      showIndexConfirmation(action)
      return
    }
    await executeCreateIndex(row.tid)
  }

  const requestBatchIndex = async (mode: 'selected' | 'filtered') => {
    const tids = mode === 'selected' ? Array.from(selectedTids) : visibleIds
    const rows = resolveRows(tids)
    const action = { mode, tids, rows }
    if (needsConfirmation(rows)) {
      showIndexConfirmation(action)
      return
    }
    await executeBatchIndex(mode, tids)
  }

  const handleCreateIndex = async () => {
    await executeCreateIndex()
  }

  const confirmPendingIndexAction = async () => {
    if (!pendingIndexAction) return
    const action = pendingIndexAction
    setPendingIndexAction(null)
    if (action.mode === 'single') {
      await executeCreateIndex(action.tids[0])
      return
    }
    if (action.mode === 'selected') {
      await executeBatchIndex('selected', action.tids)
      return
    }
    await executeBatchIndex('filtered', action.tids)
  }

  const handleSearch = async () => {
    if (!searchQuery.trim()) {
      setSearchError(t('rag_query_required'))
      return
    }
    setSearchError(null)
    setSearching(true)
    try {
      const result = await api.ragSearch({
        query: searchQuery.trim(),
        mode: searchMode,
        top_k: searchTopK,
        tid: searchTid ? Number(searchTid) : null,
        forum_id: searchForumId === 'all' ? null : Number(searchForumId),
      })
      if (!result.ok || !result.data) {
        setSearchError(result.error?.message || t('error'))
        setSearchResults([])
        setSearchMeta(null)
        return
      }
      setSearchResults(result.data.items)
      setSearchMeta({ mode: result.data.mode, count: result.data.count })
    } catch (e: any) {
      setSearchError(e.message || String(e))
    } finally { setSearching(false) }
  }

  if (loading) return <div className="panel" role="status">{t('loading')}</div>
  if (!overview) return <div className="panel tool-error" role="alert"><h1>{tx('知识检索', 'Knowledge search')}</h1><p>{indexMessage || t('error')}</p><button className="btn-subtle" onClick={() => setOverviewRetry(value => value + 1)}>{tx('重新加载', 'Reload')}</button></div>

  return (
    <div className={`tool-page rag-page rag-view-${view}`}>
      <header className="tool-page-header"><div><h1>{tx('知识检索', 'Knowledge search')}</h1><p>{tx('搜索归档内容并查看原文引用，或管理检索索引。', 'Search archived content and inspect source excerpts, or manage the search index.')}</p></div></header>
      <nav className="tool-tabs" aria-label={tx('知识检索功能', 'Knowledge search sections')}><button aria-pressed={view === "search"} onClick={() => setView("search")}>{tx('搜索归档', 'Search archive')}</button><button aria-pressed={view === "index"} onClick={() => setView("index")}>{tx('索引管理', 'Index management')}</button></nav>
      {!overview.enabled && <div className="panel tool-state" role="status">{tx('RAG 尚未启用，请先在', 'RAG is not enabled. Check the search service settings in')} <Link to="/settings">{t('settings')}</Link> {tx('检查检索服务配置。', 'before continuing.')}</div>}
      <div className="stat-row rag-index-only">
        <div className="stat-cell"><div className="label">{t('rag_unindexed_threads')}</div><div className="value">{overview.counts.unindexed_threads}</div></div>
        <div className="stat-cell"><div className="label">{t('rag_indexed_threads')}</div><div className="value">{overview.counts.indexed_threads}</div></div>
        <div className="stat-cell"><div className="label">{t('rag_total_chunks')}</div><div className="value">{overview.counts.total_chunks}</div></div>
        <div className="stat-cell"><div className="label">{t('rag_indexed_chunks')}</div><div className="value">{overview.counts.indexed_chunks}</div></div>
        <div className="stat-cell"><div className="label">{t('rag_failed_chunks')}</div><button className="value rag-failed-filter" aria-pressed={failedOnly} onClick={() => setFailedOnly(value => !value)}>{overview.counts.failed_chunks}<span>{failedOnly ? tx('显示全部', 'Show all') : tx('查看失败项', 'View failed')}</span></button></div>
      </div>

      <div className="rag-layout">
        <section className="panel rag-search-only" style={{ gridColumn: '1 / -1' }}>
          <div className="rag-panel-head">
            <div>
              <h2 className="rag-section-title">{tx('搜索归档内容', 'Search archived content')}</h2>
              <p className="rag-panel-copy">{t('rag_search_debug_desc')}</p>
            </div>
          </div>
          <div className="rag-search-form">
            <input className="filter-search" value={searchQuery} onChange={e => setSearchQuery(e.target.value)} placeholder={t('rag_query_placeholder')} />
            <label className="rag-field">
              <span>{tx('结果数量（Top K）', 'Result count (Top K)')}</span>
              <input type="number" value={searchTopK} onChange={e => setSearchTopK(Math.min(50, Math.max(1, Number(e.target.value) || 8)))} min={1} max={50} style={{ width: 96 }} />
            </label>
            <label className="rag-field rag-field-wide">
              <span>{t('rag_search_tid_label')}</span>
              <input type="number" value={searchTid} onChange={e => setSearchTid(e.target.value)} placeholder={t('rag_search_tid_placeholder')} style={{ width: 180 }} />
            </label>
            <select value={searchForumId} onChange={e => setSearchForumId(e.target.value)}>
              <option value="all">{t('all_forums')}</option>
              {forums.map(forum => <option key={forum.forum_id} value={forum.forum_id}>{lang === 'en' ? (forum.name_en || forum.name) : forum.name}</option>)}
            </select>
            <select value={searchMode} onChange={e => setSearchMode(e.target.value)}>
              {SEARCH_MODES.map(mode => <option key={mode} value={mode}>{t(`rag_mode_${mode}`)}</option>)}
            </select>
            <button className="btn-primary" disabled={searching || !searchQuery.trim() || !overview.enabled} onClick={() => void handleSearch()}>{searching ? tx('正在检索…', 'Searching…') : t('search')}</button>
          </div>
          <div className="rag-helper-text">{t('rag_search_scope_hint')}</div>
          {searchError && <div className="rag-helper-text rag-helper-error" role="alert">{searchError}</div>}
          {searchMeta && (
            <div className="rag-result-meta">
              <span>{t('rag_mode_label')}: <strong>{t(`rag_mode_${searchMeta.mode}`)}</strong></span>
              <span>{t('result')}: <strong>{searchMeta.count}</strong></span>
            </div>
          )}
          <div className="rag-results">
            {searchResults.map(item => (
              <article key={item.chunk_id} className="rag-result-card">
                <div className="rag-result-head">
                  <div>
                    <div className="rag-result-title">
                      <Link to={buildThreadLink(item.tid, item.floor_no, item.pid)}>
                        {item.display_title || `${t('thread_link')} ${item.tid}`}
                      </Link>
                    </div>
                    <div className="rag-result-meta-row">
                      <span className="mono">{item.chunk_id}</span>
                      <span>{t('tid')}: {item.tid}</span>
                      <span>{t('floor_jump')}: {item.floor_no ?? '-'}</span>
                      <span>{t('publisher')}: {item.publisher || '-'}</span>
                    </div>
                  </div>
                  <div className="rag-score-pill">{item.score.toFixed(3)}</div>
                </div>
                <div className="rag-snippet-box">
                  <div className="rag-snippet-label">{t('read_preview')}</div>
                  <p className="rag-snippet">{item.snippet}</p>
                  <div className="rag-preview-links">
                    <Link to={buildThreadLink(item.tid, item.floor_no)}>
                      {item.floor_no != null ? t('jump_to_floor') : t('thread_detail')}
                    </Link>
                    {item.pid != null && (
                      <Link to={buildThreadLink(item.tid, item.floor_no, item.pid)}>
                        {t('jump_to_post')} #{item.pid}
                      </Link>
                    )}
                  </div>
                </div>
                <div className="rag-score-breakdown">
                  <span>K {item.score_parts.keyword.toFixed(3)}</span>
                  <span>V {item.score_parts.vector.toFixed(3)}</span>
                  <span>M {item.score_parts.metadata.toFixed(3)}</span>
                  <span className="rag-source-uri" title={item.source_uri}>{item.source_uri}</span>
                </div>
              </article>
            ))}
            {searchMeta && searchResults.length === 0 && <div className="panel" role="status" style={{ marginTop: 12 }}>{tx('没有找到匹配内容。请缩短关键词、扩大版块范围，或检查索引覆盖情况。', 'No matching content was found. Try shorter keywords, a wider forum scope, or check index coverage.')}</div>}
          </div>
        </section>

        <section className="panel rag-panel rag-index-only">
          <div className="rag-panel-head">
            <div>
              <h2 className="rag-section-title">{t('rag_config_title')}</h2>
              <p className="rag-panel-copy">{t('rag_config_desc')}</p>
            </div>
            <Badge status={overview.enabled ? 'ok' : 'failed'}>{overview.enabled ? t('enabled') : t('rag_disabled')}</Badge>
          </div>
          <div className="rag-kv-grid">
            <div><span>{t('rag_embedding_model')}</span><strong>{overview.config.embedding_model}</strong></div>
            <div><span>{t('rag_embedding_dimensions')}</span><strong>{overview.config.embedding_dimensions}</strong></div>
            <div><span>{t('rag_chunker_version')}</span><strong>{overview.config.chunker_version}</strong></div>
            <div><span>{t('rag_chunk_size')}</span><strong>{overview.config.min_chunk_chars} - {overview.config.max_chunk_chars}</strong></div>
          </div>
          <div className="rag-panel-head" style={{ marginTop: 16, marginBottom: 8 }}>
            <div>
              <h3 className="rag-subsection-title">{t('rag_index_meta')}</h3>
              <p className="rag-panel-copy">{t('rag_index_meta_desc')}</p>
            </div>
          </div>
          <div className="table-wrap">
            <table>
              <thead><tr><th>{t('key')}</th><th>{t('value')}</th></tr></thead>
              <tbody>
                {Object.entries(overview.index_meta).length === 0 ? (
                  <tr><td colSpan={4}>{t('no_data')}</td></tr>
                ) : Object.entries(overview.index_meta).map(([key, value]) => (
                  <tr key={key}><td className="mono">{key}</td><td className="table-cell-long">{value}</td></tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        <section className="panel rag-panel rag-index-only">
          <div className="rag-panel-head">
            <div>
              <h2 className="rag-section-title">{t('rag_create_index')}</h2>
              <p className="rag-panel-copy">{t('rag_create_index_desc')}</p>
            </div>
          </div>
          <div className="toolbar">
            <input
              type="number"
              value={indexTid}
              onChange={e => setIndexTid(e.target.value)}
              placeholder="572313"
              style={{ width: 140 }}
            />
            <label className="rag-inline-check">
              <input type="checkbox" checked={indexForce} onChange={e => setIndexForce(e.target.checked)} />
              <span>{t('rag_force_reindex')}</span>
            </label>
            <button className="btn-primary" onClick={() => void handleCreateIndex()} disabled={indexSubmitting || !overview.enabled}>
              {indexSubmitting ? t('running') : t('rag_create_index')}
            </button>
          </div>
          {indexMessage && <div className="rag-helper-text">{indexMessage}</div>}
          <div className="table-wrap" style={{ marginTop: 12 }}>
            <table>
              <thead><tr><th>{t('forum')}</th><th>{t('thread_count')}</th><th>{t('rag_indexed_threads')}</th><th>{t('rag_total_chunks')}</th></tr></thead>
              <tbody>
                {overview.forum_breakdown.map(row => (
                  <tr key={row.forum_id}>
                    <td className="table-cell-long">{lang === 'en' ? (row.name_en || row.name) : row.name}</td>
                    <td>{row.thread_count}</td>
                    <td>{row.indexed_thread_count}</td>
                    <td>{row.chunk_count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      </div>

      <section className="panel rag-index-only">
        {failedOnly && <p className="tool-state">{tx('正在显示含失败向量的帖子。', 'Showing threads with failed vectors.')} <button className="btn-subtle" onClick={() => setFailedOnly(false)}>{tx('清除失败筛选', 'Clear failed filter')}</button></p>}
        <div className="rag-panel-head">
          <div>
            <h2 className="rag-section-title">{t('rag_thread_management')}</h2>
            <p className="rag-panel-copy">{t('rag_thread_management_desc')}</p>
          </div>
          <Badge status="accent">
            {t('rag_unindexed_threads')}: {overview.counts.unindexed_threads}
          </Badge>
        </div>
        <div className="filter-bar">
          <input
            className="filter-search"
            value={threadQuery}
            onChange={e => setThreadQuery(e.target.value)}
            placeholder={t('rag_thread_search_placeholder')}
          />
          <div className="filter-group">
            <select value={forumId} onChange={e => setForumId(e.target.value)}>
              <option value="all">{t('all_forums')}</option>
              {forums.map(forum => <option key={forum.forum_id} value={forum.forum_id}>{lang === 'en' ? (forum.name_en || forum.name) : forum.name}</option>)}
            </select>
          </div>
        </div>
        <div className="rag-thread-groups">
          <section className="rag-thread-group">
            <div className="rag-panel-head">
              <div>
                <h3 className="rag-subsection-title">{t('rag_unindexed_title')}</h3>
                <p className="rag-panel-copy">{t('rag_unindexed_desc')}</p>
              </div>
              <Badge status="warn">{unindexedList?.total_count ?? 0}</Badge>
            </div>
            <div className="toolbar rag-batch-toolbar" style={{ marginBottom: 12 }}>
              <div className="rag-batch-actions">
                <button className="btn-subtle" disabled={indexSubmitting || !overview.enabled || selectedTids.size === 0} onClick={() => void requestBatchIndex('selected')}>
                  {t('rag_index_selected')} ({selectedTids.size})
                </button>
              </div>
              <label className="rag-field rag-page-size-field">
                <span>{t('page_size')}</span>
                <select value={threadPageSize} onChange={e => setThreadPageSize(Number(e.target.value) || DEFAULT_THREAD_PAGE_SIZE)}>
                  {THREAD_PAGE_SIZES.map(size => <option key={size} value={size}>{size}</option>)}
                </select>
              </label>
            </div>
            <p className="rag-panel-copy">{tx('Chunk 指用于检索的文本片段；宽表可横向滚动查看全部字段。', 'A chunk is a text segment used for retrieval. Scroll the wide table horizontally to view all fields.')}</p>
            <div className="table-wrap" tabIndex={0} aria-label={tx('索引帖子列表，可横向滚动', 'Indexed threads table; scroll horizontally')}>
              <table style={{ tableLayout: 'fixed', width: '100%' }}>
                <thead><tr>
                  <th style={{ width: 34 }}>
                    <input
                      type="checkbox"
                      checked={allVisibleSelected}
                      onChange={() => {
                        setSelectedTids(prev => {
                          const next = new Set(prev)
                          if (allVisibleSelected) {
                            visibleIds.forEach(id => next.delete(id))
                          } else {
                            visibleIds.forEach(id => next.add(id))
                          }
                          return next
                        })
                      }}
                    />
                  </th>
                  <th style={{ width: 72 }}>{t('tid')}</th>
                  <th style={{ minWidth: 320, width: 360 }}>{t('title')}</th>
                  <th style={{ width: 92 }}>{t('forum')}</th>
                  <th style={{ width: 92 }}>{t('content_kind')}</th>
                  <th style={{ width: 84 }}>{t('rag_index_state')}</th>
                  <th style={{ width: 72 }}>{t('rag_total_chunks')}</th>
                  <th style={{ width: 72 }}>{t('rag_indexed')}</th>
                  <th style={{ width: 72 }}>{t('rag_pending')}</th>
                  <th style={{ width: 72 }}>{t('rag_failed')}</th>
                  <th style={{ width: 120 }}>{t('updated')}</th>
                  <th style={{ width: 92 }}>{t('action')}</th>
                </tr></thead>
                <tbody>
                  {unindexedList?.items.map(row => (
                    (() => {
                      const titleText = formatThreadListTitle(row)
                      return (
                        <tr key={row.tid}>
                          <td>
                            <input
                              type="checkbox"
                              checked={selectedTids.has(row.tid)}
                              onChange={() => {
                                setSelectedTids(prev => {
                                  const next = new Set(prev)
                                  next.has(row.tid) ? next.delete(row.tid) : next.add(row.tid)
                                  return next
                                })
                              }}
                            />
                          </td>
                          <td className="mono"><Link to={`/threads/${row.tid}`}>{row.tid}</Link></td>
                          <td className="table-cell-long truncate"><Link to={`/threads/${row.tid}`}>{titleText}</Link></td>
                          <td>{row.forum_id ? (forumMap[row.forum_id] || row.forum_id) : '-'}</td>
                          <td><ContentBadge kind={row.content_kind} /></td>
                          <td><Badge status={row.rag_index_state}>{t(`rag_index_state_${row.rag_index_state}`)}</Badge></td>
                          <td>{row.rag_chunk_count}</td>
                          <td><Badge status={row.rag_indexed_chunk_count > 0 ? 'ok' : 'muted'}>{row.rag_indexed_chunk_count}</Badge></td>
                          <td><Badge status={row.rag_pending_chunk_count > 0 ? 'pending' : 'muted'}>{row.rag_pending_chunk_count}</Badge></td>
                          <td><Badge status={row.rag_failed_chunk_count > 0 ? 'failed' : 'muted'}>{row.rag_failed_chunk_count}</Badge></td>
                          <td className="col-time">{formatDateTimeStacked(row.rag_last_indexed_at || row.sync_time)}</td>
                          <td>
                            <button className="btn-subtle" disabled={indexSubmitting || !overview.enabled} onClick={() => void requestCreateIndex(row)}>
                              {row.rag_index_state === 'unindexed' ? t('rag_index_now') : t('rag_reindex_now')}
                            </button>
                          </td>
                        </tr>
                      )
                    })()
                  ))}
                </tbody>
              </table>
            </div>
            <PaginationControls
              page={unindexedList?.page ?? 1}
              totalPages={unindexedList?.total_pages ?? 1}
              onPageChange={setUnindexedPage}
            />
          </section>

          <section className="rag-thread-group">
            <div className="rag-panel-head">
              <div>
                <h3 className="rag-subsection-title">{t('rag_indexed_title')}</h3>
                <p className="rag-panel-copy">{t('rag_indexed_desc')}</p>
              </div>
              <Badge status="ok">{indexedList?.total_count ?? 0}</Badge>
            </div>
            <p className="rag-panel-copy">{tx('Chunk 指用于检索的文本片段；宽表可横向滚动查看全部字段。', 'A chunk is a text segment used for retrieval. Scroll the wide table horizontally to view all fields.')}</p>
            <div className="table-wrap" tabIndex={0} aria-label={tx('索引帖子列表，可横向滚动', 'Indexed threads table; scroll horizontally')}>
              <table style={{ tableLayout: 'fixed', width: '100%' }}>
                <thead><tr>
                  <th style={{ width: 72 }}>{t('tid')}</th>
                  <th style={{ minWidth: 320, width: 360 }}>{t('title')}</th>
                  <th style={{ width: 92 }}>{t('forum')}</th>
                  <th style={{ width: 92 }}>{t('content_kind')}</th>
                  <th style={{ width: 84 }}>{t('rag_index_state')}</th>
                  <th style={{ width: 72 }}>{t('rag_total_chunks')}</th>
                  <th style={{ width: 72 }}>{t('rag_indexed')}</th>
                  <th style={{ width: 72 }}>{t('rag_pending')}</th>
                  <th style={{ width: 72 }}>{t('rag_failed')}</th>
                  <th style={{ width: 120 }}>{t('updated')}</th>
                  <th style={{ width: 92 }}>{t('action')}</th>
                </tr></thead>
                <tbody>
                  {indexedList?.items.map(row => (
                    (() => {
                      const titleText = formatThreadListTitle(row)
                      return (
                        <tr key={row.tid}>
                          <td className="mono"><Link to={`/threads/${row.tid}`}>{row.tid}</Link></td>
                          <td className="table-cell-long truncate"><Link to={`/threads/${row.tid}`}>{titleText}</Link></td>
                          <td>{row.forum_id ? (forumMap[row.forum_id] || row.forum_id) : '-'}</td>
                          <td><ContentBadge kind={row.content_kind} /></td>
                          <td><Badge status={row.rag_index_state}>{t(`rag_index_state_${row.rag_index_state}`)}</Badge></td>
                          <td>{row.rag_chunk_count}</td>
                          <td><Badge status={row.rag_indexed_chunk_count > 0 ? 'ok' : 'muted'}>{row.rag_indexed_chunk_count}</Badge></td>
                          <td><Badge status={row.rag_pending_chunk_count > 0 ? 'pending' : 'muted'}>{row.rag_pending_chunk_count}</Badge></td>
                          <td><Badge status={row.rag_failed_chunk_count > 0 ? 'failed' : 'muted'}>{row.rag_failed_chunk_count}</Badge></td>
                          <td className="col-time">{formatDateTimeStacked(row.rag_last_indexed_at || row.sync_time)}</td>
                          <td>
                            <button className="btn-subtle" disabled={indexSubmitting || !overview.enabled} onClick={() => void requestCreateIndex(row)}>
                              {row.rag_index_state === 'unindexed' ? t('rag_index_now') : t('rag_reindex_now')}
                            </button>
                          </td>
                        </tr>
                      )
                    })()
                  ))}
                </tbody>
              </table>
            </div>
            <PaginationControls
              page={indexedList?.page ?? 1}
              totalPages={indexedList?.total_pages ?? 1}
              onPageChange={setIndexedPage}
            />
          </section>
        </div>
      </section>

      <section className="panel rag-index-only">
        <div className="rag-panel-head">
          <div>
            <h2 className="rag-section-title">{t('recent_jobs')}</h2>
            <p className="rag-panel-copy">{t('rag_recent_jobs_desc')}</p>
          </div>
        </div>
        <div className="table-wrap">
          <table>
            <thead><tr><th>{t('tid')}</th><th>{t('description')}</th><th>{t('status')}</th><th>{t('stage')}</th><th>{t('updated')}</th></tr></thead>
            <tbody>
              {overview.recent_jobs.length === 0 && <tr><td colSpan={5}>{tx('暂无索引任务。可在上方选择帖子创建索引。', 'No index tasks yet. Select threads above to create one.')}</td></tr>}
              {overview.recent_jobs.map(job => (
                <tr key={job.job_id}>
                  <td>{job.tid ? <Link to={`/threads/${job.tid}`}>{job.tid}</Link> : '-'}</td>
                  <td className="table-cell-long truncate"><Link to={`/jobs/${job.job_id}`}>{lang === 'en' ? job.description_en : job.description}</Link></td>
                  <td><Badge status={job.status} /></td>
                  <td>{job.stage || '-'}</td>
                  <td className="nowrap col-time">{formatDateTime(job.updated_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {pendingIndexAction && (
        <div className="confirm-overlay" onClick={() => setPendingIndexAction(null)}>
          <div className="confirm-dialog" onClick={e => e.stopPropagation()}>
            <h2 style={{ margin: '0 0 8px', fontSize: 15 }}>{t('rag_index_confirm_title')}</h2>
            <p style={{ margin: '0 0 12px', fontSize: 12, color: 'var(--text-secondary)', lineHeight: 1.6 }}>
              {t('rag_index_confirm_desc')}
            </p>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8, maxHeight: 260, overflow: 'auto', marginBottom: 14 }}>
              {pendingIndexAction.rows.slice(0, 8).map(row => (
                (() => {
                  const titleText = formatThreadListTitle(row)
                  return (
                    <div key={row.tid} className="confirm-row">
                      <span className="confirm-key">TID {row.tid}</span>
                      <span className="confirm-val">
                        {titleText} · {t(`rag_index_state_${row.rag_index_state}`)}
                      </span>
                    </div>
                  )
                })()
              ))}
              {pendingIndexAction.rows.length > 8 && (
                <div style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>
                  +{pendingIndexAction.rows.length - 8}
                </div>
              )}
            </div>
            <div className="confirm-actions">
              <button className="btn-subtle" onClick={() => setPendingIndexAction(null)}>{t('cancel')}</button>
              <button className="btn-primary" onClick={() => void confirmPendingIndexAction()} disabled={indexSubmitting || !overview.enabled}>
                {t('rag_index_confirm_submit')}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
