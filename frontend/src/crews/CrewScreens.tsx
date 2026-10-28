import { useEffect, useRef, useState } from 'react'
import { Button, Input, Modal } from 'antd'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useAuth } from '../auth/AuthProvider'
import { ErrorNotice } from '../auth/AccountScreens'
import { AccountError, trimName } from '../api/accounts'
import { roleNames } from '../api/crews'
import type { Member } from '../api/crews'
import { RoleEditor } from './RoleEditor'
import { useBusinessSession } from './useBusinessSession'
import { InvitationManager } from './InvitationScreens'
import { MemberExitDialog } from './MemberExitDialog'
export { useBusinessSession } from './useBusinessSession'

export function CrewPicker({ open, userId, onClose, onSelect }: { open: boolean; userId: string; onClose: () => void; onSelect: (id: string) => void }) {
  const { crews } = useAuth()
  const cache = useQueryClient()
  const [number, setNumber] = useState(1)
  const [creating, setCreating] = useState(false)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [uncertain, setUncertain] = useState(false)
  const [name, setName] = useState('')
  const [project, setProject] = useState('')
  const [description, setDescription] = useState('')
  const lock = useRef(false)
  const list = useQuery({ queryKey: ['crews', userId, 'list', number], queryFn: ({ signal }) => crews.list(number, signal), enabled: open && !creating, retry: false })
  useBusinessSession(error ?? list.error)
  const validText = (value: string, max: number) => Array.from(trimName(value)).length >= 1 && Array.from(trimName(value)).length <= max && !/[\p{Cc}\p{Cs}]/u.test(value)

  return <Modal title={creating ? '创建剧组' : '选择剧组'} open={open} onCancel={onClose} footer={null}
    closable={!pending} keyboard={!pending} mask={{ closable: !pending }} width={600}>
    {creating ? <form className="account-form crew-create" noValidate onSubmit={event => {
      event.preventDefault()
      if (lock.current || uncertain) return
      lock.current = true; setPending(true); setError(null)
      void (async () => {
        try {
          const notes = trimName(description.replace(/\r\n/g, '\n'))
          if (!validText(name, 50) || !validText(project, 100) || Array.from(notes).length > 1000 || /[\p{Cs}]/u.test(notes) || /[\u0000-\u0008\u000b-\u001f\u007f-\u009f]/u.test(notes)) throw new AccountError('VALIDATION_ERROR')
          const result = await crews.create({ name: trimName(name), project_name: trimName(project), description: notes || null })
          await cache.invalidateQueries({ queryKey: ['crews', userId, 'list'] })
          setName(''); setProject(''); setDescription(''); setCreating(false)
          onSelect(result.id); onClose()
        } catch (failure) {
          setError(failure)
          if (!(failure instanceof AccountError) || ['NETWORK_UNCONFIRMED', 'INVALID_RESPONSE', 'SERVICE_UNAVAILABLE'].includes(failure.code) || failure.status >= 500) setUncertain(true)
        } finally { lock.current = false; setPending(false) }
      })()
    }}>
      <p className="caption">创建后你将成为管理员。导演、制片等业务岗位后续单独分配；同名剧组允许存在。</p>
      <div className="account-field"><label htmlFor="crew-name">剧组名称</label><Input id="crew-name" value={name} onChange={event => setName(event.target.value)} disabled={pending} maxLength={200} required /><span className="caption">1—50个字符</span></div>
      <div className="account-field"><label htmlFor="crew-project">影片名称</label><Input id="crew-project" value={project} onChange={event => setProject(event.target.value)} disabled={pending} maxLength={400} required /><span className="caption">1—100个字符</span></div>
      <div className="account-field"><label htmlFor="crew-description">剧组说明（选填）</label><Input.TextArea id="crew-description" value={description} onChange={event => setDescription(event.target.value)} disabled={pending} rows={3} maxLength={4000} /><span className="caption">最多1000个字符，不填写也可创建</span></div>
      <ErrorNotice error={error} />
      {uncertain && <p role="status">创建结果未确认，请先查看剧组列表，避免重复创建。</p>}
      <Button type="primary" htmlType="submit" loading={pending} aria-label="确认创建剧组" aria-busy={pending} disabled={uncertain}>确认创建剧组</Button>
      <Button disabled={pending} onClick={() => { setCreating(false); setError(null); void list.refetch() }}>{uncertain ? '查看剧组列表' : '返回剧组列表'}</Button>
    </form> : <div className="crew-picker">
      <ErrorNotice error={list.error} />
      {list.isPending && !list.isError && <p role="status">正在读取你的剧组…</p>}
      {!list.isError && list.data && <>
        {list.data.items.length === 0 ? <p>你还没有加入剧组。可以创建自己的剧组，或打开邀请邮件中的链接。</p>
          : <ul className="crew-list">{list.data.items.map(crew => <li key={crew.id}><button type="button" aria-label={`${crew.name} · ${crew.project_name}`} onClick={() => { onSelect(crew.id); onClose() }}><strong>{crew.name}</strong><span>{crew.project_name}</span></button></li>)}</ul>}
        <div className="list-pagination"><Button disabled={number === 1 || list.isFetching} onClick={() => setNumber(number - 1)}>上一页</Button><span>第 {number} 页 · 共 {list.data.total} 个剧组</span><Button disabled={number * 20 >= list.data.total || list.isFetching} onClick={() => setNumber(number + 1)}>下一页</Button></div>
      </>}
      <div className="crew-picker-actions"><Button aria-label="刷新剧组列表" loading={list.isFetching} onClick={() => { void list.refetch() }}>刷新列表</Button><Button type="primary" aria-label="创建新剧组" onClick={() => { setUncertain(false); setError(null); setCreating(true) }}>创建新剧组</Button></div>
    </div>}
  </Modal>
}

