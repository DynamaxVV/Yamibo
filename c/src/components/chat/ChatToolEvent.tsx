import { formatContent } from '../../types/chat'
import { record, stepError, toolLabel, type ToolStep } from './toolTimeline'
import { useI18n } from '../../context/I18nContext'

function sourceReceipt(result: unknown): { receiptId: string; tid: number; pid: number; floorNo?: number; sourceUrl?: string } | null {
  if (!result || typeof result !== 'object') return null
  const data = (result as Record<string, unknown>).data
  if (!data || typeof data !== 'object') return null
  const fields = data as Record<string, unknown>
  if (typeof fields.receipt_id !== 'string' || typeof fields.tid !== 'number' || typeof fields.pid !== 'number') return null
  let sourceUrl: string | undefined
  if (typeof fields.source_url === 'string') {
    try { const url = new URL(fields.source_url); if (url.protocol === 'http:' || url.protocol === 'https:') sourceUrl = url.toString() } catch { /* ignore malformed backend URLs */ }
  }
  return { receiptId: fields.receipt_id, tid: fields.tid, pid: fields.pid, floorNo: typeof fields.floor_no === 'number' ? fields.floor_no : undefined, sourceUrl }
}

export function ChatToolEvent({ step, streaming = false }: { step: ToolStep; streaming?: boolean }) {
  const { tx } = useI18n()
  const error = stepError(step)
  const failed = error !== undefined && error !== null
  const state = failed ? 'failed' : step.finished ? 'done' : streaming ? 'running' : 'unknown'
  const status = failed ? tx('失败', 'Failed') : step.finished ? tx('已返回', 'Returned') : streaming ? tx('执行中', 'Running') : tx('结果未确认', 'Unconfirmed')
  const args = record(step.args)
  const data = record(record(step.result).data)
  const context = [typeof args.query === 'string' ? args.query : '', args.forum_id != null ? tx(`板块 ${args.forum_id}`, `Forum ${args.forum_id}`) : '', args.page != null ? tx(`第 ${args.page} 页`, `Page ${args.page}`) : '', args.tid != null ? `TID ${args.tid}` : ''].filter(Boolean).join(' · ')
  const items = data.items ?? data.results
  const outcome = Array.isArray(items) ? tx(`返回 ${items.length} 条`, `${items.length} returned`) : typeof data.status === 'string' ? data.status : ''
  const receipt = step.name === 'read_discussion_source' ? sourceReceipt(record(step.result)) : null
  const errorFields = record(error)
  return <details className={`chat-execution-step is-${state}`}>
    <summary>
      <span className="chat-execution-mark" aria-hidden="true">{failed ? '!' : step.finished ? '✓' : '·'}</span>
      <span className="chat-execution-copy"><strong>{toolLabel(step.name, tx)}</strong>{context && <small>{context}</small>}</span>
      <span className="chat-execution-status">{outcome && !failed ? outcome : status}</span>
    </summary>
    {failed && <p className="chat-error">{String(errorFields.message || errorFields.code || formatContent(error))}</p>}
    {receipt && <div className="chat-source-receipt"><strong>{tx('来源回执', 'Source receipt')}</strong><span>TID {receipt.tid} · PID {receipt.pid}{receipt.floorNo ? ` · ${tx(`${receipt.floorNo} 楼`, `Floor ${receipt.floorNo}`)}` : ''}</span><code>{receipt.receiptId}</code>{receipt.sourceUrl && <a href={receipt.sourceUrl} target="_blank" rel="noreferrer">{tx('打开原帖楼层', 'Open source floor')}</a>}</div>}
    <div className="chat-execution-payload"><code>{step.name}</code>{step.args !== undefined && <><h5>{tx('调用参数', 'Arguments')}</h5><pre>{formatContent(step.args)}</pre></>}{step.result !== undefined && <><h5>{tx('工具返回', 'Tool result')}</h5><pre>{formatContent(step.result)}</pre></>}</div>
  </details>
}
