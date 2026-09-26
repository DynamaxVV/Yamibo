import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../api/client'
import type { ChatContext, ChatMessage, ChatSession } from '../types/chat'
const SELECTED = 'yamibo.chat.selected-session.v3'
const DRAFTS = 'yamibo.chat.drafts.v3'
function read<T>(key:string, fallback:T):T { try { const value=localStorage.getItem(key); return value ? JSON.parse(value) as T : fallback } catch { return fallback } }
function write(key:string, value:unknown) { try { localStorage.setItem(key, JSON.stringify(value)) } catch { /* storage is optional */ } }
export function useChatSessions(initialSessionId = '') {
  const [context,setContext]=useState<ChatContext|null>(null); const [sessions,setSessions]=useState<ChatSession[]>([]); const [selected,setSelectedState]=useState<string>(()=>{if(initialSessionId)return initialSessionId;const value=read<unknown>(SELECTED,'');return typeof value==='string'?value:''}); const selectedRef=useRef(selected)
  const [messages,setMessages]=useState<ChatMessage[]>([]); const [loading,setLoading]=useState(true); const [error,setError]=useState<string|null>(null); const [operationError,setOperationError]=useState<string|null>(null); const [operation,setOperation]=useState<string|null>(null); const [hasMore,setHasMore]=useState(false); const [drafts,setDrafts]=useState<Record<string,string>>(()=>read(DRAFTS,{})); const [offset,setOffset]=useState(0); const loadingMore=useRef(false); const messageRequest=useRef(0); const messagesAbort=useRef<AbortController|null>(null); const sessionsRef=useRef<ChatSession[]>([])
  useEffect(()=>{sessionsRef.current=sessions},[sessions]); useEffect(()=>{selectedRef.current=selected},[selected])
  const currentDraft=selected ? drafts[selected] || '' : ''
  const setDraft=useCallback((value:string,session=selectedRef.current)=>{if(!session)return;setDrafts(prev=>{const next={...prev,[session]:value};write(DRAFTS,next);return next})},[])
  const refreshMessages=useCallback(async(id?:string)=>{const target=id||selectedRef.current;if(!target)return;const token=++messageRequest.current;messagesAbort.current?.abort();const controller=new AbortController();messagesAbort.current=controller;try{const rows=await api.chatMessages(target,controller.signal);if(token===messageRequest.current&&target===selectedRef.current)setMessages(rows)}catch(e){if((e as Error).name!=='AbortError'&&token===messageRequest.current&&target===selectedRef.current)setError((e as Error).message)}},[])
  const refreshContext=useCallback(async()=>{try{setContext(await api.chatContext())}catch(e){setError((e as Error).message)}},[])
  const select=useCallback(async(id:string)=>{selectedRef.current=id;setSelectedState(id);write(SELECTED,id);messageRequest.current++;messagesAbort.current?.abort();setMessages([]);if(id)void refreshMessages(id)},[refreshMessages])
  const load=useCallback(async(reset=true)=>{
    if(loadingMore.current)return
    loadingMore.current=true;setLoading(reset);setError(null)
    const wanted=selectedRef.current
    try{
      const [ctx,firstPage]=await Promise.all([reset?api.chatContext():Promise.resolve(context),api.chatSessions({limit:50,offset:reset?0:offset})])
      let page=firstPage
      const rows=[...(page.sessions||page.items||[])]
      let nextOffset=(reset?0:offset)+rows.length
      let more=page.has_more??rows.length===50
      // A saved selection may be older than the first page. Resolve it before falling back.
      while(reset&&wanted&&!rows.some(row=>row.id===wanted)&&more){
        page=await api.chatSessions({limit:50,offset:nextOffset})
        const next=page.sessions||page.items||[]
        rows.push(...next);nextOffset+=next.length
        more=next.length>0&&(page.has_more??next.length===50)
      }
      setContext(ctx);setSessions(prev=>reset?rows:[...prev,...rows.filter(row=>!prev.some(old=>old.id===row.id))])
      setOffset(nextOffset);setHasMore(more)
      if(reset&&selectedRef.current===wanted){
        const chosen=rows.some(row=>row.id===wanted)?wanted:rows[0]?.id||''
        if(chosen!==wanted||!chosen)await select(chosen)
        else {write(SELECTED,chosen);await refreshMessages(chosen)}
      }
    }catch(e){setError((e as Error).message)}finally{setLoading(false);loadingMore.current=false}
  },[context,offset,refreshMessages,select])
  useEffect(()=>{void load();return()=>{messagesAbort.current?.abort()}},[]) // initial authority load
  const operationCall=useCallback(async<T>(name:string,fn:()=>Promise<T>)=>{setOperation(name);setOperationError(null);try{return await fn()}catch(e){setOperationError((e as Error).message);throw e}finally{setOperation(null)}},[])
  const create=useCallback(async(title?:string)=>operationCall('create',async()=>{const row=await api.createChatSession(title);setSessions(prev=>[row,...prev]);setOffset(prev=>prev+1);await select(row.id);return row}),[operationCall,select])
  const rename=useCallback(async(id:string,title:string)=>operationCall('rename',async()=>{const row=await api.renameChatSession(id,title);setSessions(prev=>prev.map(item=>item.id===id?{...item,...row}:item))}),[operationCall])
  const remove=useCallback(async(id:string)=>operationCall('delete',async()=>{await api.deleteChatSession(id);const current=sessionsRef.current.filter(item=>item.id!==id);setSessions(current);setOffset(prev=>Math.max(0,prev-1));setDrafts(prev=>{const next={...prev};delete next[id];write(DRAFTS,next);return next});if(id===selectedRef.current){const next=current[0]?.id||'';await select(next)}}),[operationCall,select])
  return {context,sessions,selected,messages,loading,error,operationError,operation,hasMore,currentDraft,setDraft,select,loadMore:()=>load(false),create,rename,remove,refreshMessages,refreshContext}
}
