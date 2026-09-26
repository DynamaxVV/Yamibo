import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { operationSummary } from './operationSummary'
import { api, type JobSummary, type OperationPlan, type OperationPlanPage, type OperationPlanStep } from '../../api/client'
import { isTerminalStatus, type ChatRun } from '../../types/chat'
import { useI18n } from '../../context/I18nContext'

export type WorkspaceRun = ChatRun & { input: string; operations?: unknown[] }
type Operation = { id: string; status: string; plan_hash: string; tool: string; args: unknown; result?: unknown }
function operationJobIds(runs: WorkspaceRun[]): string[] {
  const ids = new Set<string>()
  for (const run of runs) for (const raw of run.operations || []) {
    if (!raw || typeof raw !== 'object') continue
    const result = (raw as Operation).result
    if (!result || typeof result !== 'object') continue
    const data = (result as Record<string, unknown>).data
    if (!data || typeof data !== 'object') continue
    const payload = data as Record<string, unknown>
    if (Array.isArray(payload.job_ids)) payload.job_ids.forEach(id => { if (typeof id === 'string') ids.add(id) })
    const items = payload.items
    if (Array.isArray(items)) items.forEach(item => {
      if (!item || typeof item !== 'object') return
      const nested = (item as Record<string, unknown>).data
      if (nested && typeof nested === 'object' && typeof (nested as Record<string, unknown>).job_id === 'string') ids.add((nested as Record<string, unknown>).job_id as string)
    })
  }
  return [...ids]
}
const activeJob = (status: string) => ['queued', 'running', 'paused', 'retrying', 'cancel_requested'].includes(status)
const terminalPlan = (status: string) => ['completed', 'partially_completed', 'failed', 'cancelled'].includes(status)
function planJobIds(plans: OperationPlan[]): string[] {
  return [...new Set(plans.flatMap(plan => plan.items.flatMap(item => item.steps.map(step => step.job_id).filter((id): id is string => !!id))))]
}

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
  const [jobSummaries, setJobSummaries] = useState<Record<string, JobSummary>>({})
  const [files, setFiles] = useState<{ file_id: string; name: string; revision: number; source: string }[]>([])
  const [tab, setTab] = useState<'activity' | 'plans' | 'files'>('activity')
  const [error, setError] = useState('')
  const [preview, setPreview] = useState<{ name: string; content: string } | null>(null)
  const [loadingFile, setLoadingFile] = useState(false)
  const [busy, setBusy] = useState(false)
  const [loaded, setLoaded] = useState(false)
  const [plans, setPlans] = useState<OperationPlan[]>([])
  const [plansHasMore, setPlansHasMore] = useState(false)
  const [loadingMorePlans, setLoadingMorePlans] = useState(false)
  const previewRequest = useRef(0)
  useEffect(() => {
    let live = true, timer: ReturnType<typeof setTimeout>, previous = ''
    const poll = async () => {
      try {
        const planLimit = Math.min(100, Math.max(20, plans.length))
        const [r, f, p] = await Promise.all([
          api.chatRuns(sessionId), api.chatFiles(), api.operationPlans({ session_id: sessionId, limit: planLimit, offset: 0 }),
        ])
        if (!live) return
        setRuns(r.runs); setFiles(f.files); setPlans(p.plans); setPlansHasMore(p.has_more); setLoaded(true); setError(''); callbacks.current.onRuns(r.runs)
        const ids = [...new Set([...operationJobIds(r.runs), ...planJobIds(p.plans)])]
        const jobResults = await Promise.all(ids.map(async id => [id, await api.job(id)] as const).map(promise => promise.catch(() => null)))
        if (!live) return
        const summaries: Record<string, JobSummary> = {}
        for (const item of jobResults) if (item) summaries[item[0]] = item[1]
        setJobSummaries(summaries)
        const summary = r.runs.map(x => x.run_id + x.status).join(',')
        if (previous !== summary) { previous = summary; callbacks.current.onFinished() }
        const hasActiveRuns = r.runs.some(run => !isTerminalStatus(run.status))
        const hasActiveJobs = Object.values(summaries).some(job => activeJob(job.status))
        const hasActivePlans = p.plans.some(plan => !terminalPlan(plan.status))
        if (hasActiveRuns || hasActiveJobs || hasActivePlans) timer = setTimeout(poll, 2000)
      } catch (e) { if (live) setError((e as Error).message) }
    }
    void poll()
    return () => { live = false; clearTimeout(timer); previewRequest.current++ }
  }, [sessionId, visible, plans.length])
  const act = async (action: () => Promise<unknown>) => {
    setBusy(true); setError('')
    try {
      await action()
      const [r, p] = await Promise.all([api.chatRuns(sessionId), api.operationPlans({ session_id: sessionId, limit: 100, offset: 0 })])
      setRuns(r.runs); setPlans(p.plans); setPlansHasMore(p.has_more); callbacks.current.onRuns(r.runs); callbacks.current.onFinished()
    }
    catch (e) { setError((e as Error).message) } finally { setBusy(false) }
  }
  const loadMorePlans = async () => {
    setLoadingMorePlans(true); setError('')
    try {
      const page: OperationPlanPage = await api.operationPlans({ session_id: sessionId, limit: 20, offset: plans.length })
      setPlans(previous => [...previous, ...page.plans.filter(plan => !previous.some(item => item.plan_id === plan.plan_id))])
      setPlansHasMore(page.has_more)
    } catch (e) { setError((e as Error).message) } finally { setLoadingMorePlans(false) }
  }
  const planStatusLabel = (status: string) => ({
    awaiting_approval: tx('等待批准', 'Awaiting approval'), approved_pending_execution: tx('已批准 · 尚未开始', 'Approved · not started'),
    active: tx('执行中', 'Running'), cancellation_requested: tx('将停止未开始步骤', 'Unstarted steps will stop'),
    completed: tx('已完成', 'Completed'), partially_completed: tx('部分完成', 'Partially completed'),
    failed: tx('失败', 'Failed'), cancelled: tx('已取消', 'Cancelled'),
  } as Record<string, string>)[status] || status
  const stepLabel = (step: OperationPlanStep) => {
    const name = step.kind === 'archive'
      ? tx(`归档（${step.mode === 'full' ? '含图片' : '仅文本'}）`, `Archive (${step.mode === 'full' ? 'with images' : 'text only'})`)
      : tx(`导出 ${step.format || '文件'}`, `Export ${step.format || 'file'}`)
    const state = ({ required: tx('待开始', 'Not started'), queued: tx('后台任务已创建', 'Job created'), satisfied: tx('已满足', 'Already satisfied'), succeeded: tx('已完成', 'Completed'), failed: tx('失败', 'Failed'), cancelled: tx('未开始，已取消', 'Cancelled before start'), partially_completed: tx('部分完成', 'Partially completed') } as Record<string, string>)[step.state] || step.state
    return { name, state }
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
      <button role="tab" id="chat-plans-tab" aria-controls="chat-plans-panel" aria-selected={tab === 'plans'} onClick={() => setTab('plans')}>{tx('操作计划', 'Plans')} <span>{plans.length}</span></button>
      <button role="tab" id="chat-files-tab" aria-controls="chat-files-panel" aria-selected={tab === 'files'} onClick={() => setTab('files')}>{tx('工作文件', 'Work files')} <span>{files.length}</span></button>
    </div>
    {error && <p className="chat-error" role="alert">{error}</p>}
    <div className="chat-panel-content" role="tabpanel" id={`chat-${tab}-panel`} aria-labelledby={`chat-${tab}-tab`}>
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
            {op.status === 'pending' ? <>
              {['create_jobs', 'authorize_job_plan'].includes(op.tool) && <p className="chat-plan-notice">{tx('旧计划无法继续，请重新提出固定计划。', 'This legacy plan cannot continue. Propose a new frozen plan.')}</p>}
              <div className="chat-operation-actions">{!['create_jobs', 'authorize_job_plan'].includes(op.tool) && <button className="button button-primary" disabled={busy} onClick={() => void act(() => api.approveChatRun(r.run_id, 'once', false, op.id, op.plan_hash))}>{tx('批准本次', 'Approve once')}</button>}<button className="button" disabled={busy} onClick={() => void act(() => api.approveChatRun(r.run_id, 'deny', false, op.id, op.plan_hash))}>{tx('拒绝', 'Deny')}</button></div>
            </> : <pre>{JSON.stringify(op.result, null, 2)}</pre>}
            {operationJobIds([{ ...r, operations: [op] }]).map(id => { const job = jobSummaries[id]; const labels: Record<string, [string, string]> = { queued: ['排队中', 'Queued'], running: ['执行中', 'Running'], paused: ['已暂停', 'Paused'], retrying: ['重试中', 'Retrying'], cancel_requested: ['正在取消', 'Cancelling'], succeeded: ['已完成', 'Completed'], partial: ['部分完成', 'Partially complete'], failed: ['失败', 'Failed'], interrupted: ['已中断', 'Interrupted'], cancelled: ['已取消', 'Cancelled'] }; return <a className="chat-job-card" key={id} href={`/jobs/${encodeURIComponent(id)}`}><span>{tx('后台任务', 'Background job')} · {id}</span><strong>{job ? tx(...(labels[job.status] || [job.status, job.status])) : tx('读取任务状态中…', 'Loading job status…')}</strong>{job?.description && <small>{job.description}</small>}</a> })}
          </details>)}
        </article>)}
      </> : tab === 'plans' ? <>
        <p className="chat-panel-caption">{tx('计划、批准状态、固定步骤和逐帖回执来自持久快照。批准只表示授权，后台任务完成状态会单独显示。取消只阻止尚未开始的步骤。', 'Plans, approvals, fixed steps, and per-thread receipts come from persistent snapshots. Approval grants authorization; background job completion is shown separately. Cancellation only stops steps that have not started.')}</p>
        {loaded && plans.length === 0 && <div className="chat-panel-empty">{tx('当前会话没有操作计划', 'No operation plans in this session')}</div>}
        {plans.map(plan => <article className="chat-run-card chat-plan-card" key={plan.plan_id}>
          <div className="chat-run-heading"><span className={`chat-run-status is-${plan.status}`}>{planStatusLabel(plan.status)}</span></div>
          <p className="chat-plan-meta">{tx('目标', 'Targets')} TID {plan.items.map(item => item.tid).join(', ')} · {plan.action === 'archive' ? tx('归档', 'Archive') : tx('导出', 'Export')} · {tx('固定步骤', 'Fixed steps')} {plan.items.reduce((count, item) => count + item.steps.length, 0)}</p>
          {plan.items.map(item => <section className="chat-plan-item" key={item.tid}>
            <strong>TID {item.tid}{item.title ? ` · ${item.title}` : ''}</strong><span className={`chat-run-status is-${item.stage}`}>{planStatusLabel(item.stage)}</span>
            {item.steps.map((step, index) => { const label = stepLabel(step); const job = step.job_id ? jobSummaries[step.job_id] : undefined; const jobText = job ? tx(`后台任务：${job.status}`, `Job: ${job.status}`) : null; return <div className="chat-plan-step" key={`${item.tid}-${index}`}>
              <span>{label.name}</span><span>{label.state}</span>
              {step.job_id && <a href={`/jobs/${encodeURIComponent(step.job_id)}`}>{tx('任务', 'Job')} {step.job_id}{jobText ? ` · ${jobText}` : ''}</a>}
              {(step.error_code || step.error_message) && <p className="chat-error">{step.error_code && <strong>{step.error_code}: </strong>}{step.error_message}</p>}
            </div> })}
          </section>)}
          {plan.status === 'awaiting_approval' && plan.approval_ready && /^[a-f0-9]{64}$/i.test(plan.plan_hash) && <div className="chat-operation-actions"><button className="button button-primary" disabled={busy} onClick={() => void act(() => api.approveOperationPlan(plan.plan_id, { session_id: sessionId, plan_version: plan.plan_version, plan_hash: plan.plan_hash }))}>{tx('批准此固定计划', 'Approve this frozen plan')}</button><button className="button" disabled={busy} onClick={() => void act(() => api.cancelOperationPlan(plan.plan_id, sessionId))}>{tx('取消未开始计划', 'Cancel unstarted plan')}</button></div>}
          {['approved_pending_execution', 'active', 'cancellation_requested'].includes(plan.status) && <div className="chat-operation-actions"><button className="button" disabled={busy || plan.status === 'cancellation_requested'} onClick={() => void act(() => api.cancelOperationPlan(plan.plan_id, sessionId))}>{tx('取消尚未开始步骤', 'Cancel unstarted steps')}</button></div>}
          {plan.status === 'approved_pending_execution' && <p className="chat-panel-caption chat-plan-notice">{tx('已批准，等待后台接手；这不表示归档或导出已完成。', 'Approved and waiting for background pickup; this does not mean archiving or export is complete.')}</p>}
          {plan.action === 'export' && plan.items.some(item => item.steps.some(step => step.kind === 'export' && step.state === 'succeeded')) && <Link className="button" to="/exports">{tx('查看导出文件与保存位置', 'View exported files and locations')}</Link>}
        </article>)}
        {plansHasMore && <button className="button chat-plan-more" disabled={loadingMorePlans} onClick={() => void loadMorePlans()}>{loadingMorePlans ? tx('正在加载…', 'Loading…') : tx('加载更早计划', 'Load older plans')}</button>}
      </> : <>
        <p className="chat-panel-caption">{tx('所有会话共享的本地文件。助手可读取和修改，修改保留旧版本，删除移入回收区。', 'Local files shared across sessions. The assistant can read and edit them; edits keep prior versions and deletions move to trash.')}</p>
        {loaded && files.length === 0 && <div className="chat-panel-empty">{tx('还没有工作文件', 'No work files yet')}<p>{tx('试试让助手将整理结果保存为文件。', 'Ask the assistant to save its results as a file.')}</p></div>}
        {files.map(f => <button className="chat-file-card" key={f.file_id} onClick={() => void showFile(f.file_id, f.name)}><span className="chat-file-mark" aria-hidden="true">{tx('文', 'F')}</span><span><strong>{f.name}</strong><small>{tx(`版本 ${f.revision} · ${f.source === 'user' ? '导入' : '助手创建'}`, `Version ${f.revision} · ${f.source === 'user' ? 'Imported' : 'Created by assistant'}`)}</small></span><span aria-hidden="true">↗</span></button>)}
        {loadingFile && <p role="status">{tx('正在读取文件…', 'Loading file…')}</p>}
        {preview && <section className="chat-file-preview"><h3>{preview.name}</h3><pre>{preview.content}</pre></section>}
      </>}
    </div>
  </aside>
}
