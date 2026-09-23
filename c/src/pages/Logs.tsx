import '../styles/tools.css'
import { useEffect, useState, useRef, useCallback, useMemo } from 'react'
import { api, type LogEntry } from '../api/client'
import { LoadingSpinner } from '../components/LoadingIndicator'
import { useI18n } from '../context/I18nContext'

const LEVEL_COLORS: Record<string, string> = {
  DEBUG: 'var(--text-tertiary)',
  INFO: 'var(--primary)',
  WARNING: 'var(--status-warn)',
  ERROR: 'var(--status-error)',
  CRITICAL: 'var(--status-error)',
}

const LEVEL_LABEL: Record<string, string> = {
  DEBUG: 'DBG',
  INFO: 'INF',
  WARNING: 'WRN',
  ERROR: 'ERR',
  CRITICAL: 'CRT',
}

const LEVEL_ORDER = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'] as const
type LevelFilter = typeof LEVEL_ORDER[number]

function formatTs(ts: string): string {
  if (!ts) return ''
  const d = new Date(ts)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

function eventCategory(type: string): string {
  const dot = type.indexOf('.')
  return dot > 0 ? type.slice(0, dot) : type
}

function eventLabel(type: string): string {
  const dot = type.indexOf('.')
  return dot > 0 ? type.slice(dot + 1) : type
}

const CAT_COLORS: Record<string, string> = {
  job: 'var(--primary)',
  daemon: 'var(--text-secondary)',
  query: 'var(--text-secondary)',
  trend: 'var(--status-success)',
  remote: 'var(--status-warn)',
  maintenance: 'var(--text-secondary)',
}

export function Logs() {
  const { t, tx } = useI18n()
  const [fetchError, setFetchError] = useState('')
  const [fetching, setFetching] = useState(true)
  const [entries, setEntries] = useState<LogEntry[]>([])
  const [autoScroll, setAutoScroll] = useState(true)
  const [paused, setPaused] = useState(false)
  const [minLevel, setMinLevel] = useState<LevelFilter>('INFO')
  const [filterCat, setFilterCat] = useState('')
  const [filterJob, setFilterJob] = useState('')
  const [filterTid, setFilterTid] = useState('')
  const [filterEvent, setFilterEvent] = useState('')
  const [filterText, setFilterText] = useState('')
  const [expandedIdx, setExpandedIdx] = useState<number | null>(null)
  const tailRef = useRef<HTMLDivElement>(null)
  const lastTsRef = useRef<string | undefined>(undefined)

  const fetchLogs = useCallback(async (manual = false) => {
    if (paused && !manual) return
    try {
      const data = await api.logs({ limit: 500, since: lastTsRef.current })
      setFetchError('')
      if (data.entries.length > 0) {
        setEntries(prev => {
          const seen = new Set(prev.map(e => e.ts + e.event_type + e.message))
          const fresh = data.entries.filter(e => !seen.has(e.ts + e.event_type + e.message))
          if (fresh.length === 0) return prev
          const merged = [...prev, ...fresh]
          return merged.length > 1000 ? merged.slice(-1000) : merged
        })
        lastTsRef.current = data.entries[data.entries.length - 1].ts
      }
    } catch (e) { setFetchError(e instanceof Error ? e.message : String(e)) }
    finally { setFetching(false) }
  }, [paused])

  useEffect(() => {
    fetchLogs()
    const interval = setInterval(fetchLogs, 2000)
    return () => clearInterval(interval)
  }, [fetchLogs])

  useEffect(() => {
    if (autoScroll && tailRef.current) {
      tailRef.current.scrollIntoView({ behavior: 'auto', block: 'nearest' })
    }
  }, [entries, autoScroll])

  const clearLogs = () => {
    setEntries([])
    lastTsRef.current = undefined
  }

  const minIdx = LEVEL_ORDER.indexOf(minLevel)
  const filtered = useMemo(() => {
    let list = entries.filter(e => LEVEL_ORDER.indexOf(e.level as LevelFilter) >= minIdx)
    if (filterCat) list = list.filter(e => eventCategory(e.event_type) === filterCat)
    if (filterJob) list = list.filter(e => e.job_id?.includes(filterJob))
    if (filterTid) list = list.filter(e => String(e.tid ?? '').includes(filterTid))
    if (filterEvent) list = list.filter(e => e.event_type.toLowerCase().includes(filterEvent.toLowerCase()))
    if (filterText) {
      const needle = filterText.toLowerCase()
      list = list.filter(e => JSON.stringify(e).toLowerCase().includes(needle))
    }
    return list
  }, [entries, minIdx, filterCat, filterJob, filterTid, filterEvent, filterText])

  const categories = useMemo(() => {
    const cats = new Set<string>()
    for (const e of entries) cats.add(eventCategory(e.event_type))
    return [...cats].sort()
  }, [entries])

  return (
    <div className="tool-page logs-page">
      <header className="tool-page-header"><div><h1>{tx('系统日志', 'System logs')}</h1><p>{tx('按级别、模块和关键字定位问题；点击日志查看完整消息。', 'Filter by level, module, or keyword. Select a log entry to inspect its full message.')}</p></div><button className="btn-subtle" onClick={clearLogs}>{tx('清空当前显示', 'Clear visible logs')}</button></header>
      {fetchError && <div className="panel tool-error" role="alert">{tx('日志更新失败：', 'Unable to refresh logs: ')}{fetchError}{tx('。已保留当前记录。', '. Existing entries are kept.')}</div>}
      {(paused || !autoScroll) && <p className="tool-state" role="status">{paused ? tx('已暂停接收新日志。', 'Receiving new logs is paused.') : tx('正在查看历史，自动滚动已关闭。', 'Viewing history; auto-scroll is off.')}</p>}
      <div className="log-toolbar">
        <div className="log-controls">
        <button className="btn-subtle" onClick={() => void fetchLogs(true)}>{t('refresh')}</button>
        <button className="btn-subtle" onClick={() => setPaused(p => !p)}>
          {paused ? `▶ ${t('resume')}` : `⏸ ${t('pause')}`}
        </button>
        </div>
        <div className="log-filters">
        <select aria-label={tx('最低日志级别', 'Minimum log level')} value={minLevel} onChange={e => setMinLevel(e.target.value as LevelFilter)} className="log-toolbar-select">
          {LEVEL_ORDER.map(l => <option key={l} value={l}>{l}</option>)}
        </select>
        <select aria-label={tx('日志模块', 'Log module')} value={filterCat} onChange={e => setFilterCat(e.target.value)} className="log-toolbar-select">
          <option value="">{tx('全部模块', 'All modules')}</option>
          {categories.map(c => <option key={c} value={c}>{c}</option>)}
        </select>
        <input type="text" aria-label={tx('任务 ID', 'Task ID')} placeholder={`${tx('任务 ID', 'Task ID')}…`} value={filterJob}
          onChange={e => setFilterJob(e.target.value)} className="log-toolbar-input" />
        <input type="text" aria-label={tx('帖子 TID', 'Thread ID')} placeholder={`${tx('帖子 TID', 'Thread ID')}…`} value={filterTid}
          onChange={e => setFilterTid(e.target.value)} className="log-toolbar-input" />
        <input type="text" aria-label={tx('事件类型', 'Event type')} placeholder={`${tx('事件类型', 'Event type')}…`} value={filterEvent}
          onChange={e => setFilterEvent(e.target.value)} className="log-toolbar-input" />
        <input type="text" aria-label={tx('消息或数据关键词', 'Message or data keyword')} placeholder={`${tx('消息 / 数据关键词', 'Message or data keyword')}…`} value={filterText}
          onChange={e => setFilterText(e.target.value)} className="log-toolbar-input" />
        </div>
        <div className="log-toolbar-summary">
        <label className="log-toolbar-check">
          <input type="checkbox" checked={autoScroll} onChange={e => setAutoScroll(e.target.checked)} />
          {t('auto_scroll')}
        </label>
        <span className="log-toolbar-count">{filtered.length} {tx('条日志', 'entries')}</span>
        </div>
      </div>

      <div className="log-view" style={{ maxHeight: 'calc(100vh - 280px)' }}>
        {filtered.length === 0 ? (
          <div className="log-empty" role="status">{fetching ? <span className="inline-flex items-center gap-2"><LoadingSpinner />{t('loading')}</span> : fetchError ? tx('暂时无法读取日志，请刷新重试。', 'Logs are temporarily unavailable. Refresh and try again.') : entries.length ? tx('当前筛选没有匹配日志，请调整筛选条件。', 'No logs match these filters. Adjust your search.') : t('no_logs')}</div>
        ) : filtered.map((e, i) => {
          const isExpanded = expandedIdx === i
          const cat = eventCategory(e.event_type)
          const label = eventLabel(e.event_type)
          const catColor = CAT_COLORS[cat] || 'var(--text-secondary)'
          const hasDetail = true
          return (
            <div key={i} className={`log-row${e.level === 'ERROR' || e.level === 'CRITICAL' ? ' log-row-err' : ''}`}
              role="button" tabIndex={0} aria-expanded={isExpanded}
              onKeyDown={event => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); setExpandedIdx(isExpanded ? null : i); setAutoScroll(false) } }}
              onClick={() => { setExpandedIdx(isExpanded ? null : i); setAutoScroll(false) }}>
              <div className="log-row-main">
                <span className="log-col-lvl" style={{ color: LEVEL_COLORS[e.level] || 'var(--text-secondary)' }}>
                  {LEVEL_LABEL[e.level] || e.level}
                </span>
                <span className="log-col-time">{formatTs(e.ts)}</span>
                <span className="log-col-cat" style={{ color: catColor }}>{cat}</span>
                <span className="log-col-label">{label}</span>
                <span className="log-col-msg">{e.message}</span>
                {e.error_code && <span className="log-chip-err" title={e.error_code}>{e.error_code.length > 16 ? e.error_code.slice(0, 14) + '…' : e.error_code}</span>}
                {e.job_id && <span className="log-chip-id" title={e.job_id}>{e.job_id.slice(-8)}</span>}
                {hasDetail && <span className="log-col-expand">{isExpanded ? '▾' : '▸'}</span>}
              </div>
              {isExpanded && <DetailTable entry={e} />}
            </div>
          )
        })}
        <div ref={tailRef} />
      </div>
    </div>
  )
}

