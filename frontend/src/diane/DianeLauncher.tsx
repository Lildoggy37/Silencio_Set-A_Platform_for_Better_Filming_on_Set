import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { Button, Modal } from 'antd'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useAuth } from '../auth/AuthProvider'
import { ErrorNotice } from '../auth/AccountScreens'
import { useBusinessSession } from '../crews/useBusinessSession'
import { TaskProposals } from './TaskProposals'
import type { DianeHistory, DianePreferences, DianeSource, DianeTurn } from '../api/diane'

export function DianeLauncher({ crewId, userId, crewName, projectName, onSource, canManage = false, selfMemberId }: { canManage?: boolean; selfMemberId?: string; projectName?: string; crewId: string; userId: string; crewName: string; onSource: (source: DianeSource) => void }) {
  const { diane } = useAuth()
  const queryClient=useQueryClient()
  const status = useQuery({ queryKey: ['crews', userId, crewId, 'diane-status'], queryFn: ({ signal }) => diane.status(crewId, signal), retry: false, staleTime: 60_000 })
  const [open,setOpen]=useState(false), [expanded,setExpanded]=useState(false), [question,setQuestion]=useState(''), [asked,setAsked]=useState('')
  const [turns,setTurns]=useState<DianeTurn[]>([]), [error,setError]=useState<unknown>(null), [pending,setPending]=useState(false)
  const [conversationId,setConversationId]=useState<string|null|undefined>(undefined), [memoryOpen,setMemoryOpen]=useState(false)
  const [hasEarlier,setHasEarlier]=useState(false), [loadingEarlier,setLoadingEarlier]=useState(false)
  const [preferences,setPreferences]=useState<DianePreferences>({response_style:'concise',focus:'general'}), [savingPreferences,setSavingPreferences]=useState(false)
  const conversations=useQuery({queryKey:['crews',userId,crewId,'diane-conversations'],queryFn:({signal})=>diane.conversations(crewId,signal),enabled:open&&!!status.data?.enabled,refetchOnWindowFocus:false})
  const history=useQuery({queryKey:['crews',userId,crewId,'diane-history',conversationId],queryFn:({signal})=>diane.history(crewId,conversationId!,signal),enabled:open&&!!conversationId,staleTime:Infinity,refetchOnWindowFocus:false})
  const savedPreferences=useQuery({queryKey:['crews',userId,crewId,'diane-preferences'],queryFn:({signal})=>diane.preferences(crewId,signal),enabled:open&&!!status.data?.enabled,refetchOnWindowFocus:false})
  const activeTurn=turns.find(turn=>turn.status==='queued'||turn.status==='running')
  const waiting=!!activeTurn
  const [streamConnected,setStreamConnected]=useState(true)
  const loadingHistory=conversationId===undefined||(!!conversationId&&history.isPending)
  const [proposalOpen,setProposalOpen]=useState(false), [proposalVisited,setProposalVisited]=useState(false), [proposalLocked,setProposalLocked]=useState(false)
  const [resizing,setResizing]=useState(false)
  const [position,setPosition]=useState({left:0,top:0,width:450,height:480})
  const anchor=useRef<HTMLDivElement>(null), trigger=useRef<HTMLButtonElement>(null), input=useRef<HTMLTextAreaElement>(null), feed=useRef<HTMLDivElement>(null)
  const lock=useRef(false)
  useBusinessSession(error??status.error??conversations.error??history.error??savedPreferences.error)
  useEffect(()=>{if(conversationId===undefined&&conversations.data)setConversationId(conversations.data[0]?.id??null)},[conversationId,conversations.data])
  useEffect(()=>{if(history.data){
    const incoming=history.data.turns
    // A GET started before submission may arrive after the queued response/SSE.
    // Merge by version so an older snapshot cannot erase an accepted message.
    setTurns(previous=>{
      const merged=new Map(incoming.map(turn=>[turn.id,turn]))
      for(const turn of previous){const current=merged.get(turn.id);if(!current||turn.progress_version>current.progress_version)merged.set(turn.id,turn)}
      return [...merged.values()].sort((left,right)=>left.ordinal-right.ordinal)
    })
    setHasEarlier(history.data.has_earlier)
  }},[history.data])
  useEffect(()=>{if(savedPreferences.data)setPreferences(savedPreferences.data)},[savedPreferences.data])
  useEffect(()=>{
    if(!open||!conversationId||!activeTurn||status.data?.execution_mode!=='background')return
    const key=['crews',userId,crewId,'diane-history',conversationId]
    return diane.watchTurn(crewId,conversationId,activeTurn.id,turn=>{
      setTurns(previous=>previous.map(item=>item.id===turn.id&&turn.progress_version>=item.progress_version?turn:item))
      queryClient.setQueryData<DianeHistory>(key,previous=>previous?{...previous,turns:previous.turns.map(item=>item.id===turn.id&&turn.progress_version>=item.progress_version?turn:item)}:previous)
      setStreamConnected(true)
    },setStreamConnected,failure=>setError(failure))
  },[open,conversationId,activeTurn?.id,status.data?.execution_mode,crewId,userId,diane,queryClient])
  function close(){setResizing(false);setOpen(false);trigger.current?.focus()}
  useLayoutEffect(()=>{
    if(!open) return
    function place(){
      const bounds=anchor.current!.parentElement!.getBoundingClientRect()
      const button=trigger.current!.getBoundingClientRect()
      const width=window.innerWidth, height=window.innerHeight
      const navigationBottom=Math.max(0,document.querySelector('.topbar')?.getBoundingClientRect().bottom??0)
      if(expanded){
        if(width<=640){setPosition({left:0,top:navigationBottom,width,height:height-navigationBottom});return}
        setPosition({left:bounds.right,top:bounds.top,width:width-bounds.right,height:bounds.height});return
      }
      if(width<=640){setPosition({left:12,top:12,width:width-24,height:height-24});return}
      const panelHeight=Math.min(480,height-40)
      const top=Math.max(20,Math.min(height-panelHeight-20,button.bottom+100-panelHeight))
      setPosition({left:bounds.right,top,width:Math.min(450,width-bounds.right-20),height:panelHeight})
    }
    place()
    window.addEventListener('resize',place);window.addEventListener('scroll',place,true)
    return ()=>{window.removeEventListener('resize',place);window.removeEventListener('scroll',place,true)}
  },[open,expanded])
  useEffect(()=>{if(open&&feed.current) feed.current.scrollTop=feed.current.scrollHeight},[open,turns,pending,error])
  useEffect(()=>{
    if(!open) return
    input.current?.focus()
    function escape(event:KeyboardEvent){if(event.target instanceof Element&&event.target.closest('[aria-modal="true"]')) return;if(event.key==='Escape'&&!proposalOpen){event.preventDefault();close()}}
    window.addEventListener('keydown',escape)
    return ()=>window.removeEventListener('keydown',escape)
  },[open,proposalOpen])
  async function submit(){
    if(lock.current||!question.trim()||loadingHistory||waiting) return
    const text=question.trim()
    lock.current=true;setPending(true);setError(null);setAsked(text)
    try {
      const conversation=conversationId?history.data?.conversation:await diane.createConversation(crewId)
      const id=conversationId??conversation!.id
      if(!conversationId)setConversationId(id)
      const body={question:text,turn_id:crypto.randomUUID()}
      const turn=status.data!.execution_mode==='background'
        ?await diane.enqueue(crewId,id,body)
        :await diane.converse(crewId,id,body,status.data!.timeout_seconds)
      const key=['crews',userId,crewId,'diane-history',id]
      await queryClient.cancelQueries({queryKey:key})
      if(conversation)queryClient.setQueryData<DianeHistory>(key,{conversation,turns:[...turns.filter(item=>item.id!==turn.id),turn],has_earlier:hasEarlier})
      setTurns(previous=>[...previous.filter(item=>item.id!==turn.id),turn]);setQuestion('')
      void conversations.refetch()
    } catch(failure){setError(failure)} finally{lock.current=false;setPending(false);setAsked('')}
  }
  async function refreshHistory(){
    if(!conversationId)return
    try{await history.refetch({throwOnError:true});setError(null)}catch(failure){setError(failure)}
  }
  async function savePreferences(){
    setSavingPreferences(true);setError(null)
    try{await diane.savePreferences(crewId,preferences);setMemoryOpen(false)}catch(failure){setError(failure)}finally{setSavingPreferences(false)}
  }
  async function loadEarlier(){
    setLoadingEarlier(true)
    try{const data=await diane.history(crewId,conversationId!,undefined,turns[0].ordinal);setTurns(previous=>[...data.turns,...previous]);setHasEarlier(data.has_earlier)}catch(failure){setError(failure)}finally{setLoadingEarlier(false)}
  }
  if(!status.data?.enabled) return null
  return <div className="diane-anchor" ref={anchor} data-open={open}>
    <button type="button" className="diane-launcher" ref={trigger} aria-label="Diane" aria-expanded={open} aria-controls="diane-chat" onClick={()=>open?close():setOpen(true)}><span className="diane-star" aria-hidden="true">✦</span><span>Diane</span><span aria-hidden="true">→</span></button>
    {createPortal(<div className="diane-chat-shell" hidden={!open} data-expanded={expanded} data-resizing={resizing} onTransitionEnd={event=>{if(event.target===event.currentTarget)setResizing(false)}} style={{left:position.left,top:position.top,width:position.width,height:position.height}}><section id="diane-chat" className="diane-chat" role="dialog" aria-modal="false" aria-label="Diane聊天">
      <header className="diane-chat-header"><div><strong>Diane</strong><span title={crewName}>{projectName?`《${projectName}》`:crewName}</span></div><div className="diane-window-actions"><button type="button" aria-label={expanded?'还原Diane窗口':'放大Diane窗口'} aria-pressed={expanded} onClick={()=>{setResizing(true);setExpanded(!expanded)}}>{expanded?'↙':'↗'}</button><button type="button" aria-label="收起Diane" onClick={close}>×</button></div></header>
      <nav className="diane-memory-toolbar" aria-label="对话管理"><button type="button" disabled={pending||waiting||loadingHistory} onClick={()=>{setConversationId(null);setTurns([]);setHasEarlier(false);setError(null)}}>新对话 ＋</button><button type="button" aria-expanded={memoryOpen} onClick={()=>setMemoryOpen(!memoryOpen)}>历史与偏好</button>{conversationId&&<button type="button" disabled={pending} onClick={()=>void refreshHistory()}>刷新对话</button>}</nav>
      {memoryOpen&&<div className="diane-memory-settings"><label>历史对话<select aria-label="历史对话" value={conversationId??''} disabled={pending||waiting} onChange={event=>{setTurns([]);setConversationId(event.target.value||null)}}><option value="">新对话</option>{conversations.data?.map(item=><option key={item.id} value={item.id}>{item.title}</option>)}</select></label><div><label>回答方式<select value={preferences.response_style} onChange={event=>setPreferences({...preferences,response_style:event.target.value as DianePreferences['response_style']})}><option value="concise">简洁</option><option value="detailed">详细</option></select></label><label>关注方向<select value={preferences.focus} onChange={event=>setPreferences({...preferences,focus:event.target.value as DianePreferences['focus']})}><option value="general">全面筹备</option><option value="schedule">拍摄排期</option><option value="resources">资源准备</option></select></label></div><button type="button" disabled={savingPreferences} onClick={()=>void savePreferences()}>保存偏好</button></div>}
      <div className="diane-chat-feed" ref={feed} role="log" aria-label="当前对话">
        {loadingHistory&&<p role="status">正在恢复对话…</p>}
        {hasEarlier&&<button className="diane-older" type="button" disabled={loadingEarlier||pending||loadingHistory} onClick={()=>void loadEarlier()}>加载更早消息 ↑</button>}
        {!turns.length&&!loadingHistory&&!pending&&!error&&<div className="diane-welcome"><p>想先了解什么？</p><button type="button" onClick={()=>{setQuestion('下一次拍摄安排是什么？');input.current?.focus()}}>下一次拍摄安排 ↗</button><button type="button" onClick={()=>{setQuestion('当前剧组有哪些待处理任务？');input.current?.focus()}}>待处理的筹备任务 ↗</button></div>}
        {turns.map(({id,question:text,result,status:turnStatus,phase})=><article className="diane-exchange" key={id}><div className="diane-user-message"><span>我</span><p>{text}</p></div>{result?<section aria-label="Diane回答"><span className="diane-speaker">Diane</span><div className="diane-answer-text" role={result.status==='completed'?undefined:'alert'}>{result.answer}</div>
          {result.sources.length>0&&<details className="diane-sources"><summary>查看依据 ↗</summary>{result.sources.map((source,index)=>['tasks','task','resources','resource','plans','availability','changes','members'].includes(source.kind)?<Button key={index} onClick={()=>{close();onSource(source)}}>{source.label} ↗</Button>:<p key={index}>{source.label}</p>)}</details>}
          <details className="diane-usage"><summary>执行详情</summary><p>{(result.usage.elapsed_ms/1000).toFixed(1)} 秒 · {result.usage.total_tokens===null?'token 用量未完整返回':`${result.usage.total_tokens} tokens`}</p><dl><div><dt>模型耗时</dt><dd>{(result.usage.model_ms/1000).toFixed(1)} 秒</dd></div><div><dt>工具耗时</dt><dd>{(result.usage.tool_ms/1000).toFixed(1)} 秒</dd></div><div><dt>输入 / 输出 tokens</dt><dd>{result.usage.input_tokens??'未知'} / {result.usage.output_tokens??'未知'}</dd></div></dl>{result.tools.map((item,index)=><p key={index}>{item.source?.label??item.name} · {item.status==='success'?'已查询':'未能查询'}</p>)}<p>查询标识：{result.run_id}</p></details>
        </section>:<><p role={turnStatus==='failed'?'alert':'status'}>{turnStatus==='queued'?'Diane · 排队中…':turnStatus==='running'?(phase==='tools'?'Diane · 正在查询资料…':'Diane · 正在生成回答…'):turnStatus==='interrupted'?'本次执行已中断，未生成回答，可以重新提问。':phase==='access_denied'?'当前已无权访问本次对话，请检查剧组成员状态。':'Diane 执行失败，未生成回答。可以重新提问；若再次失败，请提供下方查询标识。'}</p>{turnStatus==='failed'&&<details className="diane-usage"><summary>故障详情</summary><p>查询标识：{id}</p><p>执行状态：{phase==='access_denied'?'访问权限失效':'后台执行失败'}</p></details>}</>}</article>)}
        {waiting&&!streamConnected&&<p role="status">正在重新连接，后台继续执行；也可刷新对话查看结果。</p>}
        {pending&&<><div className="diane-user-message"><span>我</span><p>{asked}</p></div><p className="diane-pending" role="status">Diane · 正在查询…</p></>}
        <ErrorNotice error={error??conversations.error??history.error??savedPreferences.error}/>
      </div>
      <footer className="diane-composer"><div className="diane-chat-tools">{canManage&&selfMemberId&&<button type="button" onClick={()=>{setProposalVisited(true);setProposalOpen(true)}}>拟定任务 ↗</button>}<details><summary>资料使用说明</summary><p>提问、近期对话、已保存偏好及查询资料将发送至阿里云百炼{status.data.model?` ${status.data.model}`:''}。对话仅本人在当前剧组可见，刷新后可恢复；业务事实每轮重新查询。</p></details></div>
        <form onSubmit={event=>{event.preventDefault();void submit()}}><label htmlFor="diane-question" className="sr-only">想了解什么？</label><textarea id="diane-question" ref={input} required maxLength={4000} rows={2} value={question} disabled={pending||waiting||loadingHistory} onChange={event=>setQuestion(event.target.value)} onKeyDown={event=>{if(event.key==='Enter'&&!event.shiftKey&&!event.nativeEvent.isComposing){event.preventDefault();void submit()}}} placeholder="问 Diane…"/><button type="submit" aria-label="查询剧组资料" disabled={!question.trim()||pending||waiting||loadingHistory}>→</button></form>
        {!turns.length&&<p className="diane-send-notice">发送后将由百炼处理提问及查询到的资料。</p>}
      </footer>
    </section></div>,document.body)}
    {proposalVisited&&selfMemberId&&<Modal title={`任务提案 · ${crewName}`} width={820} open={proposalOpen} footer={null} closable={!proposalLocked} keyboard={!proposalLocked} mask={{closable:!proposalLocked}} onCancel={()=>setProposalOpen(false)}><TaskProposals onLockChange={setProposalLocked} crewId={crewId} userId={userId} selfMemberId={selfMemberId} timeoutSeconds={status.data.timeout_seconds} onTask={id=>{setProposalOpen(false);close();onSource({kind:'task',label:'已创建任务',record_id:id})}}/></Modal>}
  </div>
}
