import { useEffect, useRef, useState } from 'react'
import { Button, Input, Modal } from 'antd'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useAuth } from '../auth/AuthProvider'
import { ErrorNotice } from '../auth/AccountScreens'
import { AccountError, trimName } from '../api/accounts'
import type { Member } from '../api/crews'
import { taskStatusNames } from '../api/tasks'
import type { Task, TaskAction, TaskCommand, TaskDetail, TaskStatus, Submission } from '../api/tasks'
import { useBusinessSession } from '../crews/useBusinessSession'

const actionNames = { create: '创建任务', edit: '调整任务', start: '开始任务', submit: '提交成果', review: '验收任务', cancel: '取消任务' }
const reviewNames = { pending: '待确认', accepted: '已通过', rejected: '已退回', cancelled: '因任务取消关闭' }
const formatDate = (value: string) => new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value))
function localDeadline(value: string) {
  const parts = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).formatToParts(new Date(value))
  const part = (type: string) => parts.find(item => item.type === type)!.value
  return `${part('year')}-${part('month')}-${part('day')}T${part('hour')}:${part('minute')}`
}

function SubmissionRecord({ item }: { item: Submission }) {
  return <article className="task-submission"><h4>第 {item.submission_no} 次提交 · {reviewNames[item.review_status]}</h4><p className="task-text">{item.completion_note}</p>
    {item.review_note && <p className="task-text">确认说明：{item.review_note}</p>}<p className="caption">提交于 {formatDate(item.submitted_at)}{item.reviewed_at ? ` · 处理于 ${formatDate(item.reviewed_at)}` : ''}</p></article>
}

