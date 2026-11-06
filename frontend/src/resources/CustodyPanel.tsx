import { useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { Button, Input, Modal } from 'antd'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useAuth } from '../auth/AuthProvider'
import { ErrorNotice } from '../auth/AccountScreens'
import { AccountError } from '../api/accounts'
import type { Member } from '../api/crews'
import type { Checkout, CustodyCommand } from '../api/custody'
import type { Reservation, Resource } from '../api/resources'
import { useBusinessSession } from '../crews/useBusinessSession'

const time = (value: string) => new Date(value).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false })
const localTime = (value: string) => new Date(Date.parse(value) + 8 * 3_600_000).toISOString().slice(0, 16)
type Mode = { kind: 'authorize' } | { kind: 'checkout'; booking: Reservation } | { kind: 'return'; loan: Checkout }

export function CustodyPanel({ resource, crewId, userId, checkoutBooking, onCheckoutClosed }: {
  resource: Resource; crewId: string; userId: string; checkoutBooking: Reservation | null; onCheckoutClosed: () => void
}) {
  const { resources } = useAuth()
  const cache = useQueryClient()
  const [page, setPage] = useState(1)
  const [mode, setMode] = useState<Mode | null>(null)
  const [notice, setNotice] = useState('')
  const loans = useQuery({ queryKey: ['crews', userId, crewId, 'checkouts', resource.id, page],
    queryFn: ({ signal }) => resources.listCheckouts(crewId, resource.id, page, signal), retry: false })
  useBusinessSession(loans.error)
  const current = checkoutBooking ? { kind: 'checkout' as const, booking: checkoutBooking } : mode
  function close() { setMode(null); onCheckoutClosed() }
  function saved() {
    setNotice(current?.kind === 'return' ? '归还已记录。' : current?.kind === 'checkout' ? '借出已记录。' : '经办范围已保存。')
    close(); void cache.invalidateQueries({ queryKey: ['crews', userId, crewId] })
  }
  return <section aria-labelledby="custody-title"><div className="section-heading"><h3 id="custody-title">实物借还</h3><div className="member-actions">
    <Button onClick={() => { void loans.refetch() }} loading={loans.isFetching}>刷新借还</Button>
    {resource.allowed_actions.includes('authorize') && <Button onClick={() => setMode({ kind: 'authorize' })}>借还经办授权</Button>}
  </div></div><p className="caption">按记录展示已借、已还和未还数量。预约到期不自动归还；个人查看本人记录，经办人查看获授权资源的记录。</p>
    {notice && <p role="status">{notice}</p>}<ErrorNotice error={loans.error} />{loans.isPending && <p role="status">正在读取借还记录…</p>}
    {!loans.isError && loans.data && <>{loans.data.items.length === 0 ? <p>暂无可查看的借还记录。</p> : <ul className="resource-bookings">{loans.data.items.map(loan => <li key={loan.id}>
      <strong>{loan.status === 'returned' ? '已归还' : loan.status === 'overdue' ? '逾期未还' : '借出未还'}</strong>
      <p>借出 {loan.quantity} · 已还 {loan.returned_quantity} · 未还 {loan.outstanding_quantity} {resource.unit}</p><p>借出时间：{time(loan.checked_out_at)} · 预计归还：{time(loan.expected_return_at)}</p>
      {loan.outstanding_quantity > 0 && resource.custody_actions?.includes('return') && <Button aria-label={`登记归还：${time(loan.checked_out_at)}`} onClick={() => setMode({ kind: 'return', loan })}>登记归还</Button>}
    </li>)}</ul>}<div className="list-pagination"><Button disabled={page === 1 || loans.isFetching} onClick={() => setPage(page - 1)}>上一页借还</Button><span>第 {page} 页 · 共 {loans.data.total} 条</span><Button disabled={page * 20 >= loans.data.total || loans.isFetching} onClick={() => setPage(page + 1)}>下一页借还</Button></div></>}
    {current && <CustodyDialog key={`${current.kind}-${current.kind === 'return' ? current.loan.id : current.kind === 'checkout' ? current.booking.id : resource.id}`} mode={current} resource={resource} crewId={crewId} userId={userId} onClose={close} onSaved={saved} />}
  </section>
}

