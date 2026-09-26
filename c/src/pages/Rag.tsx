import { Markdown } from '../components/Markdown'
import '../styles/tools.css'
import '../styles/knowledge.css'
import { useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { PaginationControls } from '../components/PaginationControls'
import { LoadingIndicator } from '../components/LoadingIndicator'
import { api, type DiscussionSearchResponse, type Forum, type RagOverview, type RagSearchItem, type RagThreadListResponse, type RagThreadRow } from '../api/client'
import { Badge, ContentBadge } from '../components/Badge'
import { useI18n } from '../context/I18nContext'
import { formatDateTime } from '../utils/time'
import { formatThreadListTitle } from '../utils/threadTitle'
import { useChatSessions } from '../hooks/useChatSessions'

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

function nextIsoDate(value: string) {
  const [year, month, day] = value.split('-').map(Number)
  const next = new Date(Date.UTC(year, month - 1, day + 1))
  return `${next.getUTCFullYear()}-${String(next.getUTCMonth() + 1).padStart(2, '0')}-${String(next.getUTCDate()).padStart(2, '0')}`
}

function yesterdayInShanghai() {
  const parts = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(new Date())
  const year = Number(parts.find(part => part.type === 'year')?.value)
  const month = Number(parts.find(part => part.type === 'month')?.value)
  const day = Number(parts.find(part => part.type === 'day')?.value)
  const previous = new Date(Date.UTC(year, month - 1, day - 1))
  return `${previous.getUTCFullYear()}-${String(previous.getUTCMonth() + 1).padStart(2, '0')}-${String(previous.getUTCDate()).padStart(2, '0')}`
}

function dailyRevisionChatHref(
  issue: import('../api/client').DailyIssue,
  revision: import('../api/client').DailyReportRevision,
  sessionId: string,
) {
  const config = issue.config_snapshot_json || {}
  const facts = revision.stats_receipt_json || revision.report_json?.facts || {}
  const candidates = Array.isArray(facts.candidates) ? facts.candidates as Record<string, unknown>[] : []
  const forumIdsRaw = Array.isArray(config.forum_ids) ? config.forum_ids : config.forum_id ? [config.forum_id] : []
  const candidateForumIds = candidates.map(candidate => candidate.forum_id)
  const forumIds = [...new Set([...forumIdsRaw, ...candidateForumIds].map(Number).filter(id => Number.isSafeInteger(id) && id > 0))]
  const tids = [...new Set(candidates.map(candidate => Number(candidate.tid)).filter(tid => Number.isSafeInteger(tid) && tid > 0))].slice(0, 200)
  const params = new URLSearchParams({ mode: tids.length ? 'selected' : 'discovery', report_revision: revision.revision_id, session_id: sessionId })
  if (forumIds.length) params.set('forum_ids', forumIds.join(','))
  if (tids.length) params.set('tids', tids.join(','))
  params.set('start_at', `${issue.target_day}T00:00:00+08:00`)
  params.set('end_at', `${nextIsoDate(issue.target_day)}T00:00:00+08:00`)
  return `/chat?${params.toString()}`
}

function readableDailyBody(body: string): string {
  const sources = new Map<string, number>()
  return body.replace(/\[(?:TID\s+(\d+)\s*\/\s*PID\s+(\d+)|原文 PID\s+(\d+))\]/g, (_match, tid, pid, legacyPid) => {
    const key = `${tid || ''}:${pid || legacyPid}`
    if (!sources.has(key)) sources.set(key, sources.size + 1)
    return `[来源 ${sources.get(key)}]`
  })
}

export function Rag() {
  const { t, lang, tx } = useI18n()
  const chatSessions = useChatSessions()
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
  const [discussionQuery, setDiscussionQuery] = useState('')
  const [discussionTid, setDiscussionTid] = useState('')
  const [discussionForumId, setDiscussionForumId] = useState('all')
  const [discussionStartDate, setDiscussionStartDate] = useState('')
  const [discussionEndDate, setDiscussionEndDate] = useState('')
  const [discussionResult, setDiscussionResult] = useState<DiscussionSearchResponse | null>(null)
  const [discussionError, setDiscussionError] = useState<string | null>(null)
  const [forumsError, setForumsError] = useState<string | null>(null)
  const [discussionLoading, setDiscussionLoading] = useState(false)
  const [discussionSubmitted, setDiscussionSubmitted] = useState(false)
  const [searchMode, setSearchMode] = useState<string>('hybrid')
  const [searchTopK, setSearchTopK] = useState(8)
  const [searchForumId, setSearchForumId] = useState<string>('all')
  const [searchTid, setSearchTid] = useState('')
  const [searchResults, setSearchResults] = useState<RagSearchItem[]>([])
  const [searchMeta, setSearchMeta] = useState<{ mode: string; count: number } | null>(null)
  const [searchError, setSearchError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [reloadToken, setReloadToken] = useState(0)
  const [dailyIssues, setDailyIssues] = useState<import('../api/client').DailyIssueSummary[]>([])
  const [dailyIssueDetail, setDailyIssueDetail] = useState<import('../api/client').DailyIssueDetail | null>(null)
  const [dailySelectedIssue, setDailySelectedIssue] = useState('')
  const [dailyRules, setDailyRules] = useState<import('../api/client').DailyRule[]>([])
  const [dailySelectedRule, setDailySelectedRule] = useState('')
  const [dailyTargetDay, setDailyTargetDay] = useState(yesterdayInShanghai)
  const [dailyForumIds, setDailyForumIds] = useState<number[]>([])
  const [dailyExecutionTime, setDailyExecutionTime] = useState('08:30')
  const [dailyDeadline, setDailyDeadline] = useState('09:00')
  const [dailyMessage, setDailyMessage] = useState('')
  const [dailyMessageIsError, setDailyMessageIsError] = useState(false)
  const [dailyLoading, setDailyLoading] = useState(false)
  const [dailyRetryReason, setDailyRetryReason] = useState('')
  const filterKey = `${threadQuery}::${forumId}::${threadPageSize}::${failedOnly}`
  const lastFilterKeyRef = useRef(filterKey)

  useEffect(() => {
    let active = true
    setLoading(true)
    Promise.allSettled([api.ragOverview(), api.forums()]).then(([overviewResult, forumsResult]) => {
      if (!active) return
      if (overviewResult.status === 'fulfilled') setOverview(overviewResult.value)
      else setIndexMessage((overviewResult.reason as Error).message || t('error'))
      if (forumsResult.status === 'fulfilled') { setForums(forumsResult.value); setForumsError(null) }
      else { const message = (forumsResult.reason as Error).message || t('error'); setForumsError(message); setDiscussionError(message) }
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
  const discussionForums = useMemo(() => forums.filter(forum => forum.content_kind === 'discussion' && forum.enabled), [forums])

  const dailySessionRef = useRef(chatSessions.selected)
  dailySessionRef.current = chatSessions.selected
  const loadDailyData = async (offset = 0) => {
    if (!chatSessions.selected) return
    const sessionId = chatSessions.selected
    try {
      const [issues, rules] = await Promise.all([
        api.dailyIssues({ session_id: chatSessions.selected, limit: 50, offset }),
        api.dailyRules(chatSessions.selected),
      ])
      if (dailySessionRef.current !== sessionId) return
      setDailyIssues(current => offset ? [...current, ...issues.items] : issues.items)
      setDailyRules(rules.items)
      if ((!dailySelectedRule || !rules.items.some(rule => rule.rule_id === dailySelectedRule)) && rules.items.length) setDailySelectedRule(rules.items[0].rule_id)
    } catch (error) { if (dailySessionRef.current === sessionId) { setDailyMessage((error as Error).message || t('error')); setDailyMessageIsError(true) } }
  }

  useEffect(() => { setDailyIssues([]); setDailyRules([]); setDailySelectedRule(''); setDailySelectedIssue(''); setDailyIssueDetail(null); setDailyMessage(''); void loadDailyData() }, [chatSessions.selected])
  useEffect(() => {
    if (!chatSessions.selected || !dailySelectedIssue) { setDailyIssueDetail(null); return }
    let active = true
    api.dailyIssue(dailySelectedIssue, chatSessions.selected).then(detail => { if (active) setDailyIssueDetail(detail) }).catch(error => { if (active) setDailyMessage((error as Error).message || t('error')) })
    return () => { active = false }
  }, [chatSessions.selected, dailySelectedIssue])

  const toggleDailyForum = (id: number) => setDailyForumIds(current => current.includes(id) ? current.filter(value => value !== id) : [...current, id])
  const submitManualIssue = async () => {
    if (!chatSessions.selected || !dailyTargetDay || dailyForumIds.length === 0) return
    setDailyLoading(true); setDailyMessage(''); setDailyMessageIsError(false)
    try {
      const result = await api.createDailyIssue({ session_id: chatSessions.selected, target_day: dailyTargetDay, forum_ids: dailyForumIds, timezone: 'Asia/Shanghai' })
      setDailyMessage(result.queued ? tx(`期次已创建，后台任务 ${result.job_id} 正在排队；这不代表报告已完成。`, `Issue created; job ${result.job_id} is queued. The report is not complete yet.`) : tx('该目标日的期次已存在。', 'An issue for that day already exists.'))
      setDailySelectedIssue(result.issue.issue_id)
      await loadDailyData()
    } catch (error) { setDailyMessage((error as Error).message || t('error')); setDailyMessageIsError(true) }
    finally { setDailyLoading(false) }
  }
  const saveDailyRule = async (enabled = true, createAnother = false) => {
    if (!chatSessions.selected || dailyForumIds.length === 0) return
    setDailyLoading(true); setDailyMessage(''); setDailyMessageIsError(false)
    const body = { session_id: chatSessions.selected, forum_ids: dailyForumIds, timezone: 'Asia/Shanghai', execution_time: dailyExecutionTime, preparation_deadline: dailyDeadline, budget: { top_n: 10, source_pid_limit: 30, statement_timeout_ms: 3000 }, enabled }
    try {
      const rule = dailySelectedRule && !createAnother
        ? await api.updateDailyRule(dailySelectedRule, { ...body, expected_revision: dailyRules.find(item => item.rule_id === dailySelectedRule)?.revision ?? 0 })
        : await api.createDailyRule(body)
      setDailySelectedRule(rule.rule_id)
      setDailyMessage(tx(`规则已保存；下次执行时间：${formatDateTime(rule.next_run_at)}。`, `Rule saved; next run: ${formatDateTime(rule.next_run_at)}.`))
      await loadDailyData()
    } catch (error) { setDailyMessage((error as Error).message || t('error')); setDailyMessageIsError(true) }
    finally { setDailyLoading(false) }
  }
  const retryDailyIssue = async () => {
    if (!chatSessions.selected || !dailySelectedIssue || !dailyRetryReason.trim()) return
    setDailyLoading(true); setDailyMessage(''); setDailyMessageIsError(false)
    try {
      const result = await api.retryDailyIssue(dailySelectedIssue, chatSessions.selected, dailyRetryReason.trim())
      setDailyMessage(result.queued ? tx('补报任务已排队，报告将在任务结束后更新为新的不可变修订。', 'The regeneration job is queued; a new immutable revision will appear after it finishes.') : tx('任务未排队。', 'No job was queued.'))
      await loadDailyData()
      setDailyIssueDetail(await api.dailyIssue(dailySelectedIssue, chatSessions.selected))
    } catch (error) { setDailyMessage((error as Error).message || t('error')); setDailyMessageIsError(true) }
    finally { setDailyLoading(false) }
  }

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

  const handleDiscussionSearch = async () => {
    const query = discussionQuery.trim()
    const tid = discussionTid.trim()
    if (!query && !tid) {
      setDiscussionError(tx('请输入关键词或 TID。', 'Enter a keyword or TID.'))
      return
    }
    if (tid && (!/^\d+$/.test(tid) || !Number.isSafeInteger(Number(tid)) || Number(tid) <= 0)) {
      setDiscussionError(tx('TID 必须是正整数。', 'TID must be a positive integer.'))
      return
    }
    setDiscussionError(null)
    setDiscussionLoading(true)
    setDiscussionSubmitted(true)
    try {
      const result = await api.searchDiscussions({
        query,
        forum_ids: discussionForumId === 'all' ? undefined : [Number(discussionForumId)],
        tids: tid ? [Number(tid)] : undefined,
        start_date: discussionStartDate ? `${discussionStartDate}T00:00:00+08:00` : undefined,
        end_date: discussionEndDate ? `${nextIsoDate(discussionEndDate)}T00:00:00+08:00` : undefined,
        limit: 20,
      })
      setDiscussionResult(result)
    } catch (e) {
      setDiscussionError((e as Error).message || String(e))
      setDiscussionResult(null)
    } finally { setDiscussionLoading(false) }
  }

  const candidateThreadLink = (tid: number, pid: number | null) => pid == null
    ? `/threads/${tid}`
    : `/threads/${tid}#pid-${pid}`
  const candidateChatLink = (tid: number, forum: number) => `/chat?${new URLSearchParams({ mode: 'selected', tids: String(tid), forum_ids: String(forum) }).toString()}`

  return (
    <div className={`tool-page rag-page rag-view-${view}`}>
      <header className="tool-page-header"><div><h1>{tx('讨论资料库', 'Discussion library')}</h1><p>{tx('按关键词、论坛、日期或 TID 查找讨论候选，并直接打开本地原帖。', 'Find discussion candidates by keyword, forum, date, or TID, then open the local post.')}</p></div></header>
      <nav className="tool-tabs" aria-label={tx('知识库功能', 'Knowledge library sections')}><button aria-pressed={view === "search"} onClick={() => setView("search")}>{tx('讨论搜索', 'Discussion search')}</button><button aria-pressed={view === "index"} onClick={() => setView("index")}>{tx('RAG 索引维护', 'RAG index maintenance')}</button></nav>
      {view === 'index' && loading && <LoadingIndicator label={t('loading')} />}
      {view === 'search' && <>
        <section className="panel knowledge-search-panel">
          <div className="rag-panel-head"><div><h2 className="rag-section-title">{tx('查找讨论资料', 'Find discussions')}</h2><p className="rag-panel-copy">{tx('这项检索直接查找标题和已存档楼层，不需要先建立 RAG 向量索引。结果是待核对的候选，打开原帖阅读后再用于分析。', 'This search checks titles and archived posts directly; you do not need to build a RAG vector index first. Results are candidates to review in the original thread before analysis.')}</p></div></div>
          <form className="knowledge-search-form" onSubmit={event => { event.preventDefault(); void handleDiscussionSearch() }}>
            <label className="knowledge-query-field"><span>{tx('关键词', 'Keyword')}</span><input value={discussionQuery} onChange={event => setDiscussionQuery(event.target.value)} placeholder={tx('输入标题或楼层中的关键词', 'Search titles or post text')} /></label>
            <label className="rag-field"><span>TID</span><input type="number" min="1" value={discussionTid} onChange={event => setDiscussionTid(event.target.value)} placeholder={tx('可选', 'Optional')} /></label>
            <label className="rag-field"><span>{t('forum')}</span><select value={discussionForumId} onChange={event => setDiscussionForumId(event.target.value)}><option value="all">{t('all_forums')}</option>{discussionForums.map(forum => <option key={forum.forum_id} value={forum.forum_id}>{lang === 'en' ? (forum.name_en || forum.name) : forum.name}</option>)}</select></label>
            <label className="rag-field"><span>{tx('开始日期（含）', 'Start date (inclusive)')}</span><input type="date" value={discussionStartDate} onChange={event => setDiscussionStartDate(event.target.value)} /></label>
            <label className="rag-field"><span>{tx('结束日期（含）', 'End date (inclusive)')}</span><input type="date" value={discussionEndDate} onChange={event => setDiscussionEndDate(event.target.value)} /></label>
            <button className="btn-primary" type="submit" disabled={discussionLoading || discussionForums.length === 0}>{discussionLoading ? tx('正在检索…', 'Searching…') : t('search')}</button>
          </form>
          {!loading && discussionForums.length === 0 && <div className="knowledge-empty-state" role={forumsError ? 'alert' : 'status'}>{forumsError || tx('当前没有启用的讨论类论坛，无法搜索讨论资料。', 'No enabled discussion forums are available, so discussion search cannot run.')}</div>}
          <p className="knowledge-date-hint">{tx('日期按北京时间（UTC+8）解释，结束日期包含当天。', 'Dates use Beijing time (UTC+8); the end date includes that day.')}</p>
          {discussionError && <div className="rag-helper-text rag-helper-error" role="alert">{discussionError}</div>}
          {discussionResult?.result_status === 'partial_index_unavailable' && <div className="knowledge-partial-state" role="status">{tx('部分检索不可用：当前环境无法搜索楼层正文，结果可能不完整。宽范围搜索建议使用包含至少 3 个连续字母或数字的关键词；也可填写 TID 搜索指定帖子。', 'Partial search unavailable: post text could not be searched in this environment, so results may be incomplete. Broad searches work best with at least three consecutive letters or digits; add a TID to search a specific thread.')}</div>}
          {discussionResult && <div className="rag-result-meta"><span>{tx('候选数量', 'Candidates')}: <strong>{discussionResult.count}</strong></span><span>{tx('正文检索', 'Post text search')}: <strong>{discussionResult.body_search_status === 'searched' ? tx('已检索', 'searched') : discussionResult.body_search_status === 'INDEX_UNAVAILABLE' ? tx('不可用', 'unavailable') : tx('未请求', 'not requested')}</strong></span></div>}
          <div className="knowledge-results">
            {discussionResult?.items.map(item => <article className="knowledge-result-card" key={`${item.tid}-${item.pid ?? 'thread'}`}>
              <div className="knowledge-result-title-row"><div><Link className="knowledge-result-title" to={candidateThreadLink(item.tid, item.pid)}>{item.title || `${t('thread_link')} ${item.tid}`}</Link><div className="knowledge-result-meta"><span>TID {item.tid}</span><span>{t('forum')} {forumMap[item.forum_id] || item.forum_id}</span><span>{tx('命中时间', 'Matched')}: {formatDateTimeStacked(item.pub_time)}</span>{item.pid && <span>PID {item.pid}</span>}</div></div><span className="knowledge-candidate-tag">{tx('候选 · 尚未阅读', 'Candidate · not read')}</span></div>
              <p className="knowledge-result-snippet">{item.snippet || tx('标题匹配；打开原帖查看内容。', 'Title match; open the thread to review its content.')}</p>
              <div className="knowledge-result-actions"><Link to={candidateThreadLink(item.tid, item.pid)}>{tx('打开本地原帖', 'Open local thread')}</Link><Link className="button button-primary" to={candidateChatLink(item.tid, item.forum_id)}>{tx('在助手中分析此帖', 'Analyze this thread in Chat')}</Link></div><p className="knowledge-scope-hint">{tx('跳转会传入此帖的 TID 和论坛范围；范围由 Chat 服务在提交 Run 时冻结。', 'The link passes this thread TID and forum scope; the Chat service freezes the scope when the Run is submitted.')}</p>
            </article>)}
            {discussionSubmitted && discussionResult?.count === 0 && !discussionLoading && <div className="knowledge-empty-state" role="status">{tx('没有找到讨论候选。可调整关键词、日期或论坛范围；也可以确认该 TID 已在本地资料中。', 'No discussion candidates found. Try another keyword, date, or forum, and check that the TID is available locally.')}</div>}
            {discussionSubmitted && discussionLoading && <div className="knowledge-empty-state" role="status">{tx('正在查找讨论…', 'Searching discussion records…')}</div>}
          </div>
        </section>
        <section className="panel knowledge-reports-panel">
          <div className="rag-panel-head"><div><h2 className="rag-section-title">{tx('日报期次', 'Daily report issues')}</h2><p className="rag-panel-copy">{tx('期次会先排入后台，再显示资料覆盖、失败原因和已保存的报告修订。排队不代表出刊完成。', 'Issues enter the background queue first; coverage, failures, and saved report revisions appear as work progresses. Queued does not mean published.')}</p></div><button className="btn-subtle" onClick={() => void loadDailyData()} disabled={!chatSessions.selected || dailyLoading}>{tx('刷新期次', 'Refresh issues')}</button></div>
          <label className="rag-field"><span>{tx('日报所属会话', 'Report session')}</span><select value={chatSessions.selected} disabled={chatSessions.loading} onChange={event => void chatSessions.select(event.target.value)}><option value="" disabled>{tx('选择会话', 'Select a session')}</option>{chatSessions.sessions.map(session => <option key={session.id} value={session.id}>{session.title || session.id}</option>)}</select></label>
          {chatSessions.hasMore && <button className="btn-subtle" onClick={() => void chatSessions.loadMore()}>{tx('加载更多会话', 'Load more sessions')}</button>}
          {!chatSessions.selected && <div className="knowledge-empty-state" role="status">{tx('请先在 Chat 中创建或选择会话，再管理私有日报期次和规则。', 'Create or select a Chat session before managing its private report issues and rules.')}</div>}
          {dailyMessage && <div className={`knowledge-partial-state ${dailyMessageIsError ? 'rag-helper-error' : ''}`} role={dailyMessageIsError ? 'alert' : 'status'}>{dailyMessage}</div>}
          <div className="daily-brief-grid">
            <section className="daily-brief-card">
              <h3>{tx('手动出刊', 'Create an issue')}</h3>
              <label className="rag-field"><span>{tx('目标日期（北京时间）', 'Target day (Beijing time)')}</span><input type="date" value={dailyTargetDay} onChange={event => setDailyTargetDay(event.target.value)} /></label>
              <fieldset className="daily-forum-list"><legend>{tx('讨论论坛范围', 'Discussion forums')}</legend>{discussionForums.map(forum => <label key={forum.forum_id}><input type="checkbox" checked={dailyForumIds.includes(forum.forum_id)} onChange={() => toggleDailyForum(forum.forum_id)} />{lang === 'en' ? (forum.name_en || forum.name) : forum.name}</label>)}</fieldset>
              <button className="btn-primary" disabled={!chatSessions.selected || dailyLoading || !dailyForumIds.length || !dailyTargetDay} onClick={() => void submitManualIssue()}>{dailyLoading ? tx('提交中…', 'Submitting…') : tx('创建目标日期期次', 'Create issue')}</button>
            </section>
            <section className="daily-brief-card">
              <h3>{tx('定时规则', 'Schedule rules')}</h3>
              {dailyRules.length > 0 && <label className="rag-field"><span>{tx('选择规则', 'Select a rule')}</span><select value={dailySelectedRule} onChange={event => { const id = event.target.value; setDailySelectedRule(id); const rule = dailyRules.find(item => item.rule_id === id); if (rule) { setDailyForumIds(rule.forum_ids); setDailyExecutionTime(rule.execution_time.slice(0, 5)); setDailyDeadline(rule.preparation_deadline.slice(0, 5)) } }}><option value="">{tx('新规则', 'New rule')}</option>{dailyRules.map(rule => <option key={rule.rule_id} value={rule.rule_id}>{rule.enabled ? tx('启用', 'Enabled') : tx('已暂停', 'Paused')} · {rule.forum_ids.join(', ')}</option>)}</select></label>}
              <div className="daily-time-fields"><label className="rag-field"><span>{tx('开始生成', 'Start generation')}</span><input type="time" value={dailyExecutionTime} onChange={event => setDailyExecutionTime(event.target.value)} /></label><label className="rag-field"><span>{tx('资料等待截止', 'Source wait deadline')}</span><input type="time" value={dailyDeadline} onChange={event => setDailyDeadline(event.target.value)} /></label></div>
              <p className="knowledge-date-hint">{tx('每天到“开始生成”时启动本期任务；若候选帖归档仍在进行，最多等到“资料等待截止”，之后按已核验资料保存覆盖缺口。截止时间必须晚于开始时间。', 'The daily issue job starts at “Start generation.” If candidate archives are still running, it waits until “Source wait deadline,” then records any remaining coverage gaps. The deadline must be later than the start time.')}</p>
              <p className="daily-next-run">{(() => { const rule = dailyRules.find(item => item.rule_id === dailySelectedRule); return rule ? <>{tx('当前下次执行', 'Current next run')}: <strong>{rule.enabled ? formatDateTime(rule.next_run_at) : tx('已暂停', 'Paused')}</strong> · revision {rule.revision}</> : tx('尚未设置定时规则。', 'No schedule rule is configured.') })()}</p>
              <div className="daily-rule-actions"><button className="btn-primary" disabled={!chatSessions.selected || dailyLoading || !dailyForumIds.length} onClick={() => void saveDailyRule(true)}>{tx('保存并启用', 'Save and enable')}</button>{dailySelectedRule && <button className="btn-subtle" disabled={dailyLoading} onClick={() => void saveDailyRule(!dailyRules.find(item => item.rule_id === dailySelectedRule)?.enabled)}>{dailyRules.find(item => item.rule_id === dailySelectedRule)?.enabled ? tx('暂停规则', 'Pause rule') : tx('恢复规则', 'Resume rule')}</button>}<button className="btn-subtle" disabled={!chatSessions.selected || dailyLoading || !dailyForumIds.length} onClick={() => void saveDailyRule(true, true)}>{tx('新增规则', 'Add another rule')}</button></div>
            </section>
          </div>
          <div className="daily-issue-layout">
            <div className="daily-issue-list" aria-label={tx('日报期次历史', 'Report issue history')}>
              {dailyIssues.map(({ issue, latest_revision, queued_job }) => <button type="button" className={`daily-issue-row ${dailySelectedIssue === issue.issue_id ? 'is-selected' : ''}`} key={issue.issue_id} onClick={() => setDailySelectedIssue(issue.issue_id)}><span><strong>{issue.target_day}</strong><small>{issue.source_kind === 'scheduled' ? tx('定时', 'Scheduled') : tx('手动', 'Manual')} · {issue.issue_id.slice(0, 8)}</small></span><span className={`daily-state state-${issue.state}`}>{issue.state}</span><small>{queued_job ? `${tx('后台任务', 'Job')}: ${queued_job.status}` : latest_revision ? `${tx('报告修订', 'Report revision')} ${latest_revision.report_revision} · ${tx('覆盖', 'coverage')}: ${latest_revision.coverage_status}${latest_revision.gap_reasons_json?.length ? ` · ${latest_revision.gap_reasons_json.length} ${tx('项缺口', 'gaps')}` : ''}` : tx('尚无报告修订', 'No report revision yet')}</small></button>)}
              {dailyIssues.length === 0 && <div className="knowledge-empty-state" role="status">{tx('还没有日报期次。选择目标日期手动出刊，或启用定时规则。', 'No report issues yet. Create one for a target day or enable a schedule.')}</div>}
              {dailyIssues.length > 0 && dailyIssues.length % 50 === 0 && <button className="btn-subtle" disabled={dailyLoading} onClick={() => void loadDailyData(dailyIssues.length)}>{tx('加载更早期次', 'Load older issues')}</button>}
            </div>
            {dailyIssueDetail && <article className="daily-issue-detail">
              <div className="daily-detail-heading"><div><h3>{dailyIssueDetail.issue.target_day} · {tx('期次详情', 'Issue details')}</h3><p>{tx('期次状态', 'Issue state')}: <strong>{dailyIssueDetail.issue.state}</strong>{dailyIssueDetail.queued_job && <> · {tx('后台任务', 'Job')}: {dailyIssueDetail.queued_job.status} ({dailyIssueDetail.queued_job.job_id})</>}</p></div></div>
              {dailyIssueDetail.queued_job && <p className="knowledge-date-hint">{tx('任务仍在运行时这里只报告队列/执行状态，不将其显示为完成。', 'While the job is active this shows queue/execution state; it is not marked complete.')}</p>}
              <div className="daily-attempts"><h4>{tx('执行尝试与覆盖情况', 'Attempts and coverage')}</h4>{dailyIssueDetail.attempts.length === 0 && <p>{tx('尚无执行尝试。', 'No attempts yet.')}</p>}{dailyIssueDetail.attempts.map(attempt => <div className="daily-attempt" key={attempt.attempt_id}><strong>#{attempt.attempt_no} · {attempt.status}</strong>{attempt.error_message && <p className="rag-helper-error">{attempt.error_code ? `${attempt.error_code}: ` : ''}{attempt.error_message}</p>}<p>{tx('覆盖状态', 'Coverage')}: {attempt.coverage_json?.coverage_status || tx('尚无回执', 'No receipt yet')}</p>{Boolean(attempt.coverage_json?.gap_reasons?.length) && <ul>{attempt.coverage_json?.gap_reasons?.map((reason: string, index: number) => <li key={`${attempt.attempt_id}-gap-${index}`}>{reason}</li>)}</ul>}</div>)}</div>
              {dailyIssueDetail.issue.source_kind === 'manual' && <div className="daily-retry"><h4>{tx('明确补报入口', 'Regenerate this manual issue')}</h4><label className="rag-field"><span>{tx('补报原因（必填）', 'Reason (required)')}</span><input value={dailyRetryReason} maxLength={500} onChange={event => setDailyRetryReason(event.target.value)} placeholder={tx('说明需要重新生成的原因', 'Explain why it needs regeneration')} /></label><button className="btn-subtle" disabled={dailyLoading || !dailyRetryReason.trim()} onClick={() => void retryDailyIssue()}>{tx('排入补报任务', 'Queue regeneration')}</button></div>}
              <div className="daily-revisions"><h4>{tx('不可变报告修订', 'Immutable report revisions')}</h4>{dailyIssueDetail.revisions.length === 0 && <div className="knowledge-empty-state">{tx('报告尚未保存。', 'No report has been saved yet.')}</div>}{dailyIssueDetail.revisions.map(revision => <section className="daily-revision" key={revision.revision_id}><div className="daily-revision-title"><strong>revision {revision.report_revision} · {revision.status}</strong><span>{tx('覆盖', 'Coverage')}: {revision.coverage_status}</span></div>{revision.regeneration_reason && <p>{tx('补报原因', 'Regeneration reason')}: {revision.regeneration_reason}</p>}{Boolean(revision.gap_reasons_json?.length) && <div><strong>{tx('资料缺口', 'Coverage gaps')}</strong><ul>{revision.gap_reasons_json?.map((reason, index) => <li key={`${revision.revision_id}-gap-${index}`}>{reason}</li>)}</ul></div>}{revision.body_markdown && <div className="daily-report-body"><Markdown content={readableDailyBody(revision.body_markdown)} /></div>}{!revision.body_markdown && Boolean(revision.source_receipts_json?.length) && <div><strong>{tx('已读取的来源', 'Read sources')}</strong><div className="daily-source-links">{revision.source_receipts_json?.map((source, index) => <Link key={`${revision.revision_id}-${source.tid}-${source.pid}`} to={`/threads/${source.tid}#pid-${source.pid}`}>{tx(`来源 ${index + 1}`, `Source ${index + 1}`)}</Link>)}</div></div>}<Link className="button button-primary daily-followup" to={dailyRevisionChatHref(dailyIssueDetail.issue, revision, chatSessions.selected)}>{tx('携本修订到 Chat 继续提问', 'Ask follow-up questions in Chat')}</Link><p className="knowledge-date-hint">{tx('此链接携带唯一修订 ID、日期和论坛/候选帖子范围。进入 Chat 后，助手可读取本修订正文和覆盖信息；引用的原帖仍需单独打开核对。', 'This link carries the unique revision ID, date, and forum/candidate thread scope. In Chat, the assistant can read this revision and its coverage details; open cited threads separately to verify the original posts.')}</p></section>)}</div>
            </article>}
          </div>
        </section>
      </>}
      {view === 'index' && !overview && <div className="panel tool-error" role="alert"><h2>{tx('RAG 索引状态不可用', 'RAG index status unavailable')}</h2><p>{indexMessage || t('error')}</p><button className="btn-subtle" onClick={() => setOverviewRetry(value => value + 1)}>{tx('重新加载', 'Reload')}</button></div>}
      {view === 'index' && overview && !overview.enabled && <div className="panel tool-state" role="status">{tx('RAG 索引服务尚未启用。讨论资料搜索仍可独立使用。请在', 'RAG indexing is not enabled. Discussion search remains available. Check')} <Link to="/settings">{t('settings')}</Link> {tx('检查向量检索配置。', 'for vector search settings.')}</div>}
      {view === 'index' && overview && <>
      <div className="stat-row rag-index-only">
        <div className="stat-cell"><div className="label">{t('rag_unindexed_threads')}</div><div className="value">{overview.counts.unindexed_threads}</div></div>
        <div className="stat-cell"><div className="label">{t('rag_indexed_threads')}</div><div className="value">{overview.counts.indexed_threads}</div></div>
        <div className="stat-cell"><div className="label">{t('rag_total_chunks')}</div><div className="value">{overview.counts.total_chunks}</div></div>
        <div className="stat-cell"><div className="label">{t('rag_indexed_chunks')}</div><div className="value">{overview.counts.indexed_chunks}</div></div>
        <div className="stat-cell"><div className="label">{t('rag_failed_chunks')}</div><button className="value rag-failed-filter" aria-pressed={failedOnly} onClick={() => setFailedOnly(value => !value)}>{overview.counts.failed_chunks}<span>{failedOnly ? tx('显示全部', 'Show all') : tx('查看失败项', 'View failed')}</span></button></div>
      </div>

      <div className="rag-layout">
        <section className="panel rag-index-only" style={{ gridColumn: '1 / -1' }}>
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
      </>}
    </div>
  )
}