function TaskEditor({ crewId, initial, action, onClose, onSaved, onReload }: {
  crewId: string; initial?: TaskDetail; action: 'create' | TaskAction; onClose: () => void; onSaved: (id: string) => Promise<void>; onReload?: () => Promise<TaskDetail>
}) {
  const { tasks, crews, state } = useAuth()
  const [current, setCurrent] = useState(initial)
  const [title, setTitle] = useState(initial?.task.title ?? '')
  const [description, setDescription] = useState(initial?.task.description ?? '')
  const [owner, setOwner] = useState(initial?.task.owner_member_id ?? '')
  const [reviewer, setReviewer] = useState(initial?.task.reviewer_member_id ?? '')
  const [collaborators, setCollaborators] = useState(initial?.task.collaborator_member_ids ?? [])
  const [due, setDue] = useState(initial ? localDeadline(initial.task.due_at) : '')
  const [note, setNote] = useState('')
  const [decision, setDecision] = useState<'accept' | 'reject'>('accept')
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [frozen, setFrozen] = useState<TaskCommand | null>(null)
  const [needsReload, setNeedsReload] = useState(false)
  const [peoplePage, setPeoplePage] = useState(1)
  const [people, setPeople] = useState<Member[]>([])
  const lock = useRef(false)
  const editing = action === 'create' || action === 'edit'
  const catalog = useQuery({ queryKey: ['crews', state.status === 'authenticated' ? state.user.id : null, crewId, 'task-assignees', peoplePage], queryFn: ({ signal }) => crews.members(crewId, peoplePage, signal), enabled: editing, retry: false, gcTime: 0 })
  useEffect(() => { if (catalog.data) setPeople(previous => [...new Map([...previous, ...catalog.data.items].map(member => [member.id, member])).values()]) }, [catalog.data])
  useBusinessSession(error ?? catalog.error)
  const disabled = pending || frozen !== null || needsReload
  const canAct = action === 'create' || !!current?.task.allowed_actions.includes(action)

  function buildCommand(): TaskCommand {
    let body: Record<string, unknown> = { expected_lock_version: current?.task.lock_version }
    if (editing) {
      const cleanTitle = trimName(title); const cleanDescription = trimName(description.replace(/\r\n/g, '\n')) || null
      if (!cleanTitle || Array.from(cleanTitle).length > 100 || !owner || !reviewer || !due || Array.from(cleanDescription ?? '').length > 3000 || collaborators.length > 20) throw new AccountError('VALIDATION_ERROR')
      const values: Record<string, unknown> = { title: cleanTitle, description: cleanDescription, owner_member_id: owner, reviewer_member_id: reviewer, collaborator_member_ids: [...collaborators].sort() }
      if (action === 'create' || due !== localDeadline(current!.task.due_at)) {
        const date = new Date(`${due}+08:00`)
        if (!Number.isFinite(date.getTime()) || date.getTime() <= Date.now()) throw new AccountError('VALIDATION_ERROR')
        values.due_at = date.toISOString()
      }
      if (action === 'create') body = values
      else {
        const original = current!.task
        for (const [key, value] of Object.entries(values)) {
          if (JSON.stringify(value) !== JSON.stringify(original[key as keyof Task])) body[key] = value
        }
        if (Object.keys(body).length === 1) throw new AccountError('VALIDATION_ERROR')
      }
    } else if (action === 'submit') body.completion_note = trimName(note)
    else if (action === 'cancel') body.reason = trimName(note)
    else if (action === 'review') {
      if (!current?.current_submission || current.current_submission.review_status !== 'pending') throw new AccountError('INVALID_STATE')
      body = { ...body, submission_id: current.current_submission.id, decision, review_note: trimName(note) || null }
    }
    if (action === 'submit' && (!trimName(note) || Array.from(trimName(note)).length > 2000)
      || action === 'cancel' && (!trimName(note) || Array.from(trimName(note)).length > 500)
      || action === 'review' && (decision === 'reject' && !trimName(note) || Array.from(trimName(note)).length > 500)) throw new AccountError('VALIDATION_ERROR')
    return { action, taskId: current?.task.id, body, ...(['create', 'submit', 'review', 'cancel'].includes(action) ? { key: crypto.randomUUID() } : {}) }
  }

  async function submit() {
    if (lock.current || needsReload || !canAct && !frozen) return
    lock.current = true; setPending(true); setError(null)
    let command: TaskCommand | undefined
    try {
      command = frozen ?? buildCommand()
      const result = await tasks.execute(crewId, command)
      onClose()
      try { await onSaved(result.id) } catch { /* The mutation is confirmed; queries surface reload failures. */ }
    } catch (failure) {
      setError(failure)
      const uncertain = !(failure instanceof AccountError) || ['NETWORK_UNCONFIRMED', 'INVALID_RESPONSE', 'SERVICE_UNAVAILABLE'].includes(failure.code) || failure.status >= 500
      if (uncertain && command?.key) setFrozen(command)
      else {
        if (failure instanceof AccountError && ['VERSION_CONFLICT', 'INVALID_STATE'].includes(failure.code)) setFrozen(null)
        if (uncertain || failure instanceof AccountError && ['VERSION_CONFLICT', 'INVALID_STATE', 'MEMBER_INACTIVE'].includes(failure.code)) setNeedsReload(true)
      }
    } finally { lock.current = false; setPending(false) }
  }

  async function reload() {
    if (lock.current || !onReload) return
    lock.current = true; setPending(true); setError(null)
    try { setCurrent(await onReload()); setNeedsReload(false) }
    catch (failure) { setError(failure) }
    finally { lock.current = false; setPending(false) }
  }

  const selectOptions = (selected: string[]) => <><option value="">选择在组成员</option>{[...new Set([...people.map(member => member.id), ...selected.filter(Boolean)])].map(id => <option key={id} value={id}>{people.find(member => member.id === id)?.display_name ?? current?.member_names[id] ?? '当前成员'} · {id.slice(0, 8)}</option>)}</>
  return <Modal title={actionNames[action]} open footer={null} onCancel={onClose} closable={!pending} keyboard={!pending} mask={{ closable: !pending }} width={640}>
    <form className="account-form task-form" noValidate onSubmit={event => { event.preventDefault(); void submit() }}>
      {current && <p className="caption">{current.task.title} · {taskStatusNames[current.task.status]} · 版本 {current.task.lock_version}</p>}
      {editing ? <>
        <div className="account-field"><label htmlFor="task-title">任务标题</label><Input id="task-title" value={title} disabled={disabled} onChange={event => setTitle(event.target.value)} maxLength={400} /></div>
        <div className="account-field"><label htmlFor="task-description">任务说明（选填）</label><Input.TextArea id="task-description" value={description} disabled={disabled} onChange={event => setDescription(event.target.value)} rows={3} maxLength={12000} /></div>
        <ErrorNotice error={catalog.error} />
        <div className="task-form-grid"><div className="account-field"><label htmlFor="task-owner">负责人</label><select id="task-owner" value={owner} disabled={disabled} onChange={event => setOwner(event.target.value)}>{selectOptions([owner])}</select></div>
          <div className="account-field"><label htmlFor="task-reviewer">确认人</label><select id="task-reviewer" value={reviewer} disabled={disabled} onChange={event => setReviewer(event.target.value)}>{selectOptions([reviewer])}</select></div></div>
        <div className="account-field"><label htmlFor="task-collaborators">协作人（选填，最多20人）</label><select id="task-collaborators" multiple size={4} value={collaborators} disabled={disabled} onChange={event => setCollaborators(Array.from(event.target.selectedOptions, option => option.value).filter(Boolean))}>{selectOptions(collaborators)}</select></div>
        {catalog.data && peoplePage * 20 < catalog.data.total && <Button disabled={disabled || catalog.isFetching} onClick={() => setPeoplePage(peoplePage + 1)}>加载更多成员</Button>}
        <div className="account-field"><label htmlFor="task-due">截止时间（北京时间）</label><input id="task-due" type="datetime-local" value={due} disabled={disabled} onChange={event => setDue(event.target.value)} /></div>
        <p className="caption">负责人提交、确认人验收。协作人不能代替二者，场戏关联暂未接入。</p>
      </> : <>
        {action === 'start' ? <p>确认开始处理这项任务？</p> : <>
          {action === 'review' && <><SubmissionRecord item={current!.current_submission!} /><div className="account-field"><label htmlFor="task-decision">验收决定</label><select id="task-decision" value={decision} disabled={disabled} onChange={event => setDecision(event.target.value as 'accept' | 'reject')}><option value="accept">通过</option><option value="reject">退回修改</option></select></div></>}
          <div className="account-field"><label htmlFor="task-note">{action === 'submit' ? '完成说明' : action === 'cancel' ? '取消原因' : '验收说明（退回时必填）'}</label><Input.TextArea id="task-note" rows={4} value={note} disabled={disabled} onChange={event => setNote(event.target.value)} maxLength={action === 'submit' ? 8000 : 2000} /></div>
          {action === 'cancel' && <p className="caption">取消会关闭当前待确认提交，保留历史。已完成、已取消任务不能再次取消。</p>}
        </>}
      </>}
      <ErrorNotice error={error} />
      {frozen && <p className="account-note">结果尚未确认。输入已冻结；可明确恢复这次操作，沿用原操作标识和原内容，不会创建第二次变更。关闭或刷新后请先核对任务记录。</p>}
      {needsReload && <Button onClick={() => { void reload() }} loading={pending} disabled={!onReload}>重新读取任务状态</Button>}
      {!canAct && !frozen && <p className="account-note">当前已不能执行此操作，请关闭并核对任务。</p>}
      <div className="role-actions"><Button onClick={onClose} disabled={pending}>关闭</Button><Button htmlType="submit" type="primary" aria-label={frozen ? '恢复这次操作结果' : `确认${actionNames[action]}`} loading={pending} disabled={needsReload || !canAct && !frozen}>{frozen ? '恢复这次操作结果' : `确认${actionNames[action]}`}</Button></div>
    </form>
  </Modal>
}

