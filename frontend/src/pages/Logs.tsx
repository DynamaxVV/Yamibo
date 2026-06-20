import { useEffect, useState, useRef, useCallback, useMemo } from 'react'
import { api, type LogEntry } from '../api/client'
import { useI18n } from '../context/I18nContext'
import { formatLogTime } from '../utils/time'

const LEVEL_COLORS: Record<string, string> = {
  DEBUG: 'var(--text-tertiary)',
  INFO: 'var(--text-secondary)',
  WARNING: 'var(--status-warn)',
  ERROR: 'var(--status-error)',
  CRITICAL: 'var(--status-error)',
}

const LEVEL_ORDER = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'] as const
type LevelFilter = typeof LEVEL_ORDER[number]

export function Logs() {
  const { t } = useI18n()
  const [entries, setEntries] = useState<LogEntry[]>([])
  const [autoScroll, setAutoScroll] = useState(true)
  const [paused, setPaused] = useState(false)
  const [minLevel, setMinLevel] = useState<LevelFilter>('INFO')
  const tailRef = useRef<HTMLDivElement>(null)
  const lastTsRef = useRef<number | undefined>(undefined)

  const fetchLogs = useCallback(async () => {
    if (paused) return
    try {
      const data = await api.logs(300, lastTsRef.current)
      if (data.entries.length > 0) {
        setEntries(prev => {
          const merged = [...prev, ...data.entries]
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
  const filtered = useMemo(
    () => entries.filter(e => LEVEL_ORDER.indexOf(e.level as LevelFilter) >= minIdx),
    [entries, minIdx],
  )

  return (
    <>
      <div className="filter-bar">
        <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', alignItems: 'center' }}>
          <button className="btn-subtle" onClick={fetchLogs}>{t('refresh')}</button>
          <button className="btn-subtle" onClick={() => setPaused(p => !p)}>
            {paused ? `▶ ${t('resume')}` : `⏸ ${t('pause')}`}
          </button>
          <button className="btn-subtle" onClick={clearLogs}>{t('clear')}</button>
          <span style={{ fontSize: 12, color: 'var(--text-tertiary)', marginLeft: 4 }}>{t('status')}:</span>
          <select value={minLevel} onChange={e => setMinLevel(e.target.value as LevelFilter)}
            style={{ fontSize: 12, padding: '1px 4px', border: '1px solid var(--border-light)', borderRadius: 3, background: 'var(--bg-surface)', color: 'var(--text-primary)' }}>
            {LEVEL_ORDER.map(l => <option key={l} value={l}>{l}</option>)}
          </select>
          <label style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 12, color: 'var(--text-tertiary)', marginLeft: 'auto' }}>
            <input type="checkbox" checked={autoScroll} onChange={e => setAutoScroll(e.target.checked)} />
            {t('auto_scroll')}
          </label>
          <span style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>{filtered.length} {t('log_unit')}</span>
        </div>
      </div>

      <div className="log-view">
        {filtered.length === 0 ? (
          <div style={{ color: 'var(--text-tertiary)', padding: 16 }}>{t('no_logs')}</div>
        ) : filtered.map((e, i) => (
          <div key={i} className="log-line">
            <span className="log-time">{formatLogTime(e.ts)}</span>
            <span className="log-level" style={{ color: LEVEL_COLORS[e.level] || 'var(--text-secondary)' }}>{e.level}</span>
            <span className="log-msg">{e.msg}</span>
          </div>
        ))}
        <div ref={tailRef} />
      </div>
    </>
  )
}
