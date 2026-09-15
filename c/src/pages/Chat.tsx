import { useEffect, useRef, useState } from 'react'
import { useTheme } from '../context/ThemeContext'
import { useI18n } from '../context/I18nContext'
import { useChatSessions } from '../hooks/useChatSessions'
import { useChatRunStream } from '../hooks/useChatRunStream'
import { ChatSessionList } from '../components/chat/ChatSessionList'
import { ChatTranscript } from '../components/chat/ChatTranscript'
import type { ChatActivityKind } from '../components/chat/ChatActivityIndicator'
import { ChatComposer } from '../components/chat/ChatComposer'
import { ChatApprovalDialog } from '../components/chat/ChatApprovalDialog'
import '../styles/chat.css'
import { api } from '../api/client'
import { isTerminalStatus } from '../types/chat'
import { ChatLocalPanel, type WorkspaceRun } from '../components/chat/ChatLocalPanel'

function liveStatus(run: ReturnType<typeof useChatRunStream>, streamingEnabled: boolean, t: (key: string, params?: Record<string, string | number>) => string) {
  if (!run.active) return null
  if (run.status === 'waiting_for_approval') return t('chat_status_waiting_approval')
  if (run.status === 'stopping') return t('chat_status_stopping')
  if (run.status === 'reconciling') return t('chat_status_reconciling')
  if (!run.connected) return t('chat_status_reconnecting')
  if (!streamingEnabled) return t('chat_status_background_wait', { seconds: run.elapsedSeconds })
  if (run.secondsSinceEvent !== null && run.secondsSinceEvent >= 5) return t('chat_status_idle_wait', { seconds: run.secondsSinceEvent })
  return run.state.assistant ? t('chat_status_streaming') : t('chat_status_connected_wait', { seconds: run.elapsedSeconds })
}

function liveActivity(run: ReturnType<typeof useChatRunStream>, streamingEnabled: boolean, t: (key: string, params?: Record<string, string | number>) => string): { kind: ChatActivityKind; label: string; detail?: string } | undefined {
  if (!run.active || run.status === 'waiting_for_approval') return undefined
  if (run.status === 'stopping') return { kind: 'stopping', label: t('chat_activity_stopping') }
  if (run.status === 'reconciling') return { kind: 'reconciling', label: t('chat_activity_reconciling') }
  if (!run.connected) return { kind: 'connecting', label: t('chat_activity_connecting') }
  if (!streamingEnabled) return { kind: 'thinking', label: t('chat_activity_background'), detail: t('chat_live_elapsed', { seconds: run.elapsedSeconds }) }

  const recent = [...run.state.events].reverse().find(event => event.type === 'tool.started' || event.type === 'tool.completed' || event.type === 'reasoning.available')
  if (recent?.type === 'tool.started') return { kind: 'tool', label: t('chat_activity_tool'), detail: recent.tool }
  if (recent?.type === 'tool.completed') return { kind: 'thinking', label: t('chat_activity_tool_done'), detail: recent.tool }
  if (recent?.type === 'reasoning.available') return { kind: 'thinking', label: t('chat_activity_thinking') }
  if (run.state.assistant) return { kind: 'reply', label: t('chat_activity_reply') }
  return { kind: 'thinking', label: t('chat_activity_thinking') }
}

