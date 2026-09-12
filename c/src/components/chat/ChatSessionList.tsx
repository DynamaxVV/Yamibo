import { useState } from 'react'
import { useI18n } from '../../context/I18nContext'
import type { ChatSession } from '../../types/chat'
type Props = { sessions: ChatSession[]; selected: string; onSelect: (id: string) => void; onCreate: () => void; onLoadMore: () => void; hasMore: boolean; busy?: boolean; loading?: boolean }
export function ChatSessionList({ sessions, selected, onSelect, onCreate, onLoadMore, hasMore, busy, loading: initialLoading = false }: Props) {
  const [loadingMore, setLoadingMore] = useState(false)
  const [search, setSearch] = useState('')
  const { t } = useI18n()
  const busyList = initialLoading || loadingMore
  return <aside className="chat-sessions" aria-busy={busyList}>
    <div className="chat-section-head"><strong>{t('chat_sessions')}</strong><button className="button button-primary" disabled={busy || initialLoading} onClick={onCreate}>{t('chat_new')}</button></div>
    <input className="chat-session-search" aria-label="搜索已加载的会话" placeholder="搜索会话…" value={search} onChange={e => setSearch(e.target.value)} />
    {initialLoading ? <div className="chat-session-loading" role="status">{t('chat_loading_sessions')}</div> : sessions.length === 0 ? <div className="chat-session-empty"><p>{t('chat_no_sessions')}</p><button className="button button-primary" onClick={onCreate}>{t('chat_new_session')}</button></div> : <div className="chat-session-list" onScroll={e => { const el = e.currentTarget; if (hasMore && !loadingMore && el.scrollTop + el.clientHeight >= el.scrollHeight - 24) { setLoadingMore(true); Promise.resolve(onLoadMore()).finally(() => setLoadingMore(false)) } }}>
      {sessions.filter(session => `${session.title} ${session.preview || ''}`.toLowerCase().includes(search.toLowerCase())).map(session => <div key={session.id} className={`chat-session ${selected === session.id ? 'active' : ''}`}><button className="chat-session-load" aria-current={selected === session.id ? 'page' : undefined} onClick={() => onSelect(session.id)} disabled={busy}><span className="chat-session-title">{session.title || t('chat_untitled')}</span><small>{session.preview || t('chat_messages_count', { count: session.message_count || 0 })}</small></button></div>)}
    </div>}
  <div className="chat-session-footer">Yamibo 业务助手<small>论坛内容 · 归档任务 · 工作文件</small></div></aside>
}
