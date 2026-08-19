import { useEffect, useState } from 'react'
import { useI18n } from '../context/I18nContext'
import { useChatSessions } from '../hooks/useChatSessions'
import { useChatRunStream } from '../hooks/useChatRunStream'
import { ChatSessionList } from '../components/chat/ChatSessionList'
import { ChatTranscript } from '../components/chat/ChatTranscript'
import type { ChatActivityKind } from '../components/chat/ChatActivityIndicator'
import { ChatComposer } from '../components/chat/ChatComposer'
import { ChatApprovalDialog } from '../components/chat/ChatApprovalDialog'
import '../styles/chat.css'

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
  const { t } = useI18n()
  const sessions = useChatSessions()
  const run = useChatRunStream(sessions.selected, sessions.refreshMessages)
  const [pendingUser, setPendingUser] = useState<string>()
  const [editingTitle, setEditingTitle] = useState(false)
  const [titleDraft, setTitleDraft] = useState('')
  const [sessionMenuOpen, setSessionMenuOpen] = useState(false)
  const ready = !!sessions.context?.ready
  const degraded = !!sessions.context?.degraded
  const streamingEnabled = sessions.context?.streaming_enabled !== false
  const selectedSession = sessions.sessions.find(x => x.id === sessions.selected)
  useEffect(() => { setPendingUser(undefined); setEditingTitle(false); setTitleDraft(''); setSessionMenuOpen(false) }, [sessions.selected])
  const startRename = () => { if (!selectedSession) return; setSessionMenuOpen(false); setTitleDraft(selectedSession.title); setEditingTitle(true) }
  const submitRename = async () => { const value = titleDraft.trim(); if (!selectedSession || !value) return; await sessions.rename(selectedSession.id, value); setEditingTitle(false) }
  const deleteSelected = async () => {
    if (!selectedSession || run.activeSessionIds.has(selectedSession.id) || selectedSession.active_run_id) return
    setSessionMenuOpen(false)
    if (!window.confirm(t('chat_delete_confirm'))) return
    await sessions.remove(selectedSession.id)
  }
  const send = async () => {
    const input = sessions.currentDraft.trim()
    if (!input || !sessions.selected || !ready || run.active) return
    setPendingUser(input)
    try { await run.start(input); sessions.setDraft('') } catch { /* keep the immediate user message and draft for retry */ }
  }
  const statusText = liveStatus(run, streamingEnabled, t)
  const activity = liveActivity(run, streamingEnabled, t)
  return <main className="chat-page">
    <ChatSessionList sessions={sessions.sessions} selected={sessions.selected} onSelect={sessions.select} onCreate={() => void sessions.create()} onLoadMore={sessions.loadMore} hasMore={sessions.hasMore} loading={sessions.loading} busy={!!sessions.operation} />
    <section className="chat-workspace">
      <header><div className="chat-title-block"><span className="eyebrow">{t('chat_eyebrow')}</span>{editingTitle ? <div className="chat-title-edit"><input autoFocus value={titleDraft} onChange={e => setTitleDraft(e.target.value)} onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); void submitRename() }; if (e.key === 'Escape') setEditingTitle(false) }} /><button className="button button-primary" disabled={!titleDraft.trim() || !!sessions.operation} onClick={() => void submitRename()}>{t('save')}</button><button className="button" disabled={!!sessions.operation} onClick={() => setEditingTitle(false)}>{t('cancel')}</button></div> : <h1>{selectedSession?.title || t('chat_title')}</h1>}</div><div className="chat-header-tools"><div className={`chat-status-pill ${ready ? 'ready' : ''} ${degraded ? 'degraded' : ''}`}><span className="chat-status-dot" aria-hidden="true" />{ready ? degraded ? t('chat_degraded') : t('chat_ready') : t('chat_not_configured')}</div>{selectedSession && !editingTitle && <div className="chat-session-menu"><button type="button" className="chat-session-menu-trigger" aria-label={t('chat_session_actions')} title={t('chat_session_actions')} aria-haspopup="menu" aria-expanded={sessionMenuOpen} onClick={() => setSessionMenuOpen(value => !value)}>⚙</button>{sessionMenuOpen && <div className="chat-session-menu-popover" role="menu"><button type="button" role="menuitem" disabled={!!sessions.operation} onClick={startRename}><span aria-hidden="true">✎</span>{t('chat_rename')}</button><button type="button" role="menuitem" className="chat-action-danger" disabled={!!sessions.operation || run.activeSessionIds.has(selectedSession.id) || !!selectedSession.active_run_id} onClick={() => void deleteSelected()}><span aria-hidden="true">⌫</span>{t('chat_delete')}</button></div>}</div>}</div></header>
      {!ready && <div className="chat-notice">{t('chat_unavailable_notice')} <a href="/settings">{t('chat_configure_settings')}</a></div>}
      {ready && degraded && <div className="chat-notice">{t('chat_degraded_notice')}</div>}
      {sessions.error && <div className="chat-error">{sessions.error}</div>}
      {sessions.operationError && <div className="chat-error">{sessions.operationError}</div>}
      {run.error && <div className="chat-error">{run.error}</div>}
      {statusText && <div className={`chat-live-status ${run.connected ? 'connected' : ''}`} role="status" aria-live="polite"><span className="chat-live-dot" aria-hidden="true" /><span className="chat-live-message">{statusText}</span><span className="chat-live-meta">{run.connected ? t('chat_live_connected') : t('chat_status_reconnecting')} · {t('chat_live_event_count', { count: run.state.events.length })} · {t('chat_live_elapsed', { seconds: run.elapsedSeconds })}</span></div>}
      <ChatTranscript messages={sessions.messages} pendingUser={pendingUser} events={run.state.events} assistant={run.state.assistant} streamingEnabled={streamingEnabled} streaming={run.active && streamingEnabled} activity={activity} />
      {run.status === 'waiting_for_approval' && <ChatApprovalDialog events={run.state.events} onChoose={run.approve} error={run.error} />}
      <ChatComposer value={sessions.currentDraft} onChange={sessions.setDraft} onSend={() => void send()} onStop={() => void run.stop()} disabled={!ready || !sessions.selected || run.status === 'waiting_for_approval'} active={run.active} />
    </section>
  </main>
}