function DetailTable({ entry: e }: { entry: LogEntry }) {
  return (
    <table className="log-detail">
      <tbody>
        <Row k="level" v={e.level} />
        <Row k="result" v={e.result && e.result !== 'success' ? e.result : undefined} />
        <Row k="status" v={e.status && e.status !== 'ok' ? e.status : undefined} />
        <Row k="job_id" v={e.job_id} />
        <Row k="job_type" v={e.job_type} />
        <Row k="tid" v={e.tid != null ? String(e.tid) : undefined} />
        <Row k="forum_id" v={e.forum_id != null ? String(e.forum_id) : undefined} />
        <Row k="run_id" v={e.run_id} />
        <Row k="stage" v={e.stage} />
        <Row k="worker_id" v={e.worker_id} />
        <Row k="component" v={e.component} />
        <Row k="error_code" v={e.error_code} err />
        <Row k="error_message" v={e.error_message} err />
        <Row k="retryable" v={e.retryable != null ? String(e.retryable) : undefined} />
        <Row k="attempt" v={e.attempt != null ? String(e.attempt) : undefined} />
        <Row k="duration" v={e.duration_ms != null ? `${e.duration_ms.toFixed(0)}ms` : undefined} />
        <Row k="fallback_mode" v={e.fallback_mode} />
        <Row k="fallback_reason" v={e.fallback_reason} />
        <Row k="agent_hint" v={e.agent_hint} />
        <Row k="next_action" v={e.next_action} />
        <Row k="operation" v={e.operation} />
        <Row k="resource_uri" v={e.resource_uri} />
        <Row k="data_version" v={e.data_version} />
        <Row k="trace_id" v={e.trace_id} />
        <Row k="warning_codes" v={e.warning_codes?.join(', ')} />
        <Row k="tags" v={e.tags?.join(', ')} />
        <Row k="payload" v={formatStructuredValue(e.payload)} />
        <Row k="context" v={formatStructuredValue(e.context)} />
        <Row k="message" v={e.message} />
      </tbody>
    </table>
  )
}

function formatStructuredValue(value?: Record<string, unknown>): string | undefined {
  if (!value || Object.keys(value).length === 0) return undefined
  return JSON.stringify(value, null, 2)
}

function Row({ k, v, err }: { k: string; v?: string; err?: boolean }) {
  if (!v) return null
  return (
    <tr className="log-detail-row">
      <td className="log-detail-k">{k}</td>
      <td className="log-detail-v" style={{ color: err ? 'var(--status-error)' : 'var(--text-primary)' }}>{v}</td>
    </tr>
  )
}
