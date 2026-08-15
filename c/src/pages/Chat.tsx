import { useEffect, useMemo, useRef, useState, type KeyboardEvent, type MouseEvent } from 'react'
import { api, type ChatContextResponse, type ChatSessionSummary, type ChatStreamEvent, type ChatTurnResponse, type SettingsResponse } from '../api/client'
import { Markdown } from '../components/Markdown'
import { useI18n } from '../context/I18nContext'

type ChatMessage = {
  id: string
  role: 'user' | 'assistant'
  content: string
  response?: ChatTurnResponse
  timestamp: number
  streaming?: boolean
}

type ChatSession = {
  id: string
  title: string
  createdAt: number
  updatedAt: number
  messages: ChatMessage[]
}

type ChatSettingsForm = {
  hermes_endpoint: string
  hermes_port: string
  hermes_host: string
  hermes_api_key: string
  hermes_model: string
  hermes_stream: boolean
}

const CHAT_SESSIONS_STORAGE_KEY = 'yamibo.chat.sessions.v2'
const CHAT_ACTIVE_SESSION_KEY = 'yamibo.chat.activeSession.v2'
const CHAT_CONTEXT_CACHE_KEY = 'yamibo.chat.context.v2'
const CHAT_SETTINGS_CACHE_KEY = 'yamibo.chat.settings.v2'
const CHAT_CACHE_TTL_MS = 10 * 60 * 1000
function toSession(item: ChatSessionSummary): ChatSession {
  return {
    id: item.id,
    title: item.title,
    createdAt: Date.parse(item.created_at) || Date.now(),
    updatedAt: Date.parse(item.updated_at) || Date.now(),
    messages: item.messages.map((message, index) => ({
      id: `${item.id}-${index}-${message.role}`,
      role: message.role,
      content: message.content,
      timestamp: Date.parse(message.created_at) || Date.now(),
    })),
  }
}

function parseBoolean(value: unknown): boolean {
  if (typeof value === 'boolean') return value
  if (typeof value === 'number') return value !== 0
  if (typeof value === 'string') return ['1', 'true', 'yes', 'on'].includes(value.trim().toLowerCase())
  return false
}

function buildChatSettingsForm(payload: SettingsResponse | null): ChatSettingsForm {
  const port = String(payload?.values.hermes_port ?? '8642')
  const host = String(payload?.values.hermes_host ?? 'host.docker.internal')
  const endpoint = String(payload?.values.hermes_endpoint ?? `http://${host}:${port}`)
  return { hermes_endpoint: endpoint, hermes_port: port, hermes_host: host, hermes_api_key: String(payload?.stored.hermes_api_key ?? ''), hermes_model: String(payload?.values.hermes_model ?? 'hermes-agent'), hermes_stream: parseBoolean(payload?.values.hermes_stream) }
}

function createSessionId(): string { return `chat-${Date.now()}-${Math.random().toString(36).slice(2, 8)}` }
function createMessageId(): string { return `msg-${Date.now()}-${Math.random().toString(36).slice(2, 8)}` }
function buildSessionTitle(messages: ChatMessage[]): string {
  const firstUser = messages.find((item) => item.role === 'user')
  const base = firstUser?.content.trim().split(/\s+/).slice(0, 6).join(' ') || '新对话'
  return base.length > 24 ? `${base.slice(0, 24)}…` : base
}