function TaskDetails({ crewId, userId, taskId, onClose, onChanged }: { crewId: string; userId: string; taskId: string; onClose: () => void; onChanged: () => Promise<void> }) {
  const { tasks } = useAuth()
  const [action, setAction] = useState<TaskAction | null>(null)
  const [historyPage, setHistoryPage] = useState<number | null>(null)
  const detail = useQuery({ queryKey: ['crews', userId, crewId, 'tasks', 'detail', taskId], queryFn: ({ signal }) => tasks.get(crewId, taskId, signal), retry: false })
  const history = useQuery({ queryKey: ['crews', userId, crewId, 'tasks', 'history', taskId, historyPage], queryFn: ({ signal }) => tasks.history(crewId, taskId, historyPage!, signal), enabled: historyPage !== null && !detail.isError, retry: false })
  useBusinessSession(detail.error ?? history.error)
  const data = detail.isError ? undefined : detail.data
  return <><Modal title="任务详情" open footer={null} onCancel={onClose} width={700}>
    <ErrorNotice error={detail.error} />{detail.isPending && <p role="status">正在读取任务…</p>}
    <Button loading={detail.isFetching} onClick={() => { void detail.refetch() }}>刷新任务详情</Button>
    {data && <div className="task-detail"><h3>{data.task.title}</h3><p>{taskStatusNames[data.task.status]} · 截止 {formatDate(data.task.due_at)}（北京时间）</p>
      <p className="task-text">{data.task.description || '未填写任务说明'}</p><p className="caption">负责人：{data.member_names[data.task.owner_member_id] ?? '成员资料暂不可用'} · 确认人：{data.member_names[data.task.reviewer_member_id] ?? '成员资料暂不可用'}</p>
      {data.task.cancel_reason && <p className="task-text">取消原因：{data.task.cancel_reason}</p>}
      {data.current_submission && <SubmissionRecord item={data.current_submission} />}
      <div className="member-actions">{data.task.allowed_actions.map(item => <Button key={item} onClick={() => setAction(item)}>{actionNames[item]}</Button>)}</div>
      <p className="caption">已有 {data.submission_count} 次提交。任务通知可在顶部通知入口查看，知悉不等于完成或验收。</p>
      {data.submission_count > 0 && <Button onClick={() => setHistoryPage(1)}>查看历次提交</Button>}
    </div>}
    {data && historyPage !== null && <section><h3>提交历史</h3><ErrorNotice error={history.error} />{!history.isError && history.data && <>
      {history.data.items.map(item => <SubmissionRecord key={item.id} item={item} />)}<div className="list-pagination"><Button disabled={historyPage === 1 || history.isFetching} onClick={() => setHistoryPage(historyPage - 1)}>较新提交</Button><span>第 {historyPage} 页</span><Button disabled={historyPage * 20 >= history.data.total || history.isFetching} onClick={() => setHistoryPage(historyPage + 1)}>较早提交</Button></div>
    </>}</section>}
  </Modal>{data && action && <TaskEditor key={`${taskId}-${action}`} crewId={crewId} initial={data} action={action} onClose={() => setAction(null)} onReload={async () => {
    const fresh = await detail.refetch(); if (fresh.isError) throw fresh.error; return fresh.data!
  }} onSaved={async () => { await onChanged(); await detail.refetch(); if (historyPage !== null) await history.refetch() }} />}</>
}

