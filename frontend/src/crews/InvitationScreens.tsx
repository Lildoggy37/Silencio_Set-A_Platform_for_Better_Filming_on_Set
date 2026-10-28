import { useRef, useState } from 'react'
import { Button, Input, Modal } from 'antd'
import { useQuery } from '@tanstack/react-query'
import { useAuth } from '../auth/AuthProvider'
import { AccountError } from '../api/accounts'
import { AccountFrame, ErrorNotice } from '../auth/AccountScreens'
import type { Invitation } from '../api/crews'
import { useBusinessSession } from './useBusinessSession'

export function InvitationAcceptance({ token, email, onJoined, onLater }: { token: string; email: string; onJoined: (id: string) => void; onLater: () => void }) {
  const { crews, logout } = useAuth()
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [uncertain, setUncertain] = useState(false)
  const lock = useRef(false)
  useBusinessSession(error)
  async function run(action: () => Promise<void>) {
    if (lock.current) return
    lock.current = true; setPending(true); setError(null)
    try { await action() } catch (failure) {
      setError(failure)
      if (!(failure instanceof AccountError) || ['NETWORK_UNCONFIRMED', 'INVALID_RESPONSE', 'SERVICE_UNAVAILABLE'].includes(failure.code) || failure.status >= 500) setUncertain(true)
    } finally { lock.current = false; setPending(false) }
  }
  return <AccountFrame title="接受剧组邀请" subtitle="请核对当前邮箱后明确确认。加入不会自动获得导演、制片或管理员岗位。">
    <p className="account-email">当前邮箱：{email}</p><div className="account-form"><ErrorNotice error={error} />
      {uncertain && <p className="account-note">加入结果未确认。可明确重试以确认同一邀请结果，不会重复创建成员关系。</p>}
      <Button type="primary" aria-label={uncertain ? '重新确认加入结果' : '确认加入剧组'} aria-busy={pending} loading={pending} onClick={() => { void run(async () => { const joined = await crews.acceptInvitation(token); onJoined(joined.crewId) }) }}>{uncertain ? '重新确认加入结果' : '确认加入剧组'}</Button>
      <Button disabled={pending} onClick={() => { void run(logout) }}>切换账号</Button>
      <Button type="text" disabled={pending} onClick={onLater}>稍后处理，返回工作台</Button><p className="caption">稍后可重新打开原邮件继续。刷新页面会清除当前邀请进度。</p>
    </div>
  </AccountFrame>
}

export function InvitationManager({ crewId, userId }: { crewId: string; userId: string }) {
  const { crews } = useAuth()
  const [open, setOpen] = useState(false)
  const [number, setNumber] = useState(1)
  const [email, setEmail] = useState('')
  const [error, setError] = useState<unknown>(null)
  const [notice, setNotice] = useState('')
  const [pending, setPending] = useState(false)
  const [uncertain, setUncertain] = useState(false)
  const [revoking, setRevoking] = useState<Invitation | null>(null)
  const lock = useRef(false)
  const records = useQuery({ queryKey: ['crews', userId, crewId, 'invitations', number], queryFn: ({ signal }) => crews.invitations(crewId, number, signal), enabled: open, retry: false })
  useBusinessSession(error ?? records.error)
  const status = { pending: '待接受', accepted: '已接受', revoked: '已撤销', expired: '已过期' }
  async function refresh() {
    const result = await records.refetch()
    if (!result.isError) { setUncertain(false); setRevoking(null); setError(null) }
  }
  async function run(action: () => Promise<void>) {
    if (lock.current) return
    lock.current = true; setPending(true); setError(null); setNotice('')
    try { await action() } catch (failure) {
      setError(failure)
      if (!(failure instanceof AccountError) || ['NETWORK_UNCONFIRMED', 'INVALID_RESPONSE', 'SERVICE_UNAVAILABLE', 'VERSION_CONFLICT'].includes(failure.code) || failure.status >= 500) setUncertain(true)
    } finally { lock.current = false; setPending(false) }
  }
  return <><Button aria-label="邀请成员" onClick={() => setOpen(true)}>邀请成员</Button><Modal title="成员邀请" open={open} onCancel={() => setOpen(false)} footer={null} width={680}
    closable={!pending} keyboard={!pending} mask={{ closable: !pending }}>
    <form className="account-form" noValidate onSubmit={event => { event.preventDefault(); if (uncertain) return; void run(async () => {
      if (!email.includes('@') || email.length > 254 || /[\r\n\0]/.test(email)) throw new AccountError('VALIDATION_ERROR')
      await crews.invite(crewId, email)
      setEmail(''); setNotice('邀请邮件已受理，尚不能确认送达。邀请7天有效，受邀者须用对应邮箱登录并明确接受。')
      await records.refetch()
    }) }}>
      <div className="account-field"><label htmlFor="invite-email">受邀邮箱</label><Input id="invite-email" type="email" value={email} maxLength={254} disabled={pending} onChange={event => setEmail(event.target.value)} /></div>
      <Button type="primary" htmlType="submit" aria-label="发送邀请" aria-busy={pending} loading={pending} disabled={uncertain}>发送邀请</Button>
    </form>
    <ErrorNotice error={error ?? records.error} />{notice && <p role="status" className="account-note">{notice}</p>}
    {uncertain && <p className="account-note">操作结果未确认或邀请已变化。请先刷新列表，避免重复发送或撤销。</p>}
    <div className="section-heading invitation-heading"><h3>邀请记录</h3><Button aria-label="刷新邀请列表" loading={records.isFetching} disabled={pending} onClick={() => { void refresh() }}>刷新列表</Button></div>
    {records.isPending && <p role="status">正在读取邀请…</p>}
    {!records.isError && records.data && <><ul className="invitation-list">{records.data.items.map(item => <li key={item.id}><div><strong>{item.email}</strong><span>{status[item.status]}</span><span>有效至 <time dateTime={item.expires_at}>{new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', dateStyle: 'short', timeStyle: 'short' }).format(new Date(item.expires_at))}</time></span></div>{item.status === 'pending' && <Button aria-label={`撤销对${item.email}的邀请`} disabled={pending || uncertain} onClick={() => setRevoking(item)}>撤销邀请</Button>}</li>)}</ul>
      {!records.data.items.length && <p className="caption">当前没有邀请记录。</p>}
      <div className="list-pagination"><Button disabled={number === 1 || records.isFetching || pending} onClick={() => setNumber(number - 1)}>上一页</Button><span>第 {number} 页 · 共 {records.data.total} 条</span><Button disabled={number * 20 >= records.data.total || records.isFetching || pending} onClick={() => setNumber(number + 1)}>下一页</Button></div></>}
    {revoking && <div className="account-note"><p>确认撤销对 {revoking.email} 的邀请？旧链接将无法用于加入剧组。</p><Button aria-label="确认撤销邀请" disabled={pending || uncertain} loading={pending} onClick={() => { void run(async () => {
      await crews.revokeInvitation(crewId, revoking.id, revoking.lock_version)
      setRevoking(null); setNotice('邀请已撤销。'); await records.refetch()
    }) }}>确认撤销邀请</Button><Button type="text" disabled={pending} onClick={() => setRevoking(null)}>取消</Button></div>}
  </Modal></>
}
