import { useEffect, useRef, useState } from 'react'
import { operationSummary } from './operationSummary'
import { api } from '../../api/client'
import { isTerminalStatus, type ChatRun } from '../../types/chat'

export const statusLabels: Record<string, string> = { queued: '排队中', running: '执行中', waiting_jobs: '等待后台任务', waiting_for_approval: '等待确认', completed: '已完成', failed: '失败', cancelled: '已停止', interrupted: '已中断', limited: '达到上限' }
export type WorkspaceRun = ChatRun & { input: string; operations?: unknown[] }
type Operation = { id: string; status: string; plan_hash: string; tool: string; args: unknown; result?: unknown }
const toolLabels: Record<string, string> = { create_jobs: '创建业务任务', authorize_job_plan: '确认批量任务计划', create_work_file: '创建工作文件', update_work_file: '修改工作文件', delete_work_file: '删除工作文件', update_agent_guidance: '更新助手指南' }

export function ChatLocalPanel({ sessionId, onFinished, onRuns, visible, onClose }: {
  sessionId: string; onFinished: () => void; onRuns: (runs: WorkspaceRun[]) => void; visible: boolean; onClose: () => void
}) {
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
  return <aside className="chat-local-panel" hidden={!visible} aria-label="工作面板">
    <div className="chat-panel-head"><div><span className="eyebrow">工作空间</span><h2>进度与成果</h2></div><button className="button" aria-label="关闭工作面板" onClick={onClose}>关闭</button></div>
    <div className="chat-panel-tabs" role="tablist" aria-label="工作面板内容">
      <button role="tab" id="chat-activity-tab" aria-controls="chat-activity-panel" aria-selected={tab === 'activity'} onClick={() => setTab('activity')}>请求记录 <span>{runs.length}</span></button>
      <button role="tab" id="chat-files-tab" aria-controls="chat-files-panel" aria-selected={tab === 'files'} onClick={() => setTab('files')}>工作文件 <span>{files.length}</span></button>
    </div>
    {error && <p className="chat-error" role="alert">{error}</p>}
    <div className="chat-panel-content" role="tabpanel" id={tab === 'activity' ? 'chat-activity-panel' : 'chat-files-panel'} aria-labelledby={tab === 'activity' ? 'chat-activity-tab' : 'chat-files-tab'}>
      {!loaded && !error && <p className="chat-panel-empty">正在加载工作空间…</p>}
      {tab === 'activity' ? <>
        <p className="chat-panel-caption">当前会话的请求与操作结果。后台任务以任务页的最终状态为准。</p>
        {loaded && runs.length === 0 && <div className="chat-panel-empty">尚无请求<p>发送消息后，可在这里跟进执行过程。</p></div>}
        {runs.map(r => <article className="chat-run-card" key={r.run_id}>
          <div className="chat-run-heading"><span className={`chat-run-status is-${r.status}`}>{statusLabels[r.status] || r.status}</span>
            {!isTerminalStatus(r.status) && <button className="chat-text-button" disabled={busy} onClick={() => void act(() => api.stopChatRun(r.run_id))}>{r.status === 'queued' ? '撤回' : '停止'}</button>}</div>
          <p className="chat-run-input">{r.input}</p>
          {r.error && <p className="chat-error">{r.error.message}</p>}
          {(r.operations as Operation[] || []).map(op => <details className="chat-operation" key={op.id} open={op.status === 'pending' ? true : undefined}>
            <summary>{toolLabels[op.tool] || op.tool}<span>{op.status === 'pending' ? '待确认' : op.status === 'completed' ? '已完成' : op.status}</span></summary>
            <p>{operationSummary(op.tool, op.args)}</p><pre>{JSON.stringify(op.args, null, 2)}</pre>
            {op.status === 'pending' ? <div className="chat-operation-actions"><button className="button button-primary" disabled={busy} onClick={() => void act(() => api.approveChatRun(r.run_id, 'once', false, op.id, op.plan_hash))}>批准本次</button><button className="button" disabled={busy} onClick={() => void act(() => api.approveChatRun(r.run_id, 'deny', false, op.id, op.plan_hash))}>拒绝</button></div> : <pre>{JSON.stringify(op.result, null, 2)}</pre>}
          </details>)}
        </article>)}
      </> : <>
        <p className="chat-panel-caption">所有会话共享的本地文件。导入文件只读；助手创建的文件保留修改版本。</p>
        {loaded && files.length === 0 && <div className="chat-panel-empty">还没有工作文件<p>试试让助手将整理结果保存为文件。</p></div>}
        {files.map(f => <button className="chat-file-card" key={f.file_id} onClick={() => void showFile(f.file_id, f.name)}><span className="chat-file-mark" aria-hidden="true">文</span><span><strong>{f.name}</strong><small>版本 {f.revision} · {f.source === 'user' ? '导入 · 只读' : '助手创建'}</small></span><span aria-hidden="true">↗</span></button>)}
        {loadingFile && <p role="status">正在读取文件…</p>}
        {preview && <section className="chat-file-preview"><h3>{preview.name}</h3><pre>{preview.content}</pre></section>}
      </>}
    </div>
  </aside>
}
