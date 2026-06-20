import { useEffect, useState, useRef, useCallback } from 'react'
import { api, type LogEntry } from '../api/client'

const LEVEL_COLORS: Record<string, string> = {
  DEBUG: 'var(--text-tertiary)',
  INFO: 'var(--text-secondary)',
  WARNING: 'var(--status-warn)',
  ERROR: 'var(--status-error)',
  CRITICAL: 'var(--status-error)',
}

export function Logs() {
  const [entries, setEntries] = useState<LogEntry[]>([])
  const [autoScroll, setAutoScroll] = useState(true)
  const [paused, setPaused] = useState(false)
  const tailRef = useRef<HTMLDivElement>(null)
  const lastTsRef = useRef<number | undefined>(undefined)

  const fetchLogs = useCallback(async () => {
    if (paused) return
    try {
      const data = await api.logs(300, lastTsRef.current)
      if (data.entries.length > 0) {
        setEntries(prev => {
          const merged = [...prev, ...data.entries]
          // Keep last 1000 entries
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

  return (
    <>
      <div className="filter-bar">
        <button className="btn-subtle" onClick={fetchLogs}>刷新</button>
        <button className="btn-subtle" onClick={() => setPaused(p => !p)}>
          {paused ? '▶ 继续' : '⏸ 暂停'}
        </button>
        <button className="btn-subtle" onClick={clearLogs}>清空</button>
        <label style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 12, color: 'var(--text-tertiary)', marginLeft: 'auto' }}>
          <input type="checkbox" checked={autoScroll} onChange={e => setAutoScroll(e.target.checked)} />
          自动滚动
        </label>
        <span style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>{entries.length} 条</span>
      </div>

      <div className="log-view">
        {entries.length === 0 ? (
          <div style={{ color: 'var(--text-tertiary)', padding: 16 }}>暂无日志输出。日志会在服务运行时自动收集。</div>
        ) : entries.map((e, i) => (
          <div key={i} className="log-line">
            <span className="log-time">{formatTime(e.ts)}</span>
            <span className="log-level" style={{ color: LEVEL_COLORS[e.level] || 'var(--text-secondary)' }}>{e.level}</span>
            <span className="log-msg">{e.msg}</span>
          </div>
        ))}
        <div ref={tailRef} />
      </div>
    </>
  )
}

function formatTime(ts: number): string {
  const d = new Date(ts * 1000)
  return d.toLocaleTimeString('zh-CN', { hour12: false }) + '.' + String(d.getMilliseconds()).padStart(3, '0')
}
