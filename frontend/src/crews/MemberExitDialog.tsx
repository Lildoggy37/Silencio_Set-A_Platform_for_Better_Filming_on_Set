import { useRef, useState } from 'react'
import { Button, Input, Modal } from 'antd'
import { useQuery } from '@tanstack/react-query'
import { useAuth } from '../auth/AuthProvider'
import { ErrorNotice } from '../auth/AccountScreens'
import { AccountError } from '../api/accounts'
import type { Member } from '../api/crews'
import { useBusinessSession } from './useBusinessSession'
import { taskStatusNames } from '../api/tasks'

export function MemberExitDialog({ crewId, member, removing, onClose, onReload, onSaved, onOpenTask, onOpenResource }: {
  crewId: string; member: Member; removing: boolean; onClose: () => void
  onReload: () => Promise<Member>; onSaved: () => Promise<void>
  onOpenTask?: (id: string) => void
  onOpenResource?: (id: string) => void
}) {
  const { crews, resources, state } = useAuth()
  const [current, setCurrent] = useState(member)
  const [reason, setReason] = useState('')
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [needsReload, setNeedsReload] = useState(false)
  const [page, setPage] = useState(1)
  const [resourcePage, setResourcePage] = useState(1)
  const responsibilities = useQuery({ queryKey: ['crews', state.status === 'authenticated' ? state.user.id : null, crewId, 'exit-responsibilities', member.id, page],
    queryFn: ({ signal }) => crews.responsibilities(crewId, member.id, page, signal), retry: false, gcTime: 0 })
  const resourceChecks = useQuery({ queryKey: ['crews', state.status === 'authenticated' ? state.user.id : null, crewId, 'resource-responsibilities', member.id, resourcePage],
    queryFn: ({ signal }) => resources.responsibilities(crewId, member.id, resourcePage, signal), retry: false, gcTime: 0 })
  const hasBlockers = responsibilities.isError || responsibilities.isPending || responsibilities.isFetching || (responsibilities.data?.blocking_total ?? 1) > 0
    || resourceChecks.isError || resourceChecks.isPending || resourceChecks.isFetching || (resourceChecks.data?.blocking_total ?? 1) > 0
  const lock = useRef(false)
  useBusinessSession(error ?? responsibilities.error ?? resourceChecks.error)
  const reasonValid = Array.from(reason.trim()).length >= 1 && Array.from(reason.trim()).length <= 500 && !/[\p{Cc}\p{Cs}]/u.test(reason.trim())

  async function submit() {
    if (lock.current || needsReload || hasBlockers || removing && !reasonValid) return
    lock.current = true; setPending(true); setError(null)
    try {
      if (removing) await crews.remove(crewId, current.id, current.lock_version, reason)
      else await crews.leave(crewId, current.id, current.lock_version)
      onClose()
      try { await onSaved() } catch { /* Confirmed write; refresh failures must not cause a replay. */ }
    } catch (failure) {
      setError(failure)
      if (failure instanceof AccountError && failure.code === 'MEMBER_HAS_RESPONSIBILITIES') void responsibilities.refetch()
      if (failure instanceof AccountError && failure.code === 'MEMBER_HAS_RESOURCE_RESPONSIBILITIES') void resourceChecks.refetch()
      if (!(failure instanceof AccountError) || ['VERSION_CONFLICT', 'NETWORK_UNCONFIRMED', 'INVALID_RESPONSE', 'SERVICE_UNAVAILABLE', 'MEMBER_INACTIVE'].includes(failure.code) || failure.status >= 500) setNeedsReload(true)
    } finally { lock.current = false; setPending(false) }
  }

  async function reload() {
    if (lock.current) return
    lock.current = true; setPending(true); setError(null)
    try {
      setCurrent(await onReload())
      const checks = await responsibilities.refetch()
      if (checks.isError) throw checks.error
      const resourceResult = await resourceChecks.refetch()
      if (resourceResult.isError) throw resourceResult.error
      setNeedsReload(false)
    }
    catch (failure) { setError(failure) }
    finally { lock.current = false; setPending(false) }
  }

  return <Modal title={removing ? `移除成员 · ${current.display_name}` : '退出当前剧组'} open footer={null} onCancel={onClose}
    closable={!pending} keyboard={!pending} mask={{ closable: !pending }} width={560}>
    <div className="role-editor">
      <p>{removing ? `确认移除 ${current.display_name}？` : '确认离开当前剧组？'}操作成功后立即停止剧组访问与操作权限，历史记录保留。再次加入需要新的邀请和授权。</p>
      {current.role_codes.includes('admin') && <p className="account-note">剧组必须保留至少一名有效管理员，请先确认管理员交接。</p>}
      <section aria-label="任务交接检查"><h3>任务交接</h3><ErrorNotice error={responsibilities.error} />
        {responsibilities.isFetching && <p role="status">正在检查未完成任务…</p>}
        {!responsibilities.isError && responsibilities.data && <>
          {responsibilities.data.blocking_total === 0 ? <p>当前没有待交接的任务责任。退出时仍会重新检查。</p> : <p>还有 {responsibilities.data.blocking_total} 项未完成任务责任，需要先转交、完成或取消。</p>}
          {responsibilities.data.items.length > 0 && <ul className="task-list">{responsibilities.data.items.map(item => <li key={item.id}><strong>{item.title}</strong><p>{taskStatusNames[item.status]} · {item.is_owner && '负责人'}{item.is_owner && item.is_reviewer && ' / '}{item.is_reviewer && '确认人'}</p>
            {onOpenTask && <Button disabled={pending} onClick={() => { onClose(); onOpenTask(item.id) }}>处理任务：{item.title}</Button>}</li>)}</ul>}
          {responsibilities.data.restricted_total > 0 && <p className="account-note">其中 {responsibilities.data.restricted_total} 项任务超出你的查看范围，请联系有权管理任务的成员处理。</p>}
          {responsibilities.data.total > 20 && <div className="list-pagination"><Button disabled={page === 1 || responsibilities.isFetching} onClick={() => setPage(page - 1)}>上一页责任</Button><span>第 {page} 页</span><Button disabled={page * 20 >= responsibilities.data.total || responsibilities.isFetching} onClick={() => setPage(page + 1)}>下一页责任</Button></div>}
        </>}
        <Button aria-label="重新检查任务交接" disabled={pending} loading={responsibilities.isFetching} onClick={() => { void responsibilities.refetch() }}>重新检查任务交接</Button>
      </section>
      <section aria-label="资源交接检查"><h3>资源交接</h3><ErrorNotice error={resourceChecks.error} />
        {resourceChecks.isFetching && <p role="status">正在检查资源与预约责任…</p>}
        {!resourceChecks.isError && resourceChecks.data && <>
          <p>{resourceChecks.data.blocking_total === 0 ? '当前没有待交接的资源或未结束预约。退出时仍会重新检查。' : `还有 ${resourceChecks.data.blocking_total} 项资源或未结束预约责任，需要先转交资源或处理预约。`}</p>
          {resourceChecks.data.items.length > 0 && <ul className="task-list">{resourceChecks.data.items.map(item => <li key={`${item.kind}-${item.id}`}><strong>{item.title}</strong><p>{item.kind === 'resource' ? '资源负责人' : '未结束的预约申请'}</p>{onOpenResource && <Button disabled={pending} onClick={() => { onClose(); onOpenResource(item.resource_id) }}>处理资源：{item.title}</Button>}</li>)}</ul>}
          {resourceChecks.data.restricted_total > 0 && <p className="account-note">其中 {resourceChecks.data.restricted_total} 项责任超出你的查看范围，请联系有权处理的成员。</p>}
          {resourceChecks.data.total > 20 && <div className="list-pagination"><Button disabled={resourcePage === 1 || resourceChecks.isFetching} onClick={() => setResourcePage(resourcePage - 1)}>上一页资源责任</Button><span>第 {resourcePage} 页</span><Button disabled={resourcePage * 20 >= resourceChecks.data.total || resourceChecks.isFetching} onClick={() => setResourcePage(resourcePage + 1)}>下一页资源责任</Button></div>}
        </>}
        <Button aria-label="重新检查资源交接" disabled={pending} loading={resourceChecks.isFetching} onClick={() => { void resourceChecks.refetch() }}>重新检查资源交接</Button>
      </section>
      {removing && <div className="account-field"><label htmlFor="member-remove-reason">移除原因</label><Input.TextArea id="member-remove-reason" rows={3} maxLength={2000} value={reason} disabled={pending} onChange={event => setReason(event.target.value)} /><span className="caption">1—500个字符，原因仅记录在内部审计中。</span></div>}
      <ErrorNotice error={error} />
      {needsReload && <div className="account-note"><p>结果尚未确认或成员资料已变化。先读取当前状态，再决定是否提交；不会自动重试。</p><Button loading={pending} onClick={() => { void reload() }}>读取当前成员状态</Button></div>}
      <div className="role-actions"><Button onClick={onClose} disabled={pending}>取消</Button><Button type="primary" aria-label={removing ? '确认移除成员' : '确认退出剧组'} aria-busy={pending} loading={pending} disabled={needsReload || hasBlockers || removing && !reasonValid}
        onClick={() => { void submit() }}>{removing ? '确认移除成员' : '确认退出剧组'}</Button></div>
    </div>
  </Modal>
}