function formatShortTime(ts: number): string {
  return new Date(ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

function readCache<T>(key: string): { value: T; savedAt: number } | null {
  if (typeof window === 'undefined') return null
  try {
    const raw = window.localStorage.getItem(key)
    if (!raw) return null
    const parsed = JSON.parse(raw) as { value: T; savedAt: number }
    if (!parsed || typeof parsed.savedAt !== 'number') return null
    if (Date.now() - parsed.savedAt > CHAT_CACHE_TTL_MS) return null
    return parsed
  } catch {
    return null
  }
}

function writeCache<T>(key: string, value: T) {
  if (typeof window === 'undefined') return
  window.localStorage.setItem(key, JSON.stringify({ value, savedAt: Date.now() }))
}

function isLongMessage(content: string): boolean {
  return content.length > 280 || content.split(/\r?\n/).length > 10
}

export function Chat() {
  const { lang } = useI18n()
  const [context, setContext] = useState<ChatContextResponse | null>(null)
  const [message, setMessage] = useState('')
  const [sessions, setSessions] = useState<ChatSession[]>([])
  const [activeSessionId, setActiveSessionId] = useState('')
  const [loading, setLoading] = useState(true)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [settingsPayload, setSettingsPayload] = useState<SettingsResponse | null>(null)
  const [settingsForm, setSettingsForm] = useState<ChatSettingsForm>(buildChatSettingsForm(null))
  const [settingsLoading, setSettingsLoading] = useState(false)
  const [settingsSaving, setSettingsSaving] = useState(false)
  const [settingsError, setSettingsError] = useState<string | null>(null)
  const [settingsSaved, setSettingsSaved] = useState<string | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<ChatSession | null>(null)
  const [deleteSaving, setDeleteSaving] = useState(false)
  const transcriptRef = useRef<HTMLElement | null>(null)
  const [messageExpanded, setMessageExpanded] = useState<Record<string, boolean>>({})
  const activeSession = useMemo(() => sessions.find((item) => item.id === activeSessionId) || null, [sessions, activeSessionId])
  const messages = activeSession?.messages || []

  const persistSessions = (nextSessions: ChatSession[], nextActiveId?: string) => {
    setSessions(nextSessions)
    if (typeof window !== 'undefined') {
      window.localStorage.setItem(CHAT_SESSIONS_STORAGE_KEY, JSON.stringify(nextSessions))
      if (nextActiveId) window.localStorage.setItem(CHAT_ACTIVE_SESSION_KEY, nextActiveId)
    }
    if (nextActiveId) setActiveSessionId(nextActiveId)
  }

  useEffect(() => {
    let alive = true
    const cachedContext = readCache<ChatContextResponse>(CHAT_CONTEXT_CACHE_KEY)
    const cachedSettings = readCache<SettingsResponse>(CHAT_SETTINGS_CACHE_KEY)
    const cachedSessions = typeof window !== 'undefined' ? window.localStorage.getItem(CHAT_SESSIONS_STORAGE_KEY) : null
    const storedActiveId = typeof window !== 'undefined' ? window.localStorage.getItem(CHAT_ACTIVE_SESSION_KEY) : null
    let cachedSessionSnapshot: ChatSession[] = []

    if (cachedContext?.value) {
      setContext(cachedContext.value)
    }
    if (cachedSettings?.value) {
      setSettingsPayload(cachedSettings.value)
      setSettingsForm(buildChatSettingsForm(cachedSettings.value))
    }
    if (cachedSessions) {
      try {
        const parsedSessions = JSON.parse(cachedSessions) as ChatSession[]
        cachedSessionSnapshot = parsedSessions
        setSessions(parsedSessions)
        setActiveSessionId(storedActiveId && parsedSessions.some((item) => item.id === storedActiveId) ? storedActiveId : parsedSessions[0]?.id || '')
      } catch {
        window.localStorage.removeItem(CHAT_SESSIONS_STORAGE_KEY)
      }
    }

    api.chatContext().then((next) => {
      if (!alive) return
      const parsed = (next.sessions || []).map(toSession)
      const serverSessionIds = new Set(parsed.map((item) => item.id))
      const mergedSessions = parsed.map((serverSession) => {
        const localSession = cachedSessionSnapshot.find((item) => item.id === serverSession.id)
        return localSession && localSession.updatedAt > serverSession.updatedAt ? localSession : serverSession
      })
      for (const localSession of cachedSessionSnapshot) {
        if (!serverSessionIds.has(localSession.id)) mergedSessions.push(localSession)
      }
      const nextActiveId = storedActiveId && mergedSessions.some((item) => item.id === storedActiveId) ? storedActiveId : mergedSessions[0]?.id || ''
      setContext(next)
      setSessions(mergedSessions)
      setActiveSessionId(nextActiveId)
      writeCache(CHAT_CONTEXT_CACHE_KEY, next)
      if (typeof window !== 'undefined') window.localStorage.setItem(CHAT_ACTIVE_SESSION_KEY, nextActiveId)
      setLoading(false)
    }).catch((e: Error) => {
      if (!alive) return
      if (!cachedContext?.value && !cachedSessions) setError(e.message)
      setLoading(false)
    })
    return () => { alive = false }
  }, [])

  useEffect(() => {
    const el = transcriptRef.current
    if (!el) return
    el.scrollTop = el.scrollHeight
  }, [messages.length, activeSessionId, submitting])

  useEffect(() => {
    const next: Record<string, boolean> = {}
    for (const session of sessions) {
      for (const item of session.messages) {
        if (item.role === 'assistant' && isLongMessage(item.content)) next[item.id] = false
      }
    }
    setMessageExpanded(next)
  }, [sessions])

  const currentTransport = useMemo(() => context?.transports.find((item) => item.id === 'hermes_http'), [context])

  const loadSettings = async () => {
    setSettingsLoading(true)
    setSettingsError(null)
    try {
      const payload = await api.settings()
      setSettingsPayload(payload)
      setSettingsForm(buildChatSettingsForm(payload))
      writeCache(CHAT_SETTINGS_CACHE_KEY, payload)
      if (typeof window !== 'undefined') {
        window.localStorage.setItem(CHAT_SETTINGS_CACHE_KEY, JSON.stringify({ value: payload, savedAt: Date.now() }))
      }
    } catch (e: any) {
      setSettingsError(e.message || String(e))
    } finally { setSettingsLoading(false) }
  }

  const openSettings = async () => { setSettingsOpen(true); await loadSettings() }

  const testHermesConnection = async () => {
    setSettingsSaving(true)
    setSettingsError(null)
    setSettingsSaved(null)
    try {
      const port = Number.parseInt(settingsForm.hermes_port.trim(), 10)
      if (!Number.isFinite(port) || port <= 0) throw new Error(lang === 'en' ? 'Port must be a positive integer' : '端口必须是正整数')
      const result = await api.testHermesConnection()
      setSettingsSaved(result.connected ? (lang === 'en' ? 'Connection ok' : '连接正常') : (lang === 'en' ? 'Connection failed' : '连接失败'))
      await api.chatContext().then((next) => {
        setContext(next)
        writeCache(CHAT_CONTEXT_CACHE_KEY, next)
      })
    } catch (e: any) { setSettingsError(e.message || String(e)) } finally { setSettingsSaving(false) }
  }

  const saveSettings = async () => {
    setSettingsSaving(true)
    setSettingsError(null)
    setSettingsSaved(null)
    try {
      const port = Number.parseInt(settingsForm.hermes_port.trim(), 10)
      if (!Number.isFinite(port) || port <= 0) throw new Error(lang === 'en' ? 'Port must be a positive integer' : '端口必须是正整数')
      const endpoint = settingsForm.hermes_endpoint.trim() || `http://${settingsForm.hermes_host.trim() || 'host.docker.internal'}:${port}`
      let host = settingsForm.hermes_host.trim()
      try { host = new URL(endpoint).hostname || host } catch { /* noop */ }
      const payload = await api.updateSettings({ hermes_port: port, hermes_endpoint: endpoint, hermes_host: host || 'host.docker.internal', hermes_api_key: settingsForm.hermes_api_key.trim() || null, hermes_model: settingsForm.hermes_model.trim() || 'hermes-agent', hermes_stream: settingsForm.hermes_stream })
      setSettingsPayload(payload)
      setSettingsForm(buildChatSettingsForm(payload))
      writeCache(CHAT_SETTINGS_CACHE_KEY, payload)
      setSettingsSaved(lang === 'en' ? 'Saved' : '已保存')
      await loadSettings()
      await api.chatContext().then((next) => {
        setContext(next)
        writeCache(CHAT_CONTEXT_CACHE_KEY, next)
      })
      setSettingsOpen(false)
    } catch (e: any) { setSettingsError(e.message || String(e)) } finally { setSettingsSaving(false) }
  }

  const submit = async () => {
    const trimmed = message.trim()
    if (!trimmed || submitting) return
    const nextUserMessage: ChatMessage = { id: createMessageId(), role: 'user', content: trimmed, timestamp: Date.now() }
    const assistantMessageId = createMessageId()
    const sessionId = activeSessionId || createSessionId()
    const baseSessions = sessions.some((item) => item.id === sessionId) ? sessions : [...sessions, { id: sessionId, title: '新对话', createdAt: Date.now(), updatedAt: Date.now(), messages: [] }]
    const provisionalAssistant: ChatMessage = { id: assistantMessageId, role: 'assistant', content: '', timestamp: Date.now(), streaming: true }
    const nextSessions = baseSessions.map((session) => session.id === sessionId ? { ...session, updatedAt: Date.now(), messages: [...session.messages, nextUserMessage, provisionalAssistant], title: session.title === '新对话' ? buildSessionTitle([nextUserMessage]) : session.title } : session)
    persistSessions(nextSessions, sessionId)
    setMessage('')
    setSubmitting(true)
    setError(null)
    try {
      const sessionMessages = nextSessions.find((item) => item.id === sessionId)?.messages || []
      const history = sessionMessages.filter((item) => item.id !== assistantMessageId).slice(-6).map((item) => ({ role: item.role, content: item.content }))
      const streamEnabled = settingsForm.hermes_stream || parseBoolean(context?.hermes?.stream)
      if (streamEnabled) {
        const body = await api.chatTurnStream({ session_id: sessionId, message: trimmed, history, stream: true })
        if (!body) throw new Error('stream unavailable')
        const reader = body.getReader()
        const decoder = new TextDecoder()
        let buffer = ''
        let finalText = ''
        const upsertAssistant = (content: string, done = false) => {
          persistSessions(nextSessions.map((session) => session.id === sessionId ? { ...session, updatedAt: Date.now(), messages: session.messages.map((item) => item.id === assistantMessageId ? { ...item, content, streaming: !done } : item) } : session), sessionId)
        }
        try {
          while (true) {
            const { value, done } = await reader.read()
            if (done) break
            buffer += decoder.decode(value, { stream: true })
            while (buffer.includes('\n\n')) {
              const idx = buffer.indexOf('\n\n')
              const block = buffer.slice(0, idx)
              buffer = buffer.slice(idx + 2)
              for (const line of block.split(/\r?\n/)) {
                if (!line.startsWith('data:')) continue
                const data = line.slice(5).trim()
                if (!data) continue
                if (data === '[DONE]') { upsertAssistant(finalText, true); continue }
                let event: ChatStreamEvent | null = null
                try { event = JSON.parse(data) as ChatStreamEvent } catch { continue }
                if (event.type === 'delta' && event.content) { finalText += event.content; upsertAssistant(finalText, false) } else if (event.type === 'done') { upsertAssistant(finalText, true) }
              }
            }
          }
        } catch (streamError: any) {
          finalText = finalText || (lang === 'en' ? 'Streaming failed.' : '流式输出失败。')
          upsertAssistant(finalText, true)
          setError(streamError?.message || String(streamError))
        } finally { reader.releaseLock() }
        const response: ChatTurnResponse = { assistant_message: finalText, model: context?.model || settingsForm.hermes_model, transport: 'hermes_http', stream: true, commands: [], warnings: [], runtime_files: { agent: '', skill: '', prompt: '' } }
        persistSessions(nextSessions.map((session) => session.id === sessionId ? { ...session, updatedAt: Date.now(), messages: session.messages.map((item) => item.id === assistantMessageId ? { ...item, content: finalText, response, streaming: false } : item), title: buildSessionTitle(session.messages.map((item) => item.id === assistantMessageId ? { ...item, content: finalText } : item)) } : session), sessionId)
      } else {
        const response = await api.chatTurn({ session_id: sessionId, message: trimmed, history, stream: false })
        if (response.warnings?.length) setError(response.warnings.join(' · '))
        persistSessions(nextSessions.map((session) => session.id === sessionId ? { ...session, updatedAt: Date.now(), messages: session.messages.map((item) => item.id === assistantMessageId ? { ...item, content: response.assistant_message, response, streaming: false } : item), title: buildSessionTitle(session.messages.map((item) => item.id === assistantMessageId ? { ...item, content: response.assistant_message } : item)) } : session), sessionId)
      }
      await api.chatContext().then((next) => {
        setContext(next)
        writeCache(CHAT_CONTEXT_CACHE_KEY, next)
      })
    } catch (e: any) { setError(e.message || String(e)) } finally { setSubmitting(false) }
  }

  const createNewSession = () => {
    const nextId = createSessionId()
    persistSessions([...sessions, { id: nextId, title: lang === 'en' ? 'New chat' : '新对话', createdAt: Date.now(), updatedAt: Date.now(), messages: [] }], nextId)
    setMessage('')
    setError(null)
  }

  const clearSession = async (sessionId: string) => {
    const target = sessions.find((item) => item.id === sessionId)
    if (!target) return
    setDeleteTarget(target)
  }

  const confirmDeleteSession = async (event?: MouseEvent<HTMLButtonElement>) => {
    event?.preventDefault()
    event?.stopPropagation()
    if (!deleteTarget || deleteSaving) return
    const sessionId = deleteTarget.id
    setDeleteSaving(true)
    setError(null)
    try {
      await api.deleteChatSession(sessionId)
      const next = sessions.filter((item) => item.id !== sessionId)
      persistSessions(next, next[0]?.id || '')
      setDeleteTarget(null)
      setMessageExpanded((prev) => {
        const next = { ...prev }
        for (const message of deleteTarget.messages) delete next[message.id]
        return next
      })
    } catch (e: any) {
      setError(e.message || String(e))
    } finally {
      setDeleteSaving(false)
    }
  }

  const onComposerKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && !event.metaKey && !event.ctrlKey && !event.shiftKey) {
      if (!message.trim()) { event.preventDefault(); return }
      event.preventDefault(); void submit()
    }
  }

  const lockedFields = new Set(settingsPayload?.locked_fields || [])
  const connectionStatus = context?.status?.connected ? (lang === 'en' ? 'Connected' : '已连接') : (lang === 'en' ? 'Disconnected' : '未连接')
  const displayModel = context?.model
  const displayEndpoint = context?.hermes?.endpoint
  const sessionCount = sessions.length

  return (
    <>
      <div className="chat-page-minimal">
        <header className="chat-topbar-minimal panel">
          <div className="chat-brand-block">
            <div className="chat-eyebrow">{lang === 'en' ? 'Conversation' : '对话'}</div>
            <h1>{lang === 'en' ? 'Yamibo chat' : 'Yamibo 对话'}</h1>
          </div>
          <div className="chat-topbar-actions">
            <span className={`badge ${context?.status?.connected ? 'badge-ok' : 'badge-error'}`}>{context?.status?.label || (lang === 'en' ? 'Unknown' : '未知')}</span>
            <span className="badge badge-muted">{displayModel || '-'}</span>
            <button className="btn-secondary btn-compact" onClick={() => void openSettings()}>{lang === 'en' ? 'Settings' : '设置'}</button>
          </div>
        </header>

        <section className="chat-rail panel">
          <div className="chat-rail-meta">
            <span>{lang === 'en' ? 'Sessions' : '会话'}</span>
            <div className="chat-rail-actions">
              <strong>{sessionCount}</strong>
              <button className="btn-secondary btn-compact" onClick={createNewSession}>{lang === 'en' ? 'New' : '新建'}</button>
            </div>
          </div>
          <div className="chat-session-strip">
            {sessions.length === 0 ? <div className="chat-strip-empty">{lang === 'en' ? 'No conversations yet' : '暂无对话'}</div> : sessions.slice().sort((a, b) => b.updatedAt - a.updatedAt).map((session) => (
              <div key={session.id} className={`chat-strip-item ${session.id === activeSessionId ? 'active' : ''}`} role="button" tabIndex={0} onClick={() => setActiveSessionId(session.id)} onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') setActiveSessionId(session.id) }}>
                <span className="chat-strip-title">{session.title}</span>
                <span className="chat-strip-time">{formatShortTime(session.updatedAt)}</span>
                <span className="chat-strip-count">{session.messages.length}</span>
                <button type="button" className="chat-strip-x" aria-label={lang === 'en' ? `Delete conversation ${session.title}` : `删除对话 ${session.title}`} onClick={(e) => { e.stopPropagation(); void clearSession(session.id) }}>×</button>
              </div>
            ))}
          </div>
          <div className="chat-rail-meta chat-rail-meta-right">
            <span>{loading ? (lang === 'en' ? 'Loading…' : '加载中…') : connectionStatus}</span>
            <span>{currentTransport?.label || '-'}</span>
          </div>
        </section>

        <main className="chat-stage-minimal panel">
          <div className="chat-stage-header">
            <div>
              <div className="chat-eyebrow">{lang === 'en' ? 'Timeline' : '时间线'}</div>
              <h2>{activeSession?.title || (lang === 'en' ? 'Select or start a conversation' : '选择或创建一个对话')}</h2>
            </div>
            <div className="chat-stage-badges">
              <span className="badge badge-muted">{activeSession ? `${messages.length} ${lang === 'en' ? 'messages' : '条消息'}` : '-'}</span>
              <span className="badge badge-muted">{displayEndpoint || '-'}</span>
            </div>
          </div>

          <section className="chat-transcript-minimal" ref={transcriptRef}>
            {loading ? (
              <div className="chat-loading-state" aria-live="polite" aria-busy="true">
                <div className="chat-loading-spinner" />
                <div className="chat-loading-copy">
                  <strong>{lang === 'en' ? 'Loading conversation…' : '正在加载对话…'}</strong>
                  <span>{lang === 'en' ? 'Restoring sessions and runtime context.' : '正在恢复会话与运行时信息。'}</span>
                </div>
              </div>
            ) : messages.length === 0 ? (
              <div className="chat-empty-state-minimal">
                <strong>{lang === 'en' ? 'Start with a short prompt' : '从一句简短提示开始'}</strong>
                <p>{lang === 'en' ? 'The assistant replies will stay compact, readable, and centered on the content.' : '助手回复会保持紧凑、清晰，并尽量把空间留给内容。'}</p>
              </div>
            ) : messages.map((item, index) => (
              <article key={`${item.role}-${item.timestamp}-${index}`} className={`chat-turn ${item.role === 'user' ? 'is-user' : 'is-assistant'} ${item.role === 'assistant' && !item.streaming && !messageExpanded[item.id] && isLongMessage(item.content) ? 'is-collapsed' : ''}`}>
                <div className="chat-turn-meta">
                  <div className="chat-turn-persona">
                    <strong>{item.role === 'user' ? (lang === 'en' ? 'You' : '你') : (lang === 'en' ? 'Assistant' : '助手')}</strong>
                    <span>{formatShortTime(item.timestamp)}</span>
                  </div>
                  <div className="chat-turn-actions">
                    {item.streaming && <span className="badge badge-muted">{lang === 'en' ? 'Streaming' : '流式输出'}</span>}
                    {item.role === 'assistant' && isLongMessage(item.content) && !item.streaming && (
                      <button className="chat-toggle-btn" onClick={() => setMessageExpanded((prev) => ({ ...prev, [item.id]: !prev[item.id] }))}>
                        {messageExpanded[item.id] ? (lang === 'en' ? 'Collapse' : '折叠') : (lang === 'en' ? 'Expand' : '展开')}
                      </button>
                    )}
                  </div>
                </div>
                <div className={`chat-turn-body ${item.role === 'assistant' && !item.streaming && !messageExpanded[item.id] && isLongMessage(item.content) ? 'is-collapsed' : ''}`}>
                  {item.role === 'assistant' ? <Markdown content={item.content || (item.streaming ? '...' : '')} /> : <p>{item.content}</p>}
                </div>

                {item.response && (
                  <details className="chat-toolbox" open={false}>
                    <summary className="chat-toolbox-summary">
                      <span>{lang === 'en' ? 'Tool details' : '工具详情'}</span>
                      <span className="badge badge-muted">{item.response.commands.length} {lang === 'en' ? 'actions' : '项操作'}</span>
                    </summary>
                    {item.response.warnings.length > 0 && (
                      <div className="chat-toolbox-section">
                        <div className="chat-toolbox-label">{lang === 'en' ? 'Warnings' : '警告'}</div>
                        <div className="chat-warning-list">
                          {item.response.warnings.map((warning) => <div key={warning} className="chat-warning-item">{warning}</div>)}
                        </div>
                      </div>
                    )}
                    <div className="chat-tool-list">
                      {item.response.commands.length === 0 ? <div className="chat-tool-empty">{lang === 'en' ? 'No tool actions were required.' : '没有需要执行的工具动作。'}</div> : item.response.commands.map((command, commandIndex) => (
                        <details key={`${command.command}-${commandIndex}`} className="chat-tool-card" open={false}>
                          <summary className="chat-tool-card-head">
                            <div>
                              <strong>{command.command}</strong>
                              <span>{command.reason || (lang === 'en' ? 'Planned action' : '计划动作')}</span>
                            </div>
                            <span className={`badge ${command.ok ? 'badge-ok' : 'badge-error'}`}>{command.executed ? (command.ok ? (lang === 'en' ? 'done' : '已完成') : (lang === 'en' ? 'failed' : '失败')) : (lang === 'en' ? 'planned' : '计划中')}</span>
                          </summary>
                          {command.invocation && <pre className="chat-code-slab">{command.invocation}</pre>}
                          {command.warning && <div className="chat-tool-warning">{command.warning}</div>}
                          {command.output != null && <pre className="chat-code-slab">{typeof command.output === 'string' ? command.output : JSON.stringify(command.output, null, 2)}</pre>}
                          {command.stderr && <pre className="chat-code-slab chat-code-error">{command.stderr}</pre>}
                        </details>
                      ))}
                    </div>
                  </details>
                )}
              </article>
            ))}
          </section>

          <section className="chat-composer-minimal">
            <textarea value={message} onChange={(event) => setMessage(event.target.value)} onKeyDown={onComposerKeyDown} placeholder={lang === 'en' ? 'Ask Yamibo to do something...' : '让 Yamibo 帮你做点什么...'} rows={4} />
            <div className="chat-composer-bar">
              <span>{lang === 'en' ? 'Enter to send · Ctrl/Cmd+Enter for newline' : 'Enter 发送 · Ctrl/Cmd+Enter 换行'}</span>
              <button className="btn-primary btn-send-minimal" onClick={() => void submit()} disabled={submitting || !message.trim()}>{submitting ? (lang === 'en' ? 'Running…' : '执行中…') : (lang === 'en' ? 'Send' : '发送')}</button>
            </div>
            {error && <div className="chat-error">{error}</div>}
          </section>
        </main>
      </div>

      {settingsOpen && (
        <div className="confirm-overlay" onClick={() => setSettingsOpen(false)}>
          <div className="confirm-dialog chat-settings-dialog-minimal" onClick={(event) => event.stopPropagation()}>
            <div className="chat-settings-head-minimal">
              <div>
                <h3>{lang === 'en' ? 'Chat settings' : '对话设置'}</h3>
                <p>{lang === 'en' ? 'Minimal configuration panel for Hermes.' : '更简洁的 Hermes 配置面板。'}</p>
              </div>
              <button className="btn-secondary btn-compact" onClick={() => setSettingsOpen(false)}>{lang === 'en' ? 'Close' : '关闭'}</button>
            </div>
            {settingsLoading ? <div className="chat-empty-state-minimal">{lang === 'en' ? 'Loading settings...' : '正在加载设置...'}</div> : (
              <div className="chat-settings-form-minimal">
                <label className="chat-settings-field"><span>{lang === 'en' ? 'Hermes endpoint' : 'Hermes 地址'}</span><input type="text" value={settingsForm.hermes_endpoint} disabled={lockedFields.has('hermes_endpoint')} onChange={(event) => setSettingsForm((prev) => ({ ...prev, hermes_endpoint: event.target.value }))} /></label>
                <label className="chat-settings-field"><span>{lang === 'en' ? 'Hermes host' : 'Hermes 主机'}</span><input type="text" value={settingsForm.hermes_host} disabled={lockedFields.has('hermes_host')} onChange={(event) => setSettingsForm((prev) => ({ ...prev, hermes_host: event.target.value }))} /></label>
                <label className="chat-settings-field"><span>{lang === 'en' ? 'Hermes port' : 'Hermes 端口'}</span><input type="number" value={settingsForm.hermes_port} disabled={lockedFields.has('hermes_port')} onChange={(event) => setSettingsForm((prev) => ({ ...prev, hermes_port: event.target.value }))} /></label>
                <label className="chat-settings-field"><span>{lang === 'en' ? 'Hermes API key' : 'Hermes API Key'}</span><input type="password" value={settingsForm.hermes_api_key} disabled={lockedFields.has('hermes_api_key')} onChange={(event) => setSettingsForm((prev) => ({ ...prev, hermes_api_key: event.target.value }))} /></label>
                <label className="chat-settings-field"><span>{lang === 'en' ? 'Hermes model' : 'Hermes 模型'}</span><input type="text" value={settingsForm.hermes_model} disabled={lockedFields.has('hermes_model')} onChange={(event) => setSettingsForm((prev) => ({ ...prev, hermes_model: event.target.value }))} /></label>
                <label className="chat-settings-field chat-settings-field-row">
                  <span>{lang === 'en' ? 'Stream output' : '流式输出'}</span>
                  <input type="checkbox" checked={settingsForm.hermes_stream} disabled={lockedFields.has('hermes_stream')} onChange={(event) => setSettingsForm((prev) => ({ ...prev, hermes_stream: event.target.checked }))} />
                </label>
                <div className="chat-settings-help">{lang === 'en' ? 'Point this page at the Hermes host, for example `http://host.docker.internal:8642`.' : '将此页面指向 Hermes 宿主机，例如 `http://host.docker.internal:8642`。'}</div>
                <div className="chat-settings-actions-minimal">
                  <button className="btn-secondary btn-compact" onClick={() => setSettingsOpen(false)}>{lang === 'en' ? 'Cancel' : '取消'}</button>
                  <button className="btn-secondary btn-compact" onClick={() => void testHermesConnection()} disabled={settingsSaving}>{settingsSaving ? (lang === 'en' ? 'Testing…' : '测试中…') : (lang === 'en' ? 'Test' : '测试')}</button>
                  <button className="btn-primary btn-compact" onClick={() => void saveSettings()} disabled={settingsSaving}>{settingsSaving ? (lang === 'en' ? 'Saving…' : '保存中…') : (lang === 'en' ? 'Save' : '保存')}</button>
                </div>
                {settingsError && <div className="chat-error">{settingsError}</div>}
                {settingsSaved && <div className="chat-settings-saved">{settingsSaved}</div>}
              </div>
            )}
          </div>
        </div>
      )}

      {deleteTarget && (
        <div className="confirm-overlay" onClick={() => setDeleteTarget(null)}>
          <div className="confirm-dialog" onClick={(event) => event.stopPropagation()}>
            <div className="confirm-title">{lang === 'en' ? 'Delete conversation' : '删除对话'}</div>
            <div className="confirm-message">{lang === 'en' ? `Delete “${deleteTarget.title}” permanently? This cannot be undone.` : `确认永久删除“${deleteTarget.title}”？此操作不可恢复。`}</div>
            <div className="confirm-actions">
              <button className="btn-secondary" type="button" onClick={() => setDeleteTarget(null)} disabled={deleteSaving}>{lang === 'en' ? 'Cancel' : '取消'}</button>
              <button className="btn-danger" type="button" onClick={(event) => { void confirmDeleteSession(event) }} disabled={deleteSaving}>{deleteSaving ? (lang === 'en' ? 'Deleting…' : '删除中…') : (lang === 'en' ? 'Delete' : '删除')}</button>
            </div>
          </div>
        </div>
      )}
    </>
  )
}
