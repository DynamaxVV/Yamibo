import { useEffect, useRef, useState } from 'react'
import { operationSummary } from './operationSummary'
import { api } from '../../api/client'
import { isTerminalStatus, type ChatRun } from '../../types/chat'
import { useI18n } from '../../context/I18nContext'

export type WorkspaceRun = ChatRun & { input: string; operations?: unknown[] }
type Operation = { id: string; status: string; plan_hash: string; tool: string; args: unknown; result?: unknown }

export function ChatLocalPanel({ sessionId, onFinished, onRuns, visible, onClose }: {
  sessionId: string; onFinished: () => void; onRuns: (runs: WorkspaceRun[]) => void; visible: boolean; onClose: () => void
}) {
  const { lang, tx } = useI18n()
  const statusLabel = (status: string) => ({
    queued: tx('排队中', 'Queued'), running: tx('执行中', 'Running'), waiting_jobs: tx('等待后台任务', 'Waiting for background tasks'),
    waiting_for_approval: tx('等待确认', 'Awaiting approval'), completed: tx('已完成', 'Completed'), failed: tx('失败', 'Failed'),
    cancelled: tx('已停止', 'Stopped'), interrupted: tx('已中断', 'Interrupted'), limited: tx('达到上限', 'Limit reached'),
  } as Record<string, string>)[status] || status
  const toolLabel = (tool: string) => ({
    create_jobs: tx('创建业务任务', 'Create tasks'), authorize_job_plan: tx('确认批量任务计划', 'Approve bulk task plan'),
    create_work_file: tx('创建工作文件', 'Create work file'), update_work_file: tx('修改工作文件', 'Update work file'),
    delete_work_file: tx('删除工作文件', 'Delete work file'), update_agent_guidance: tx('更新助手指南', 'Update assistant guidance'),
  } as Record<string, string>)[tool] || tool
  const callbacks = useRef({ onFinished, onRuns }); callbacks.current = { onFinished, onRuns }
  const [runs, setRuns] = useState<WorkspaceRun[]>([])
  const [files, setFiles] = useState<{ file_id: string; name: string; revision: number; source: string }[]>([])
  const [tab, setTab] = useState<'activity' | 'files'>('activity')
  const [error, setError] = useState('')
  const [preview, setPreview] = useState<{ name: string; content: string } | null>(null)
  const [loadingFile, setLoadingFile] = useState(false)
  const [busy, setBusy] = useState(false)
  const [loaded, setLoaded] = useState(false)
  const previewRequest = useRef(0)
  useEffect(() => {
    let live = true, timer: ReturnType<typeof setTimeout>, previous = ''
    const poll = async () => {
      try {
        const [r, f] = await Promise.all([api.chatRuns(sessionId), api.chatFiles()])
        if (!live) return
        setRuns(r.runs); setFiles(f.files); setLoaded(true); setError(''); callbacks.current.onRuns(r.runs)
        const summary = r.runs.map(x => x.run_id + x.status).join(',')
        if (previous !== summary) { previous = summary; callbacks.current.onFinished() }
      } catch (e) { if (live) setError((e as Error).message) }
      finally { if (live) timer = setTimeout(poll, 2000) }
    }
    void poll()
    return () => { live = false; clearTimeout(timer); previewRequest.current++ }
  }, [sessionId])
  const act = async (action: () => Promise<unknown>) => {
    setBusy(true); setError('')
    try { await action(); const r = await api.chatRuns(sessionId); setRuns(r.runs); callbacks.current.onRuns(r.runs); callbacks.current.onFinished() }
    catch (e) { setError((e as Error).message) } finally { setBusy(false) }
  }
  const showFile = async (id: string, name: string) => {
    const request = ++previewRequest.current
    setLoadingFile(true); setPreview(null)
    try {
      let content = '', offset: number | null = 0
      while (offset !== null) { const page = await api.chatFile(id, offset); if (request !== previewRequest.current) return; content += page.content; offset = page.next_offset }
      setPreview({ name, content })
    } catch (e) { if (request === previewRequest.current) setError((e as Error).message) }
    finally { if (request === previewRequest.current) setLoadingFile(false) }
  }
  return <aside className="chat-local-panel" hidden={!visible} aria-label={tx('工作面板', 'Workspace panel')}>
    <div className="chat-panel-head"><div><span className="eyebrow">{tx('工作空间', 'Workspace')}</span><h2>{tx('进度与成果', 'Progress and results')}</h2></div><button className="button" aria-label={tx('关闭工作面板', 'Close workspace panel')} onClick={onClose}>{tx('关闭', 'Close')}</button></div>
    <div className="chat-panel-tabs" role="tablist" aria-label={tx('工作面板内容', 'Workspace panel sections')}>
      <button role="tab" id="chat-activity-tab" aria-controls="chat-activity-panel" aria-selected={tab === 'activity'} onClick={() => setTab('activity')}>{tx('请求记录', 'Activity')} <span>{runs.length}</span></button>
      <button role="tab" id="chat-files-tab" aria-controls="chat-files-panel" aria-selected={tab === 'files'} onClick={() => setTab('files')}>{tx('工作文件', 'Work files')} <span>{files.length}</span></button>
    </div>
    {error && <p className="chat-error" role="alert">{error}</p>}
    <div className="chat-panel-content" role="tabpanel" id={tab === 'activity' ? 'chat-activity-panel' : 'chat-files-panel'} aria-labelledby={tab === 'activity' ? 'chat-activity-tab' : 'chat-files-tab'}>
      {!loaded && !error && <p className="chat-panel-empty">{tx('正在加载工作空间…', 'Loading workspace…')}</p>}
      {tab === 'activity' ? <>
        <p className="chat-panel-caption">{tx('当前会话的请求与操作结果。后台任务以任务页的最终状态为准。', 'Requests and results for this session. The task page shows final background task statuses.')}</p>
        {loaded && runs.length === 0 && <div className="chat-panel-empty">{tx('尚无请求', 'No requests yet')}<p>{tx('发送消息后，可在这里跟进执行过程。', 'Send a message to follow its progress here.')}</p></div>}
        {runs.map(r => <article className="chat-run-card" key={r.run_id}>
          <div className="chat-run-heading"><span className={`chat-run-status is-${r.status}`}>{statusLabel(r.status)}</span>
            {!isTerminalStatus(r.status) && <button className="chat-text-button" disabled={busy} onClick={() => void act(() => api.stopChatRun(r.run_id))}>{r.status === 'queued' ? tx('撤回', 'Withdraw') : tx('停止', 'Stop')}</button>}</div>
          <p className="chat-run-input">{r.input}</p>
          {r.error && <p className="chat-error">{r.error.message}</p>}
          {(r.operations as Operation[] || []).map(op => <details className="chat-operation" key={op.id} open={op.status === 'pending' ? true : undefined}>
            <summary>{toolLabel(op.tool)}<span>{op.status === 'pending' ? tx('待确认', 'Pending') : op.status === 'completed' ? tx('已完成', 'Completed') : op.status}</span></summary>
            <p>{operationSummary(op.tool, op.args, lang)}</p><pre>{JSON.stringify(op.args, null, 2)}</pre>
            {op.status === 'pending' ? <div className="chat-operation-actions"><button className="button button-primary" disabled={busy} onClick={() => void act(() => api.approveChatRun(r.run_id, 'once', false, op.id, op.plan_hash))}>{tx('批准本次', 'Approve once')}</button><button className="button" disabled={busy} onClick={() => void act(() => api.approveChatRun(r.run_id, 'deny', false, op.id, op.plan_hash))}>{tx('拒绝', 'Deny')}</button></div> : <pre>{JSON.stringify(op.result, null, 2)}</pre>}
          </details>)}
        </article>)}
      </> : <>
        <p className="chat-panel-caption">{tx('所有会话共享的本地文件。导入文件只读；助手创建的文件保留修改版本。', 'Local files shared across sessions. Imported files are read-only; assistant-created files keep their revision history.')}</p>
        {loaded && files.length === 0 && <div className="chat-panel-empty">{tx('还没有工作文件', 'No work files yet')}<p>{tx('试试让助手将整理结果保存为文件。', 'Ask the assistant to save its results as a file.')}</p></div>}
        {files.map(f => <button className="chat-file-card" key={f.file_id} onClick={() => void showFile(f.file_id, f.name)}><span className="chat-file-mark" aria-hidden="true">{tx('文', 'F')}</span><span><strong>{f.name}</strong><small>{tx(`版本 ${f.revision} · ${f.source === 'user' ? '导入 · 只读' : '助手创建'}`, `Version ${f.revision} · ${f.source === 'user' ? 'Imported · read only' : 'Created by assistant'}`)}</small></span><span aria-hidden="true">↗</span></button>)}
        {loadingFile && <p role="status">{tx('正在读取文件…', 'Loading file…')}</p>}
        {preview && <section className="chat-file-preview"><h3>{preview.name}</h3><pre>{preview.content}</pre></section>}
      </>}
    </div>
  </aside>
}
