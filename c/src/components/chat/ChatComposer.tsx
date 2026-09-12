import { useRef } from 'react'
import { useI18n } from '../../context/I18nContext'

export function ChatComposer({ value, onChange, onSend, onStop, disabled, active, queueEnabled = false, sending = false }: {
  value: string; onChange: (value: string) => void; onSend: () => void; onStop: () => void
  disabled: boolean; active: boolean; queueEnabled?: boolean; sending?: boolean
}) {
  const { t } = useI18n()
  const composing = useRef(false)
  const sendDisabled = disabled || sending || !value.trim() || (active && !queueEnabled)
  return <div className="chat-compose-area">
    <div className="chat-composer">
      <textarea rows={3} value={value} disabled={disabled || sending || (active && !queueEnabled)}
        onChange={e => onChange(e.target.value)} onCompositionStart={() => { composing.current = true }}
        onCompositionEnd={() => { composing.current = false }} onKeyDown={e => {
          if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing && !composing.current && !sendDisabled) { e.preventDefault(); onSend() }
        }} placeholder={active && queueEnabled ? '写下下一步，当前请求结束后依次执行…' : '搜索论坛内容、整理资料，或描述你要创建的任务…'} aria-label={t('chat_message_placeholder')} />
      <div className="chat-compose-actions"><span>{active && queueEnabled ? '按顺序执行' : 'Enter 发送 · Shift + Enter 换行'}</span><div>
        {active && <button className="button" onClick={onStop}>{t('chat_stop')}</button>}
        <button className="button button-primary" disabled={sendDisabled} onClick={onSend}>{sending ? '提交中…' : active && queueEnabled ? '加入队列' : t('chat_send')}</button>
      </div></div>
    </div>
    <p className="chat-compose-note">{queueEnabled ? '关闭页面后继续执行 · 停止对话不会取消已创建的任务' : '请核对操作结果与引用来源'}</p>
  </div>
}