function CustodyDialog({ mode, resource, crewId, userId, onClose, onSaved }: {
  mode: Mode; resource: Resource; crewId: string; userId: string; onClose: () => void; onSaved: () => void
}) {
  const { resources, crews } = useAuth()
  const [base, setBase] = useState(resource)
  const [booking, setBooking] = useState(mode.kind === 'checkout' ? mode.booking : null)
  const [loan, setLoan] = useState(mode.kind === 'return' ? mode.loan : null)
  const [quantity, setQuantity] = useState(1)
  const [returnAt, setReturnAt] = useState(mode.kind === 'checkout' ? localTime(mode.booking.ends_at) : '')
  const [note, setNote] = useState('')
  const [memberPage, setMemberPage] = useState(1)
  const [member, setMember] = useState<Member | null>(null)
  const [codes, setCodes] = useState<string[]>([])
  const [grant, setGrant] = useState(false)
  const [command, setCommand] = useState<CustodyCommand | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [pending, setPending] = useState(false)
  const [uncertain, setUncertain] = useState(false)
  const [needsReload, setNeedsReload] = useState(false)
  const lock = useRef(false)
  const members = useQuery({ queryKey: ['crews', userId, crewId, 'members', memberPage],
    queryFn: ({ signal }) => crews.members(crewId, memberPage, signal), enabled: mode.kind === 'authorize', retry: false })
  useBusinessSession(error ?? members.error)
  const title = mode.kind === 'checkout' ? '登记借出' : mode.kind === 'return' ? '登记归还' : '借还经办授权'
  function review(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const key = crypto.randomUUID()
    if (mode.kind === 'checkout') setCommand({ action: 'checkout', id: booking!.id, key,
      body: { quantity, expected_lock_version: booking!.lock_version, expected_return_at: new Date(`${returnAt}:00+08:00`).toISOString() } })
    else if (mode.kind === 'return') setCommand({ action: 'return', id: loan!.id, key,
      body: { quantity, expected_lock_version: loan!.lock_version, note: note.trim() || null } })
    else setCommand({ action: 'authorize', id: base.id, key, body: { member_id: member!.id, expected_lock_version: base.lock_version,
      expected_member_lock_version: member!.lock_version, permission_codes: [...codes].sort(), grant_capabilities: grant } })
    setError(null)
  }
  async function submit() {
    if (lock.current) return
    lock.current = true; setPending(true); setError(null)
    try { await resources.custody(crewId, command!); onSaved() }
    catch (failure) {
      setError(failure)
      const unknown = !(failure instanceof AccountError) || ['NETWORK_UNCONFIRMED', 'INVALID_RESPONSE', 'SERVICE_UNAVAILABLE'].includes(failure.code) || failure.status >= 500
      setUncertain(unknown)
      if (!unknown) { setCommand(null); setNeedsReload(true) }
    } finally { lock.current = false; setPending(false) }
  }
  async function reload() {
    setPending(true); setError(null)
    try {
      setBase(await resources.get(crewId, base.id))
      if (booking) setBooking(await resources.reservation(crewId, booking.id))
      if (loan) setLoan(await resources.getCheckout(crewId, loan.id))
      if (mode.kind === 'authorize') {
        const fresh = await crews.members(crewId, memberPage)
        setMember(fresh.items.find(item => item.id === member?.id) ?? null)
      }
      setNeedsReload(false)
    } catch (failure) { setError(failure) }
    finally { setPending(false) }
  }
  return <Modal title={title} open footer={null} onCancel={onClose} closable={!pending && !uncertain} keyboard={!pending && !uncertain} mask={{ closable: !pending && !uncertain }} width={600}>
    <div className="role-editor"><p>{base.name} · {title}</p><ErrorNotice error={error} />
      {needsReload && <><p>数据或权限已变化，请读取当前状态后核对保留的输入。</p><Button onClick={() => { void reload() }} loading={pending}>读取最新借还状态</Button></>}
      {uncertain && <p role="alert">结果尚未确认，请用原请求重试；本次数量与内容保持不变。</p>}
      {command ? <><p><strong>确认{title}？</strong></p>{mode.kind === 'authorize' ? <><p>成员：{member?.display_name}</p><p>{codes.length === 0 ? '撤销该成员在本资源的全部借还经办范围。' : codes.map(code => code === 'resource.checkout' ? '借出' : '归还').join('、')}</p><p>{grant ? '明确授予所选经办能力，包括替换已有拒绝。' : '使用已有能力；不会自动授予新能力。'}</p></> : <><p>本次数量：{quantity} {base.unit}</p>{mode.kind === 'checkout' ? <p>预计归还：{time(command.action === 'checkout' ? command.body.expected_return_at : '')}</p> : note && <p>说明：{note}</p>}</>}
        <div className="member-actions"><Button disabled={pending || uncertain} onClick={() => setCommand(null)}>返回修改</Button><Button type="primary" aria-label={uncertain ? '用原借还请求重试' : '确认借还操作'} loading={pending} onClick={() => { void submit() }}>{uncertain ? '用原借还请求重试' : '确认借还操作'}</Button></div>
      </> : <form onSubmit={review}>
        <fieldset disabled={pending || needsReload} className="custody-fields">
          {mode.kind === 'authorize' ? <><ErrorNotice error={members.error} /><label>经办成员<select required value={member?.id ?? ''} onChange={event => setMember(members.data!.items.find(item => item.id === event.target.value)!)}><option value="">请选择成员</option>{members.data?.items.map(item => <option key={item.id} value={item.id}>{item.display_name}</option>)}</select></label>
            <div className="list-pagination"><Button disabled={memberPage === 1 || members.isFetching} onClick={() => { setMember(null); setMemberPage(memberPage - 1) }}>上一页成员</Button><span>第 {memberPage} 页</span><Button disabled={!members.data || memberPage * 20 >= members.data.total || members.isFetching} onClick={() => { setMember(null); setMemberPage(memberPage + 1) }}>下一页成员</Button><Button onClick={() => { void members.refetch() }}>刷新候选成员</Button></div>
            {(['resource.checkout', 'resource.return'] as const).map(code => <label key={code}><input type="checkbox" checked={codes.includes(code)} onChange={event => setCodes(event.target.checked ? [...codes, code] : codes.filter(value => value !== code))} />{code === 'resource.checkout' ? '允许经办借出' : '允许经办归还'}</label>)}
            <p className="caption">本次替换该成员在本资源的借还范围，两项均不选即撤销。其他资源范围保留。</p><label><input type="checkbox" checked={grant} onChange={event => setGrant(event.target.checked)} />明确授予所选经办能力</label><p className="caption">勾选会将对应能力设为允许，包括替换已有拒绝。未勾选时，成员必须已有对应能力。撤销资源范围不撤销其他资源需要的能力。</p>
          </> : <><p>{mode.kind === 'return' ? `尚未归还 ${loan!.outstanding_quantity} ${base.unit}，允许分次归还。` : `预约批准 ${booking!.quantity} ${base.unit}，累计借出不能超过批准量。`}</p><label>本次数量<Input type="number" required min={1} max={mode.kind === 'return' ? loan!.outstanding_quantity : booking!.quantity} step={1} value={quantity} onChange={event => setQuantity(Number(event.target.value))} /></label>
            {mode.kind === 'checkout' ? <label>预计归还时间（北京时间）<input type="datetime-local" required value={returnAt} onChange={event => setReturnAt(event.target.value)} /></label> : <label>归还说明<Input.TextArea maxLength={500} value={note} onChange={event => setNote(event.target.value)} /></label>}
          </>}
          <Button type="primary" htmlType="submit" disabled={mode.kind === 'authorize' && (!member || members.isFetching || members.isError)}>核对借还操作</Button>
        </fieldset>
      </form>}
    </div>
  </Modal>
}