export function TaskBoard({ crewId, userId, canManage, openTaskId, onTaskOpened }: { crewId: string; userId: string; canManage: boolean; openTaskId?: string | null; onTaskOpened?: () => void }) {
  const { tasks } = useAuth()
  const cache = useQueryClient()
  const [page, setPage] = useState(1)
  const [status, setStatus] = useState<TaskStatus | ''>('')
  const [creating, setCreating] = useState(false)
  const [selected, setSelected] = useState<string | null>(null)
  useEffect(() => { if (openTaskId) { setSelected(openTaskId); onTaskOpened?.() } }, [openTaskId, onTaskOpened])
  const list = useQuery({ queryKey: ['crews', userId, crewId, 'tasks', 'list', page, status], queryFn: ({ signal }) => tasks.list(crewId, page, status, signal), retry: false })
  useBusinessSession(list.error)
  const refresh = async () => { await cache.invalidateQueries({ queryKey: ['crews', userId, crewId, 'tasks'] }) }
  return <section className="task-board"><div className="section-heading"><h3>筹备任务</h3><div className="member-actions"><Button onClick={() => { setStatus(''); setPage(1) }}>全部任务</Button>{canManage && <Button type="primary" onClick={() => setCreating(true)}>创建任务</Button>}</div></div>
    <div className="task-filters"><label htmlFor="task-filter">任务状态</label><select id="task-filter" value={status} onChange={event => { setStatus(event.target.value as TaskStatus | ''); setPage(1) }}><option value="">全部状态</option>{Object.entries(taskStatusNames).map(([code, label]) => <option key={code} value={code}>{label}</option>)}</select><Button loading={list.isFetching} onClick={() => { void list.refetch() }}>刷新任务</Button></div>
    <ErrorNotice error={list.error} />{list.isPending && <p role="status">正在读取任务列表…</p>}
    {!list.isError && list.data && <>{list.data.items.length === 0 ? <p className="module-note">当前范围内没有任务。</p> : <ul className="task-list">{list.data.items.map(task => <li key={task.id}><button type="button" onClick={() => setSelected(task.id)}><strong>{task.title}</strong><span>{taskStatusNames[task.status]} · 截止 {formatDate(task.due_at)}</span></button></li>)}</ul>}
      <div className="list-pagination"><Button disabled={page === 1 || list.isFetching} onClick={() => setPage(page - 1)}>上一页任务</Button><span>第 {page} 页 · 共 {list.data.total} 项可见任务</span><Button disabled={page * 20 >= list.data.total || list.isFetching} onClick={() => setPage(page + 1)}>下一页任务</Button></div></>}
    {creating && <TaskEditor crewId={crewId} action="create" onClose={() => setCreating(false)} onSaved={async id => { await refresh(); setSelected(id) }} />}
    {selected && <TaskDetails key={selected} crewId={crewId} userId={userId} taskId={selected} onClose={() => setSelected(null)} onChanged={refresh} />}
  </section>
}
