import { useEffect, useRef, useState } from 'react'
import { useTheme } from '../context/ThemeContext'
import { useI18n } from '../context/I18nContext'
import { useChatSessions } from '../hooks/useChatSessions'
import { useChatRunStream } from '../hooks/useChatRunStream'
import { ChatSessionList } from '../components/chat/ChatSessionList'
import { ChatTranscript } from '../components/chat/ChatTranscript'
import { preparationTools, toolLabel } from '../components/chat/toolTimeline'
import type { ChatActivityKind } from '../components/chat/ChatActivityIndicator'
import { ChatComposer } from '../components/chat/ChatComposer'
import { ChatApprovalDialog } from '../components/chat/ChatApprovalDialog'
import '../styles/chat.css'
import { api } from '../api/client'
import { isTerminalStatus, type ChatRunScope, type ChatSubmissionStatus } from '../types/chat'
import { ChatLocalPanel, type WorkspaceRun } from '../components/chat/ChatLocalPanel'
import { Settings } from './Settings'
import { useLocation } from 'react-router-dom'

function liveStatus(run: ReturnType<typeof useChatRunStream>, streamingEnabled: boolean, t: (key: string, params?: Record<string, string | number>) => string, tx: (zh: string, en: string) => string) {
  if (!run.active) return null
  if (run.status === 'waiting_for_approval') return t('chat_status_waiting_approval')
  if (run.status === 'stopping') return t('chat_status_stopping')
  if (run.status === 'reconciling') return t('chat_status_reconciling')
  if (run.status === 'waiting_jobs') return tx('等待后台任务', 'Waiting for background jobs')
  if (run.status === 'queued') return tx('排队中', 'Queued')
  const tool = [...run.state.events].reverse().find(event => event.type === 'tool.started')
  if (tool?.type === 'tool.started' && tool.tool === 'find_discussions') return tx('正在查找讨论', 'Finding discussions')
  if (tool?.type === 'tool.started' && tool.tool === 'read_discussion_source') return tx('正在阅读来源', 'Reading sources')
  if (!streamingEnabled) return t('chat_status_background_wait', { seconds: run.elapsedSeconds })
  if (run.secondsSinceEvent !== null && run.secondsSinceEvent >= 5) return t('chat_status_idle_wait', { seconds: run.secondsSinceEvent })
  return tx('处理中', 'Running')
}

function chatScopeFromLocation(search: string): ChatRunScope {
  const params = new URLSearchParams(search)
  const scope: ChatRunScope = {}
  const mode = params.get('mode')
  if (mode === 'discovery' || mode === 'selected') scope.mode = mode
  for (const [key, field] of [['forum_ids', 'forum_ids'], ['tids', 'tids'], ['pids', 'pids']] as const) {
    const values = params.get(key)?.split(',').map(value => Number(value.trim()))
    if (values?.length && values.every(value => Number.isSafeInteger(value) && value > 0)) scope[field] = [...new Set(values)]
  }
  const startAt = params.get('start_at'); if (startAt) scope.start_at = startAt
  const endAt = params.get('end_at'); if (endAt) scope.end_at = endAt
  const revision = params.get('report_revision'); if (revision) scope.report_revision = revision
  if (!scope.mode && scope.tids?.length) scope.mode = 'selected'
  return scope
}

type PendingSubmission = { input: string; id: string; scope: ChatRunScope; status: ChatSubmissionStatus; runId?: string; existingCount: number }
function submissionStatus(item: PendingSubmission, run: ReturnType<typeof useChatRunStream>): ChatSubmissionStatus {
  if (item.status === 'failed' || item.status === 'submitting' || !item.runId || run.runId !== item.runId) return item.status
  if (run.status === 'queued') return 'queued'
  if (run.status === 'waiting_jobs') return 'waiting_jobs'
  if (run.status === 'waiting_for_approval') return 'approval'
  if (run.status === 'completed') return 'completed'
  if (['failed', 'cancelled', 'limited', 'interrupted'].includes(run.status)) return 'failed'
  const tool = [...run.state.events].reverse().find(event => event.type === 'tool.started')
  if (tool?.type === 'tool.started' && tool.tool === 'find_discussions') return 'research_find'
  if (tool?.type === 'tool.started' && tool.tool === 'read_discussion_source') return 'research_read'
  return 'running'
}

