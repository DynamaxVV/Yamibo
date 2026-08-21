import { useEffect, useState, useRef, useCallback, useMemo } from 'react'
import { api, type LogEntry } from '../api/client'
import { useI18n } from '../context/I18nContext'

const LEVEL_COLORS: Record<string, string> = {
  DEBUG: 'var(--text-tertiary)',
  INFO: 'var(--accent-blue)',
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
  job: 'var(--accent-blue)',
  daemon: 'var(--accent-purple)',
  query: 'var(--accent-cyan)',
  trend: 'var(--accent-green)',
  remote: 'var(--status-warn)',
  maintenance: 'var(--text-secondary)',
}

export function Logs() {
  const { t } = useI18n()
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

  const fetchLogs = useCallback(async () => {
    if (paused) return
    try {
      const data = await api.logs({ limit: 500, since: lastTsRef.current })
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
    } catch { /* ignore */ }
  }, [paused])

  useEffect(() => {
    fetchLogs()
    const interval = setInterval(fetchLogs, 2000)
    return () => clearInterval(interval)
  }, [fetchLogs])

  useEffect(() => {
    if (autoScroll && tailRef.current) {
      tailRef.current.scrollIntoView({ behavior: 'smooth' })
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
    <>
      <div className="log-toolbar">
        <button className="btn-subtle" onClick={fetchLogs}>{t('refresh')}</button>
        <button className="btn-subtle" onClick={() => setPaused(p => !p)}>
          {paused ? `▶ ${t('resume')}` : `⏸ ${t('pause')}`}
        </button>
        <button className="btn-subtle" onClick={clearLogs}>{t('clear')}</button>
        <span className="log-toolbar-sep" />
        <select value={minLevel} onChange={e => setMinLevel(e.target.value as LevelFilter)} className="log-toolbar-select">
          {LEVEL_ORDER.map(l => <option key={l} value={l}>{l}</option>)}
        </select>
        <select value={filterCat} onChange={e => setFilterCat(e.target.value)} className="log-toolbar-select">
          <option value="">all</option>
          {categories.map(c => <option key={c} value={c}>{c}</option>)}
        </select>
        <input type="text" placeholder="job_id…" value={filterJob}
          onChange={e => setFilterJob(e.target.value)} className="log-toolbar-input" />
        <input type="text" placeholder="tid…" value={filterTid}
          onChange={e => setFilterTid(e.target.value)} className="log-toolbar-input" />
        <input type="text" placeholder="event_type…" value={filterEvent}
          onChange={e => setFilterEvent(e.target.value)} className="log-toolbar-input" />
        <input type="text" placeholder="message / payload…" value={filterText}
          onChange={e => setFilterText(e.target.value)} className="log-toolbar-input" />
        <span className="log-toolbar-sep" />
        <label className="log-toolbar-check">
          <input type="checkbox" checked={autoScroll} onChange={e => setAutoScroll(e.target.checked)} />
          {t('auto_scroll')}
        </label>
        <span className="log-toolbar-count">{filtered.length} lines</span>
      </div>

      <div className="log-view" style={{ maxHeight: 'calc(100vh - 280px)' }}>
        {filtered.length === 0 ? (
          <div className="log-empty">{t('no_logs')}</div>
        ) : filtered.map((e, i) => {
          const isExpanded = expandedIdx === i
          const cat = eventCategory(e.event_type)
          const label = eventLabel(e.event_type)
          const catColor = CAT_COLORS[cat] || 'var(--text-secondary)'
          const hasDetail = true
          return (
            <div key={i} className={`log-row${e.level === 'ERROR' || e.level === 'CRITICAL' ? ' log-row-err' : ''}`}
              onClick={() => setExpandedIdx(isExpanded ? null : i)}>
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
    </>
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
