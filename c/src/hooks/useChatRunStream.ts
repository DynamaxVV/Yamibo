import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../api/client'
import { isTerminalStatus, reduceChatEvent, type ChatEvent, type ChatRunStatus, type ChatState } from '../types/chat'
export type SseFrame = { id?: string; event?: string; data: string }
export function parseSseFrames(buffer: string, eof = false): { frames: SseFrame[]; rest: string } {
  const blocks = buffer.split(/\r\n\r\n|\n\n|\r\r/)
  let rest = ''
  if (!eof) rest = blocks.pop() ?? ''
  const frames: SseFrame[] = []
  for (const block of blocks) {
    let id: string | undefined
    let event: string | undefined
    const data: string[] = []
    for (const line of block.split(/\r\n|\n|\r/)) {
      if (!line || line.startsWith(':')) continue
      const index = line.indexOf(':')
      const field = index < 0 ? line : line.slice(0, index)
      const value = (index < 0 ? '' : line.slice(index + 1)).replace(/^ /, '')
      if (field === 'id') id = value
      else if (field === 'event') event = value
      else if (field === 'data') data.push(value)
    }
    if (data.length) frames.push({ id, event, data: data.join('\n') })
  }
  return { frames, rest }
}
export const parseSseChunk=parseSseFrames
type RunView={runId:string|null;state:ChatState;error:string|null}
const emptyState=():ChatState=>({status:'unknown',lastSeq:0,assistant:'',events:[],terminal:false,connected:false}); const key='yamibo.chat.run-probes.v3'
function readStore():Record<string,{run_id:string;last_seq:number}>{try{const raw=localStorage.getItem(key);const parsed=raw?JSON.parse(raw):{};return parsed&&typeof parsed==='object'?parsed:{} }catch{return {}}}
function persist(sessionId:string,runId:string,seq:number){const all=readStore();all[sessionId]={run_id:runId,last_seq:seq};try{localStorage.setItem(key,JSON.stringify(all))}catch{/* optional */}}
const activeStatus=(status:ChatRunStatus)=>['preparing','submitting','queued','running','waiting_jobs','waiting_for_approval','stopping','reconciling'].includes(status)
export function useChatRunStream(sessionId:string,refreshMessages:(id?:string)=>Promise<void>,lang:'zh'|'en'){const [views,setViews]=useState<Record<string,RunView>>({});const [now,setNow]=useState(()=>Date.now());const controllersRef=useRef<Record<string,AbortController>>({});const recoveredSessionsRef=useRef<Record<string,boolean>>({});const selectedRef=useRef(sessionId);const viewsRef=useRef(views);useEffect(()=>{selectedRef.current=sessionId;viewsRef.current=views},[sessionId,views]);useEffect(()=>{if(!Object.values(views).some(view=>activeStatus(view.state.status)))return;const timer=window.setInterval(()=>setNow(Date.now()),1000);return()=>window.clearInterval(timer)},[views])
  const update=(id:string,fn:(view:RunView)=>RunView)=>setViews(prev=>{const old=prev[id]||{runId:null,state:emptyState(),error:null};return {...prev,[id]:fn(old)}})
  const consume = useCallback(async (session: string, runId: string, initialSeq: number) => {
    let after = initialSeq
    const finalizeLostConnection = async () => {
      await refreshMessages(session)
        update(session, view => ({
          ...view,
          runId,
          error: lang === 'en' ? 'The live connection was lost. Messages are now up to date.' : '实时连接已丢失，已校准当前消息',
          state: { ...view.state, status: 'unknown', terminal: true, connected: false },
        }))
    }
    for (let attempt = 0; attempt < 3; attempt += 1) {
      const controller = new AbortController()
      controllersRef.current[session] = controller
      try {
        const body = await api.openChatRunEvents(runId, after ? String(after) : undefined, controller.signal)
        update(session, view => ({ ...view, state: { ...view.state, connected: true } }))
        const reader = body.getReader()
        const decoder = new TextDecoder()
        let buffer = ''
        let reconciled = false
        const handle = async (frame: SseFrame) => {
          try {
            const event = JSON.parse(frame.data) as ChatEvent
            if (event.run_id !== runId || event.seq <= after) return
            after = event.seq
            persist(session, runId, after)
            update(session, view => {
              const state = reduceChatEvent(view.state, event)
              return { ...view, runId, state: { ...state, connected: event.type !== 'session.reconciled', lastEventAt: Date.now() }, error: event.type === 'stream.error' ? event.error.message : (event.type === 'run.failed' || event.type === 'run.limited' || event.type === 'run.interrupted') ? event.error?.message || event.type : view.error }
            })
            if (event.type === 'stream.gap' || event.type === 'stream.error') {
              const status = await api.getChatRun(runId)
              update(session, view => ({ ...view, state: { ...view.state, status: activeStatus(status.status) ? 'reconciling' : view.state.status } }))
              await refreshMessages(session)
            }
            if (event.type === 'session.reconciled') {
              reconciled = true
              await refreshMessages(session)
              update(session, view => ({ ...view, state: { ...view.state, assistant: '', connected: false } }))
            }
          } catch {
            update(session, view => ({ ...view, error: lang === 'en' ? 'Unable to parse a stream event.' : '无法解析流事件' }))
          }
        }
        while (true) {
          const { done, value } = await reader.read()
          if (done) break
          const parsed = parseSseFrames(buffer + decoder.decode(value, { stream: true }))
          buffer = parsed.rest
          for (const frame of parsed.frames) await handle(frame)
        }
        const tail = parseSseFrames(buffer, true)
        for (const frame of tail.frames) await handle(frame)
        if (reconciled) return
        const run = await api.getChatRun(runId)
        if (isTerminalStatus(run.status)) {
          await refreshMessages(session)
          return
        }
        if (!activeStatus(run.status)) {
          await refreshMessages(session)
          return
        }
        // Keep the browser's last received sequence.  `run.last_seq` is the
        // server's newest sequence and using it here would skip buffered
        // events when a tab was backgrounded or its SSE connection was
        // interrupted.  The next subscription replays from `after`.
      } catch (error) {
        if ((error as Error).name === 'AbortError') return
        update(session, view => ({ ...view, error: (error as Error).message, state: { ...view.state, connected: false } }))
        if (attempt === 2) {
          await finalizeLostConnection()
          return
        }
        await new Promise(resolve => setTimeout(resolve, 200 * (attempt + 1)))
      }
    }
    await finalizeLostConnection()
  }, [refreshMessages, lang])
  const followRun = useCallback((runId: string, status: import('../types/chat').ChatRun['status']) => {
    const view = viewsRef.current[sessionId]
    if (view?.runId === runId || (view?.runId && activeStatus(view.state.status))) return
    controllersRef.current[sessionId]?.abort()
    persist(sessionId, runId, 0)
    update(sessionId, () => ({ runId, state: { ...emptyState(), status, startedAt: Date.now() }, error: null }))
    void consume(sessionId, runId, 0)
  }, [consume, sessionId])
  const requestRef=useRef<{session:string;input:string;id:string}|null>(null)
  const sendingRef=useRef(false)
  const start=useCallback(async(input:string)=>{if(sendingRef.current)throw new Error(lang === 'en' ? 'A message is already being submitted.' : '正在提交');sendingRef.current=true;
    if(!requestRef.current){try{requestRef.current=JSON.parse(sessionStorage.getItem('yamibo.chat.pending.'+sessionId)||'null')}catch{}}
    if(!requestRef.current||requestRef.current.session!==sessionId||requestRef.current.input!==input)requestRef.current={session:sessionId,input,id:crypto.randomUUID()};
    sessionStorage.setItem('yamibo.chat.pending.'+sessionId,JSON.stringify(requestRef.current));
    try { const old=controllersRef.current[sessionId];old?.abort();const run=await api.startChatRun(sessionId,input,requestRef.current.id);requestRef.current=null;sessionStorage.removeItem('yamibo.chat.pending.'+sessionId);persist(sessionId,run.run_id,0);update(sessionId,()=>({runId:run.run_id,state:{...emptyState(),status:run.status,startedAt:Date.now()},error:null}));void consume(sessionId,run.run_id,0);return run} finally {sendingRef.current=false}},[consume,sessionId,lang])
  const stop=useCallback(async()=>{const view=viewsRef.current[sessionId];if(view?.runId){await api.stopChatRun(view.runId);update(sessionId,v=>({...v,state:{...v.state,status:'stopping'}}))}},[sessionId])
  const reconnect=useCallback(async(session:string,force=false)=>{
    const view=viewsRef.current[session]
    if(!view?.runId||!activeStatus(view.state.status))return
    const idleFor=view.state.lastEventAt===undefined?Number.POSITIVE_INFINITY:Date.now()-view.state.lastEventAt
    if(!force&&view.state.connected===true&&idleFor<15000)return
    const runId=view.runId
    const saved=readStore()[session]
    const initialSeq=saved?.run_id===runId?Math.max(view.state.lastSeq,saved.last_seq):view.state.lastSeq
    controllersRef.current[session]?.abort()
    try{
      const remote=await api.getChatRun(runId)
      if(isTerminalStatus(remote.status)){
        await refreshMessages(session)
        update(session,current=>({...current,state:{...current.state,status:remote.status,terminal:true,connected:false}}))
        return
      }
      update(session,current=>({...current,state:{...current.state,status:remote.status,connected:false}}))
      void consume(session,runId,initialSeq)
    }catch{
      // The active consumer owns normal retry/error handling.  A visibility
      // probe is best-effort and must not turn a recoverable run into a stop.
    }
  },[consume,refreshMessages])
  const approve=useCallback(async(choice:'once'|'session'|'always'|'deny')=>{const view=viewsRef.current[sessionId];if(view?.runId){const req=[...view.state.events].reverse().find((e):e is Extract<ChatEvent,{type:'approval.request'}>=>e.type==='approval.request');await api.approveChatRun(view.runId,choice,false,req?.approval_id,req?.plan_hash)}},[sessionId])
  useEffect(()=>{if(!sessionId||recoveredSessionsRef.current[sessionId])return;recoveredSessionsRef.current[sessionId]=true;const saved=readStore()[sessionId];if(!saved){void api.chatRuns(sessionId).then(({runs})=>{const r=runs.find(r=>activeStatus(r.status));if(r){persist(sessionId,r.run_id,0);update(sessionId,v=>({...v,runId:r.run_id,state:{...v.state,status:r.status}}));void consume(sessionId,r.run_id,0)}}).catch(()=>{});return};void api.getChatRun(saved.run_id).then(run=>{update(sessionId,v=>({...v,runId:saved.run_id,state:{...v.state,status:run.status,lastSeq:0}}));if(activeStatus(run.status))void consume(sessionId,saved.run_id,0);else void refreshMessages(sessionId)}).catch(()=>void refreshMessages(sessionId))},[consume,refreshMessages,sessionId])
  useEffect(()=>{
    const resume=()=>{if(document.visibilityState==='visible')void reconnect(sessionId)}
    const restore=()=>{if(document.visibilityState==='visible')void reconnect(sessionId,true)}
    document.addEventListener('visibilitychange',resume)
    window.addEventListener('pageshow',restore)
    return()=>{document.removeEventListener('visibilitychange',resume);window.removeEventListener('pageshow',restore)}
  },[reconnect,sessionId])
  // Unmounting only releases this browser's SSE subscriber.  The server-side
  // ChatRunRegistry remains the sole Hermes consumer until the Run reaches a
  // terminal state, so closing the page never sends an implicit stop.
  useEffect(()=>()=>{Object.values(controllersRef.current).forEach(controller=>controller.abort())},[])
  const current = views[sessionId] || { runId: null, state: emptyState(), error: null }
  const activeSessionIds = useMemo(() => new Set(Object.entries(views).filter(([, view]) => view.runId !== null && activeStatus(view.state.status)).map(([id]) => id)), [views])
  const elapsedSeconds = current.state.startedAt ? Math.max(0, Math.floor((now - current.state.startedAt) / 1000)) : 0
  const secondsSinceEvent = current.state.lastEventAt ? Math.max(0, Math.floor((now - current.state.lastEventAt) / 1000)) : null
  return useMemo(() => ({
    runId: current.runId,
    status: current.state.status,
    state: current.state,
    error: current.error,
    active: !!current.runId && activeStatus(current.state.status),
    connected: current.state.connected === true,
    elapsedSeconds,
    secondsSinceEvent,
    start,
    followRun,
    stop,
    approve,
    views,
    activeSessionIds,
  }), [followRun, activeSessionIds, approve, current, elapsedSeconds, now, reconnect, secondsSinceEvent, start, stop, views])
}