function liveActivity(run: ReturnType<typeof useChatRunStream>, streamingEnabled: boolean, t: (key: string, params?: Record<string, string | number>) => string, tx: (zh: string, en: string) => string): { kind: ChatActivityKind; label: string; detail?: string } | undefined {
  if (!run.active || run.status === 'waiting_for_approval') return undefined
  if (run.status === 'stopping') return { kind: 'stopping', label: t('chat_activity_stopping') }
  if (run.status === 'reconciling') return { kind: 'reconciling', label: t('chat_activity_reconciling') }
  if (!run.connected) return { kind: 'connecting', label: t('chat_activity_connecting') }
  if (!streamingEnabled) return { kind: 'thinking', label: t('chat_activity_background'), detail: t('chat_live_elapsed', { seconds: run.elapsedSeconds }) }

  const recent = [...run.state.events].reverse().find(event => event.type === 'tool.started' || event.type === 'tool.completed' || event.type === 'reasoning.available')
  if (recent?.type === 'tool.started') return { kind: 'tool', label: t('chat_activity_tool'), detail: preparationTools.has(recent.tool) ? tx('准备工具与资料', 'Preparing tools and context') : toolLabel(recent.tool, tx) }
  if (recent?.type === 'tool.completed') return { kind: 'thinking', label: t('chat_activity_tool_done'), detail: preparationTools.has(recent.tool) ? tx('准备工具与资料', 'Preparing tools and context') : toolLabel(recent.tool, tx) }
  if (recent?.type === 'reasoning.available') return { kind: 'thinking', label: t('chat_activity_thinking') }
  if (run.state.assistant) return { kind: 'reply', label: t('chat_activity_reply') }
  return { kind: 'thinking', label: t('chat_activity_thinking') }
}