export function Chat() {
  const pageRef = useRef<HTMLElement>(null)
  useEffect(() => {
    const resize = () => {
      const el = pageRef.current
      if (el) el.style.setProperty('--chat-height', `${Math.max(320, (window.visualViewport?.height || window.innerHeight) - el.getBoundingClientRect().top - 12)}px`)
    }
    const observer = new ResizeObserver(resize)
    const topbar = document.querySelector('.topbar')
    if (topbar) observer.observe(topbar)
    window.addEventListener('resize', resize)
    window.visualViewport?.addEventListener('resize', resize)
    resize()
    return () => { observer.disconnect(); window.removeEventListener('resize', resize); window.visualViewport?.removeEventListener('resize', resize) }
  }, [])
  const { dark, toggleDark } = useTheme()
  const { t } = useI18n()
  const sessions = useChatSessions()
  const selectedRef = useRef(sessions.selected); selectedRef.current = sessions.selected
  const run = useChatRunStream(sessions.selected, sessions.refreshMessages)
  const [panelOpen, setPanelOpen] = useState(false)
  const [sessionsOpen, setSessionsOpen] = useState(false)
  const [localRuns, setLocalRuns] = useState<WorkspaceRun[]>([])
  const [sending, setSending] = useState(false)
  const sendingRef = useRef(false)
  const [sendError, setSendError] = useState('')
  const embedded = sessions.context?.mode === 'embedded'
  const activeLocalRun = localRuns.find(r => r.session_id === sessions.selected && !isTerminalStatus(r.status) && r.status !== 'queued') || localRuns.find(r => r.session_id === sessions.selected && r.status === 'queued')
  const active = run.active || !!activeLocalRun
  useEffect(() => {
    if (embedded && activeLocalRun && !run.active) run.followRun(activeLocalRun.run_id, activeLocalRun.status)
  }, [embedded, activeLocalRun?.run_id, activeLocalRun?.status, run.active, run.followRun])
  const [pendingUser, setPendingUser] = useState<string>()
  const [editingTitle, setEditingTitle] = useState(false)
  const [titleDraft, setTitleDraft] = useState('')
  const [sessionMenuOpen, setSessionMenuOpen] = useState(false)
  const ready = !!sessions.context?.ready
  const degraded = !!sessions.context?.degraded
  const streamingEnabled = sessions.context?.streaming_enabled !== false
  const selectedSession = sessions.sessions.find(x => x.id === sessions.selected)
  const selectedMessageCount = Math.max(selectedSession?.message_count || 0, sessions.messages.length)
  useEffect(() => { setPendingUser(undefined); setEditingTitle(false); setTitleDraft(''); setSessionMenuOpen(false); setLocalRuns([]); setSendError('') }, [sessions.selected])
  const startRename = () => { if (!selectedSession) return; setSessionMenuOpen(false); setTitleDraft(selectedSession.title); setEditingTitle(true) }
  const submitRename = async () => { const value = titleDraft.trim(); if (!selectedSession || !value) return; await sessions.rename(selectedSession.id, value); setEditingTitle(false) }
  const deleteSelected = async () => {
    if (!selectedSession || active || run.activeSessionIds.has(selectedSession.id) || selectedSession.active_run_id) return
    setSessionMenuOpen(false)
    if (!window.confirm(t('chat_delete_confirm'))) return
    await sessions.remove(selectedSession.id)
  }
  const send = async () => {
    const input = sessions.currentDraft.trim()
    if (!input || !sessions.selected || !ready || sendingRef.current || (active && !embedded)) return
    const target = sessions.selected
    sendingRef.current = true; setSending(true); setSendError('')
    try {
      if (active && embedded) {
        const key = 'yamibo.chat.queue.' + target
        let saved: { input: string; id: string } | null = null
        try { saved = JSON.parse(sessionStorage.getItem(key) || 'null') } catch { /* optional storage */ }
        const request = saved?.input === input ? saved : { input, id: crypto.randomUUID() }
        sessionStorage.setItem(key, JSON.stringify(request))
        await api.startChatRun(target, input, request.id)
        sessionStorage.removeItem(key)
        sessions.setDraft('', target)
        const queued = await api.chatRuns(target)
        if (selectedRef.current === target) { setLocalRuns(queued.runs); setPanelOpen(true) }
      } else {
        await run.start(input)
        if (selectedRef.current === target) setPendingUser(input)
      }
      sessions.setDraft('', target)
    } catch (e) { setSendError((e as Error).message) }
    finally { sendingRef.current = false; setSending(false) }
  }
  const stop = async () => {
    try { if (activeLocalRun) await api.stopChatRun(activeLocalRun.run_id); else await run.stop() }
    catch (e) { setSendError((e as Error).message) }
  }
  const statusText = liveStatus(run, streamingEnabled, t)
  const activity = liveActivity(run, streamingEnabled, t)
  return <main ref={pageRef} className={`chat-page ${panelOpen && embedded ? 'with-panel' : ''} ${sessionsOpen ? 'sessions-open' : ''}`}>
    <button className="chat-mobile-back" aria-label="返回对话" onClick={() => setSessionsOpen(false)}>返回对话</button>
    <ChatSessionList
      sessions={sessions.sessions}
      selected={sessions.selected}
      onSelect={id => { void sessions.select(id); setSessionsOpen(false) }}
      onCreate={() => { void sessions.create().then(() => setSessionsOpen(false)).catch(() => {}) }}
      onLoadMore={sessions.loadMore}
      hasMore={sessions.hasMore}
      loading={sessions.loading}
      busy={!!sessions.operation}
    />
    <section className="chat-workspace">
      <header>
        <button className="button chat-mobile-sessions" aria-label="打开会话列表" onClick={() => setSessionsOpen(true)}>会话</button>
        <div className="chat-title-block">
          <span className="eyebrow">{t('chat_eyebrow')}</span>
          {editingTitle ? <div className="chat-title-edit">
            <input autoFocus value={titleDraft} onChange={e => setTitleDraft(e.target.value)} onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); void submitRename() }; if (e.key === 'Escape') setEditingTitle(false) }} />
            <button className="button button-primary" disabled={!titleDraft.trim() || !!sessions.operation} onClick={() => void submitRename()}>{t('save')}</button>
            <button className="button" disabled={!!sessions.operation} onClick={() => setEditingTitle(false)}>{t('cancel')}</button>
          </div> : <h1>{selectedSession?.title || t('chat_title')}</h1>}
          {selectedSession && !editingTitle && <span className="chat-title-meta">{t('chat_messages_count', { count: selectedMessageCount })}</span>}
        </div>
        <div className="chat-header-tools">
          <button className="chat-session-menu-trigger" aria-label={dark ? '切换浅色主题' : '切换深色主题'} onClick={toggleDark}>{dark ? '☀' : '☾'}</button>
          {embedded && <button className="button chat-panel-toggle" aria-expanded={panelOpen} onClick={() => setPanelOpen(!panelOpen)} aria-label={panelOpen ? '关闭工作面板' : '打开工作面板'}>面板{localRuns.filter(r => !isTerminalStatus(r.status)).length > 0 && <span className="chat-count">{localRuns.filter(r => !isTerminalStatus(r.status)).length}</span>}</button>}
          <div className={`chat-status-pill ${ready ? 'ready' : ''} ${degraded ? 'degraded' : ''}`}><span className="chat-status-dot" aria-hidden="true" />{ready ? degraded ? t('chat_degraded') : t('chat_ready') : t('chat_not_configured')}</div>
          {selectedSession && !editingTitle && <div className="chat-session-menu">
            <button type="button" className="chat-session-menu-trigger" aria-label={t('chat_session_actions')} title={t('chat_session_actions')} aria-haspopup="menu" aria-expanded={sessionMenuOpen} onClick={() => setSessionMenuOpen(value => !value)}>⚙</button>
            {sessionMenuOpen && <div className="chat-session-menu-popover" role="menu">
              <button type="button" role="menuitem" disabled={!!sessions.operation} onClick={startRename}><span aria-hidden="true">✎</span>{t('chat_rename')}</button>
              <button type="button" role="menuitem" className="chat-action-danger" disabled={!!sessions.operation || active || run.activeSessionIds.has(selectedSession.id) || !!selectedSession.active_run_id} onClick={() => void deleteSelected()}><span aria-hidden="true">⌫</span>{t('chat_delete')}</button>
            </div>}
          </div>}
        </div>
      </header>
      {!ready && <div className="chat-notice chat-notice-compact"><span>{t('chat_unavailable_notice')}</span> <a href="/settings">{t('chat_configure_settings')}</a></div>}
      {ready && degraded && <div className="chat-notice">{t('chat_degraded_notice')}</div>}
      {sessions.error && <div className="chat-error">{sessions.error}</div>}
      {sessions.operationError && <div className="chat-error">{sessions.operationError}</div>}
      {sendError && <div className="chat-error" role="alert">{sendError}</div>}
      {run.error && <div className="chat-error">{run.error}</div>}
      {statusText && <div className={`chat-live-status ${run.connected ? 'connected' : ''}`} role="status" aria-live="polite"><span className="chat-live-dot" aria-hidden="true" /><span className="chat-live-message">{statusText}</span><span className="chat-live-meta">{run.connected ? t('chat_live_connected') : t('chat_status_reconnecting')} · {t('chat_live_event_count', { count: run.state.events.length })} · {t('chat_live_elapsed', { seconds: run.elapsedSeconds })}</span></div>}
      {sessions.messages.length === 0 && !pendingUser && !active ? <div className="chat-welcome"><span className="chat-welcome-mark" aria-hidden="true">Y</span><span className="eyebrow">YAMIBO ASSISTANT</span><h2>从一条线索，开始整理。</h2><p>查找论坛内容、归档帖子、查看任务进度。</p>{sessions.selected ? <div className="chat-starters">{[['查找内容', '搜索论坛中关于星灵感应的帖子'], ['整理资料', '整理已归档内容中的相关资料'], ['查看进展', '查看最近创建的归档任务进度']].map(([title, prompt]) => <button key={title} onClick={() => sessions.setDraft(prompt)}><strong>{title}</strong><span>{prompt}</span><b aria-hidden="true">↗</b></button>)}</div> : <button className="button button-primary" disabled={!!sessions.operation || !ready} onClick={() => void sessions.create().catch(() => {})}>开始新会话</button>}</div> : <ChatTranscript messages={sessions.messages} pendingUser={pendingUser} events={run.state.events} assistant={run.state.assistant} streamingEnabled={streamingEnabled} streaming={run.active && streamingEnabled} activity={activity} />}
      {run.status === 'waiting_for_approval' && <ChatApprovalDialog events={run.state.events} onChoose={run.approve} error={run.error} />}
      <ChatComposer value={sessions.currentDraft} onChange={sessions.setDraft} onSend={() => void send()} onStop={() => void stop()} disabled={!ready || !sessions.selected} active={active} queueEnabled={embedded} sending={sending} />
    </section>
    {embedded && sessions.selected && <ChatLocalPanel key={sessions.selected} sessionId={sessions.selected} visible={panelOpen} onClose={() => setPanelOpen(false)} onRuns={setLocalRuns} onFinished={() => void sessions.refreshMessages()} />}
  </main>
}
