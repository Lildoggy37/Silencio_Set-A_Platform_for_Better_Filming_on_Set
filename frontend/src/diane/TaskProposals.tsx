import { useEffect, useRef, useState } from 'react'
import { Button, Input } from 'antd'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useAuth } from '../auth/AuthProvider'
import { ErrorNotice } from '../auth/AccountScreens'
import { AccountError } from '../api/accounts'
import type { DianeResult, TaskProposal } from '../api/diane'
import { useBusinessSession } from '../crews/useBusinessSession'

const names = {pending:'待确认', completed:'已创建', rejected:'已放弃'}
const date = (value:string) => new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).format(new Date(value))

export function TaskProposals({crewId,userId,selfMemberId,timeoutSeconds,onTask,onLockChange}: {onLockChange?:(locked:boolean)=>void;crewId:string;userId:string;selfMemberId:string;timeoutSeconds:number;onTask:(id:string)=>void}) {
  const {diane,crews} = useAuth()
  const cache = useQueryClient()
  const [question,setQuestion]=useState(''), [owner,setOwner]=useState(selfMemberId), [due,setDue]=useState('')
  const [memberPage,setMemberPage]=useState(1)
  const [proposal,setProposal]=useState<TaskProposal|null>(null), [run,setRun]=useState<DianeResult|null>(null)
  const [error,setError]=useState<unknown>(null), [pending,setPending]=useState(false)
  const [frozen,setFrozen]=useState<{id:string;body:{expected_lock_version:number;decision:'accept'|'reject'}}|null>(null)
  const lock=useRef(false)
  useEffect(()=>{onLockChange?.(pending||!!frozen)},[pending,frozen,onLockChange])
  const history=useQuery({queryKey:['crews',userId,crewId,'diane-proposals'],queryFn:({signal})=>diane.proposals(crewId,signal),retry:false})
  const members=useQuery({queryKey:['crews',userId,crewId,'diane-assignees',memberPage],queryFn:({signal})=>crews.members(crewId,memberPage,signal),retry:false})
  useBusinessSession(error??history.error??members.error)
  async function draft() {
    if(lock.current) return
    lock.current=true;setPending(true);setError(null);setRun(null)
    try {
      const result=await diane.draftTasks(crewId,{question:question.trim(),owner_member_id:owner,due_at:new Date(due+':00+08:00').toISOString()},timeoutSeconds)
      setRun(result.run);setProposal(result.proposal)
      void cache.invalidateQueries({queryKey:['crews',userId,crewId,'diane-proposals']})
    } catch(failure) {setError(failure)} finally {lock.current=false;setPending(false)}
  }
  async function decide(decision:'accept'|'reject') {
    if(lock.current||!proposal) return
    const command=frozen??{id:proposal.id,body:{expected_lock_version:proposal.lock_version,decision}}
    lock.current=true;setPending(true);setError(null)
    try {
      setProposal(await diane.decide(crewId,command.id,command.body));setFrozen(null)
      void cache.invalidateQueries({queryKey:['crews',userId,crewId]})
    } catch(failure) {
      setError(failure)
      if(!(failure instanceof AccountError)||failure.status>=500||['NETWORK_UNCONFIRMED','INVALID_RESPONSE'].includes(failure.code)) setFrozen(command)
    } finally {lock.current=false;setPending(false)}
  }
  async function reload() {
    setPending(true);setError(null)
    try {
      const result=await history.refetch()
      if(result.error) throw result.error
      const current=result.data!.items.find(item=>item.id===proposal?.id)
      if(current) {setProposal(current); if(current.status!=='pending') setFrozen(null)}
    } catch(failure) {setError(failure)} finally {setPending(false)}
  }
  return <section className="diane-proposals"><p>先拟定任务，审阅后再创建。每批最多 5 项，均使用下方选择的负责人和截止时间。</p>
    <ErrorNotice error={error} />
    {!proposal ? <form className="diane-question" onSubmit={event=>{event.preventDefault();void draft()}}><fieldset className="custody-fields" disabled={pending}>
      <label>希望安排什么？<Input.TextArea required maxLength={4000} rows={4} value={question} onChange={event=>setQuestion(event.target.value)} placeholder="例如：把拍摄前的收音设备准备拆成几项任务" /></label>
      <label>这批任务的负责人<select required value={owner} onChange={event=>setOwner(event.target.value)}><option value={selfMemberId}>我</option>{members.data?.items.filter(item=>item.id!==selfMemberId).map(item=><option key={item.id} value={item.id}>{item.display_name}</option>)}</select></label>
      {members.error && <p>成员目录暂不可读，可选择自己负责，或重新读取成员。</p>}
      {(members.data?.total??0)>20 && <div className="member-actions"><Button disabled={memberPage===1} onClick={()=>{setOwner(selfMemberId);setMemberPage(memberPage-1)}}>上一页成员</Button><Button disabled={memberPage*20>=members.data!.total} onClick={()=>{setOwner(selfMemberId);setMemberPage(memberPage+1)}}>下一页成员</Button></div>}
      <label>共同截止时间（北京时间）<Input type="datetime-local" required value={due} onChange={event=>setDue(event.target.value)} /></label>
      <Button htmlType="submit" type="primary" loading={pending}>拟定任务提案</Button>
    </fieldset></form> : <>
      <div className="section-heading"><h3>任务提案 · {names[proposal.status]}</h3><Button disabled={pending||!!frozen} onClick={()=>{setProposal(null);setRun(null)}}>拟定另一份</Button></div>
      <ol className="diane-proposal-items">{proposal.tasks.map((task,index)=><li key={index}><h4>{task.title}</h4>{task.description&&<p>{task.description}</p>}<p>负责人：{proposal.member_names[task.owner_member_id]}<br />截止：{date(task.due_at)}<br />{task.requires_review?'需要指定人验收':'由负责人直接完成'}</p>{proposal.created_task_ids[index]&&<Button onClick={()=>onTask(proposal.created_task_ids[index])}>查看已创建任务</Button>}</li>)}</ol>
      {proposal.status==='pending' && <><p>有效至 {date(proposal.expires_at)}。确认后将按上方内容创建任务。</p>{frozen&&<p role="alert">结果尚未确认。请读取提案结果，或使用原决定重试；不会重新生成提案。</p>}<div className="member-actions"><Button disabled={pending||!!frozen} onClick={()=>{void decide('reject')}}>放弃提案</Button><Button type="primary" aria-label={frozen?'用原决定重试':`确认创建 ${proposal.tasks.length} 项任务`} loading={pending} disabled={!frozen&&Date.parse(proposal.expires_at)<=Date.now()} onClick={()=>{void decide(frozen?.body.decision??'accept')}}>{frozen?'用原决定重试':`确认创建 ${proposal.tasks.length} 项任务`}</Button><Button disabled={pending} onClick={()=>{void reload()}}>读取提案结果</Button></div></>}
      {proposal.status==='completed' && <p role="status">已创建 {proposal.created_task_ids.length} 项任务。</p>}
    </>}
    {run&&<p className="caption">拟定耗时 {(run.usage.elapsed_ms/1000).toFixed(1)} 秒 · {run.usage.total_tokens===null?'token 用量未知':`${run.usage.total_tokens} tokens`}{!proposal&&` · ${run.answer}`}</p>}
    <details className="diane-usage"><summary>最近的提案</summary><ErrorNotice error={history.error} />{history.data?.items.map(item=><Button key={item.id} disabled={pending||!!frozen} onClick={()=>{setProposal(item);setRun(null);setError(null)}}>{date(item.created_at)} · {item.tasks[0].title} · {names[item.status]}</Button>)}</details>
  </section>
}