export function MemberDirectory({ userId, crewId, selfMemberId, canAuthorize = false, canInvite = false, canRemove = false, onAccessChanged, onOpenTask, onOpenResource }: {
  userId: string; crewId: string; selfMemberId: string; canAuthorize?: boolean; canInvite?: boolean; canRemove?: boolean; onAccessChanged: () => Promise<void>
  onOpenTask?: (id: string) => void
  onOpenResource?: (id: string) => void
}) {
  const { crews } = useAuth()
  const [number, setNumber] = useState(1)
  const [editing, setEditing] = useState<Member | null>(null)
  const [removing, setRemoving] = useState<Member | null>(null)
  const cache = useQueryClient()
  const members = useQuery({ queryKey: ['crews', userId, crewId, 'members', number], queryFn: ({ signal }) => crews.members(crewId, number, signal), retry: false })
  useBusinessSession(members.error)
  useEffect(() => { if (!canAuthorize) setEditing(null) }, [canAuthorize])
  useEffect(() => { if (!canRemove) setRemoving(null) }, [canRemove])
  return <div className="member-directory">
    <div className="section-heading"><h2>在组成员</h2><div className="member-actions">{canInvite && <InvitationManager crewId={crewId} userId={userId} />}<Button loading={members.isFetching} onClick={() => { void members.refetch(); void onAccessChanged() }}>刷新成员</Button></div></div>
    <ErrorNotice error={members.error} />
    {members.isPending && <p role="status">正在读取成员…</p>}
    {!members.isError && members.data && <>
      <div className="directory-scroll"><table><caption className="caption">仅展示当前剧组的在组成员及岗位</caption><thead><tr><th scope="col">成员</th><th scope="col">岗位</th><th scope="col">状态</th>{(canAuthorize || canRemove) && <th scope="col">操作</th>}</tr></thead><tbody>{members.data.items.map(member => <tr key={member.id}><td>{member.display_name}</td><td>{member.role_codes.map(code => roleNames[code]).join(' / ') || '尚未分配岗位'}</td><td>在组</td>{(canAuthorize || canRemove) && <td>{canAuthorize && <Button aria-label={`管理${member.display_name}的岗位`} onClick={() => setEditing(member)}>管理岗位</Button>}{canRemove && member.id !== selfMemberId && <Button aria-label={`移除${member.display_name}`} onClick={() => setRemoving(member)}>移除成员</Button>}</td>}</tr>)}</tbody></table></div>
      <div className="list-pagination"><Button disabled={number === 1 || members.isFetching} onClick={() => setNumber(number - 1)}>上一页</Button><span>第 {number} 页 · 共 {members.data.total} 人</span><Button disabled={number * 20 >= members.data.total || members.isFetching} onClick={() => setNumber(number + 1)}>下一页</Button></div>
    </>}
    <p className="module-note">{canAuthorize ? '岗位变更先核对差异，再明确提交。' : '岗位由有管理权限的成员分配。'}受邀者接受后加入，个人档期由本人维护。</p>
    {removing && canRemove && <MemberExitDialog key={removing.id} crewId={crewId} member={removing} removing onClose={() => setRemoving(null)} onOpenTask={onOpenTask} onOpenResource={onOpenResource}
      onReload={async () => {
        const fresh = await members.refetch()
        await onAccessChanged()
        if (fresh.isError) throw fresh.error
        const found = fresh.data?.items.find(item => item.id === removing.id)
        if (!found) throw new AccountError('MEMBER_LIST_CHANGED')
        return found
      }} onSaved={async () => {
        await cache.cancelQueries({ queryKey: ['crews', userId, crewId] })
        await cache.invalidateQueries({ queryKey: ['crews', userId] })
        await onAccessChanged()
      }} />}
    {editing && canAuthorize && <RoleEditor key={editing.id} crewId={crewId} member={editing} onClose={() => setEditing(null)}
      onReload={async () => {
        const fresh = await members.refetch()
        if (fresh.isError) throw fresh.error
        const member = fresh.data?.items.find(item => item.id === editing.id)
        if (!member) throw new AccountError('MEMBER_LIST_CHANGED')
        return member
      }} onSaved={async () => {
        await cache.invalidateQueries({ queryKey: ['crews', userId] })
        await onAccessChanged()
      }} />}
  </div>
}