export function Chat() {
  const location = useLocation()
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
  const { t, lang, tx } = useI18n()
  const sessions = useChatSessions(new URLSearchParams(location.search).get('session_id') || '')
  const selectedRef = useRef(sessions.selected); selectedRef.current = sessions.selected
  const run = useChatRunStream(sessions.selected, sessions.refreshMessages, lang)
  const [panelOpen, setPanelOpen] = useState(false)
  const [sessionsOpen, setSessionsOpen] = useState(false)
  const [modelSettingsOpen, setModelSettingsOpen] = useState(false)
  const [modelSettingsDirty, setModelSettingsDirty] = useState(false)
  const modelSettingsDialog = useRef<HTMLDialogElement>(null)
  const [localRuns, setLocalRuns] = useState<WorkspaceRun[]>([])
  const [sending, setSending] = useState(false)
  const sendingRef = useRef(false)
  const [sendError, setSendError] = useState('')
  const chatScope = chatScopeFromLocation(location.search)
  const embedded = sessions.context?.mode === 'embedded'
  const activeLocalRun = localRuns.find(r => r.session_id === sessions.selected && !isTerminalStatus(r.status) && r.status !== 'queued') || localRuns.find(r => r.session_id === sessions.selected && r.status === 'queued')
  const active = run.active || !!activeLocalRun
  useEffect(() => {
    if (embedded && activeLocalRun && !run.active) run.followRun(activeLocalRun.run_id, activeLocalRun.status)
  }, [embedded, activeLocalRun?.run_id, activeLocalRun?.status, run.active, run.followRun])
  const [pending, setPending] = useState<PendingSubmission[]>([])
  const [editingTitle, setEditingTitle] = useState(false)
  const [titleDraft, setTitleDraft] = useState('')
  const [sessionMenuOpen, setSessionMenuOpen] = useState(false)
  const ready = !!sessions.context?.ready
  const degraded = !!sessions.context?.degraded
  const streamingEnabled = sessions.context?.streaming_enabled !== false
  const selectedSession = sessions.sessions.find(x => x.id === sessions.selected)
  const selectedMessageCount = Math.max(selectedSession?.message_count || 0, sessions.messages.length)
  useEffect(() => {
    const dialog = modelSettingsDialog.current
    if (!modelSettingsOpen || !dialog) return
    dialog.showModal()
    return () => { if (dialog.open) dialog.close() }
  }, [modelSettingsOpen])
  const openModelSettings = () => { setModelSettingsDirty(false); setModelSettingsOpen(true) }
  const closeModelSettings = () => {
    if (modelSettingsDirty && !window.confirm(t('settings_discard_prompt'))) return
    setModelSettingsOpen(false)
  }
  useEffect(() => { setPending([]); setEditingTitle(false); setTitleDraft(''); setSessionMenuOpen(false); setLocalRuns([]); setSendError('') }, [sessions.selected])
  const startRename = () => { if (!selectedSession) return; setSessionMenuOpen(false); setTitleDraft(selectedSession.title); setEditingTitle(true) }
  const submitRename = async () => { const value = titleDraft.trim(); if (!selectedSession || !value) return; await sessions.rename(selectedSession.id, value); setEditingTitle(false) }
  const deleteSelected = async () => {
    if (!selectedSession || active || run.activeSessionIds.has(selectedSession.id) || selectedSession.active_run_id) return
    setSessionMenuOpen(false)
    if (!window.confirm(t('chat_delete_confirm'))) return
    await sessions.remove(selectedSession.id)
  }
  const send = async (retry?: PendingSubmission) => {
    const input = (retry?.input || sessions.currentDraft).trim()
    if (!input || !sessions.selected || !ready || sendingRef.current || (active && !embedded)) return
    const target = sessions.selected
    const existingCount = sessions.messages.filter(message => message.role === 'user' && typeof message.content === 'string' && message.content === input).length
    const request = retry || { input, id: crypto.randomUUID(), scope: chatScope, status: 'submitting' as const, existingCount }
    const submitting = { ...request, status: 'submitting' as const }
    setPending(previous => retry ? previous.map(item => item.id === retry.id ? submitting : item) : [...previous, submitting])
    sendingRef.current = true; setSending(true); setSendError('')
    try {
      if (active && embedded) {
        const accepted = await api.startChatRun(target, { input, client_request_id: request.id, ...request.scope })
        setPending(previous => previous.map(item => item.id === request.id ? { ...request, status: accepted.status === 'queued' ? 'queued' : 'accepted', runId: accepted.run_id } : item))
        sessions.setDraft('', target)
        const queued = await api.chatRuns(target)
        if (selectedRef.current === target) { setLocalRuns(queued.runs); setPanelOpen(true) }
      } else {
        const accepted = await run.start(input, request.scope, request.id)
        setPending(previous => previous.map(item => item.id === request.id ? { ...request, status: accepted.status === 'queued' ? 'queued' : 'accepted', runId: accepted.run_id } : item))
        sessions.setDraft('', target)
      }
    } catch (e) { setPending(previous => previous.map(item => item.id === request.id ? { ...request, status: 'failed' } : item)); setSendError((e as Error).message) }
    finally { sendingRef.current = false; setSending(false) }
  }
  const pendingTranscript = pending.map(item => ({ id: item.id, input: item.input, existingCount: item.existingCount, status: submissionStatus(item, run), onRetry: item.status === 'failed' && !item.runId ? () => void send(item) : undefined }))
  const stop = async () => {
    try { if (activeLocalRun) await api.stopChatRun(activeLocalRun.run_id); else await run.stop() }
    catch (e) { setSendError((e as Error).message) }
  }
  const emptyConversation = sessions.messages.length === 0 && pending.length === 0 && !active
  const unavailableReason = [...new Set([sessions.context?.error?.message, sessions.error].filter(Boolean))].join(' · ')
  const unavailableDetails = unavailableReason ? <details className="chat-unavailable-details"><summary>{tx('查看连接详情', 'Connection details')}</summary><p>{unavailableReason}</p></details> : null
  const statusText = liveStatus(run, streamingEnabled, t, tx)
  const activity = liveActivity(run, streamingEnabled, t, tx)
  return <main ref={pageRef} className={`chat-page ${panelOpen && embedded ? 'with-panel' : ''} ${sessionsOpen ? 'sessions-open' : ''}`}>
    <button className="chat-mobile-back" aria-label={tx('返回对话', 'Back to chat')} onClick={() => setSessionsOpen(false)}>{tx('返回对话', 'Back to chat')}</button>
    <ChatSessionList
      sessions={sessions.sessions}
      selected={sessions.selected}
      onSelect={id => { void sessions.select(id); setSessionsOpen(false) }}
      onCreate={() => { if (!ready) return; void sessions.create().then(() => setSessionsOpen(false)).catch(() => {}) }}
      onLoadMore={sessions.loadMore}
      hasMore={sessions.hasMore}
      loading={sessions.loading}
      createDisabled={!ready}
      busy={!!sessions.operation}
    />
    <section className="chat-workspace">
      <header>
        <button className="button chat-mobile-sessions" aria-label={tx('打开会话列表', 'Open session list')} onClick={() => setSessionsOpen(true)}>{t('chat_sessions')}</button>
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
          <button type="button" className="button chat-model-settings" onClick={openModelSettings}>{tx('模型配置', 'Model settings')}</button>
          <button className="chat-session-menu-trigger" aria-label={dark ? tx('切换浅色主题', 'Switch to light theme') : tx('切换深色主题', 'Switch to dark theme')} onClick={toggleDark}>{dark ? '☀' : '☾'}</button>
          {embedded && <button className="button chat-panel-toggle" aria-expanded={panelOpen} onClick={() => setPanelOpen(!panelOpen)} aria-label={panelOpen ? tx('关闭工作面板', 'Close workspace panel') : tx('打开工作面板', 'Open workspace panel')}>{tx('面板', 'Panel')}{localRuns.filter(r => !isTerminalStatus(r.status)).length > 0 && <span className="chat-count">{localRuns.filter(r => !isTerminalStatus(r.status)).length}</span>}</button>}
          <div className={`chat-status-pill ${ready ? 'ready' : ''} ${degraded ? 'degraded' : ''}`}><span className="chat-status-dot" aria-hidden="true" />{ready ? degraded ? t('chat_degraded') : t('chat_ready') : t('chat_not_configured')}</div>
          {selectedSession && !editingTitle && <div className="chat-session-menu">
            <button type="button" className="chat-session-menu-trigger" aria-label={t('chat_session_actions')} title={t('chat_session_actions')} aria-haspopup="menu" aria-expanded={sessionMenuOpen} onClick={() => setSessionMenuOpen(value => !value)}>⋯</button>
            {sessionMenuOpen && <div className="chat-session-menu-popover" role="menu">
              <button type="button" role="menuitem" disabled={!!sessions.operation} onClick={startRename}><span aria-hidden="true">✎</span>{t('chat_rename')}</button>
              <button type="button" role="menuitem" className="chat-action-danger" disabled={!!sessions.operation || active || run.activeSessionIds.has(selectedSession.id) || !!selectedSession.active_run_id} onClick={() => void deleteSelected()}><span aria-hidden="true">⌫</span>{t('chat_delete')}</button>
            </div>}
          </div>}
        </div>
      </header>
      {modelSettingsOpen && <dialog ref={modelSettingsDialog} className="chat-model-settings-dialog" aria-labelledby="chat-model-settings-title"
        onCancel={event => { event.preventDefault(); closeModelSettings() }}
        onClick={event => { if (event.target === event.currentTarget) closeModelSettings() }}>
        <header className="chat-model-settings-dialog-head"><h2 id="chat-model-settings-title">{tx('模型配置', 'Model settings')}</h2><button type="button" className="chat-model-settings-close" aria-label={t('close')} onClick={closeModelSettings}>×</button></header>
        <div className="chat-model-settings-dialog-body"><Settings sectionOnly="llm" onDirtyChange={setModelSettingsDirty} onSaved={() => { void sessions.refreshContext() }} /></div>
      </dialog>}
      {!ready && !emptyConversation && <div className="chat-notice chat-notice-compact" role="status"><span>{sessions.loading ? tx('正在连接服务…', 'Connecting to service…') : tx('服务暂不可用，已有消息可继续查看。', 'The service is unavailable. Existing messages remain available.')}</span> <button type="button" className="chat-model-settings-inline" onClick={openModelSettings}>{tx('配置模型', 'Configure model')}</button>{unavailableDetails}</div>}
      {ready && degraded && <div className="chat-notice">{t('chat_degraded_notice')}</div>}
      {ready && sessions.error && <div className="chat-error">{sessions.error}</div>}
      {sessions.operationError && <div className="chat-error">{sessions.operationError}</div>}
      {sendError && <div className="chat-error" role="alert">{sendError}</div>}
      {run.error && <div className="chat-error">{run.error}</div>}
      {statusText && <div className={`chat-live-status ${run.connected ? 'connected' : ''}`} role="status" aria-live="polite"><span className="chat-live-dot" aria-hidden="true" /><span className="chat-live-message">{statusText}</span><span className="chat-live-meta">{run.connected ? t('chat_live_connected') : t('chat_status_reconnecting')} · {t('chat_live_event_count', { count: run.state.events.length })} · {t('chat_live_elapsed', { seconds: run.elapsedSeconds })}</span></div>}
      {emptyConversation ? <div className="chat-welcome"><span className="chat-welcome-mark" aria-hidden="true">Y</span><span className="eyebrow">YAMIBO ASSISTANT</span><h2>{ready ? tx('从一条线索，开始整理。', 'Start organizing from a single clue.') : (sessions.loading ? tx('正在连接对话服务…', 'Connecting to chat…') : tx('对话服务暂不可用', 'Chat is unavailable'))}</h2><p>{ready ? tx('查找论坛内容、归档帖子、查看任务进度。', 'Search forum content, archive threads, and track tasks.') : tx('请检查服务配置；已有会话仍可查看。', 'Check the service settings. Existing sessions remain available.')}</p>{!ready ? <><button type="button" className="button chat-model-settings-inline" onClick={openModelSettings}>{tx('配置模型', 'Configure model')}</button>{unavailableDetails}</> : sessions.selected ? <div className="chat-starters">{[[tx('查找内容', 'Find content'), tx('搜索论坛中关于星灵感应的帖子', 'Find forum threads about Astral Feelings')], [tx('整理资料', 'Organize research'), tx('整理已归档内容中的相关资料', 'Organize relevant information from archived content')], [tx('查看进展', 'Check progress'), tx('查看最近创建的归档任务进度', 'Check the progress of recent archive tasks')]].map(([title, prompt]) => <button key={title} onClick={() => sessions.setDraft(prompt)}><strong>{title}</strong><span>{prompt}</span><b aria-hidden="true">↗</b></button>)}</div> : <button className="button button-primary" disabled={!!sessions.operation || !ready} onClick={() => void sessions.create().catch(() => {})}>{tx('开始新会话', 'Start a session')}</button>}</div> : <ChatTranscript messages={sessions.messages} pendingSubmissions={pendingTranscript} events={run.state.events} assistant={run.state.assistant} streamingEnabled={streamingEnabled} streaming={run.active && streamingEnabled} activity={activity} />}
      {run.status === 'waiting_for_approval' && <ChatApprovalDialog events={run.state.events} onChoose={run.approve} error={run.error} />}
      {!ready && !active ? <div className="chat-input-unavailable" role="status">{tx('输入已停用 · 服务恢复后可继续对话', 'Input is disabled · Chat will resume when the service is available')}</div> : <ChatComposer value={sessions.currentDraft} onChange={sessions.setDraft} onSend={() => void send()} onStop={() => void stop()} disabled={!ready || !sessions.selected || pending.some(item => item.status === 'failed' && !item.runId)} active={active} queueEnabled={embedded} sending={sending} />}
    </section>
    {embedded && sessions.selected && <ChatLocalPanel key={sessions.selected} sessionId={sessions.selected} visible={panelOpen} onClose={() => setPanelOpen(false)} onRuns={setLocalRuns} onFinished={() => void sessions.refreshMessages()} />}
  </main>
}
