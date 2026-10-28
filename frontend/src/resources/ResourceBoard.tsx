import { useEffect, useRef, useState } from 'react'
import { Button, Input, Modal } from 'antd'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useAuth } from '../auth/AuthProvider'
import { ErrorNotice } from '../auth/AccountScreens'
import { AccountError } from '../api/accounts'
import type { Member } from '../api/crews'
import { reservationStatuses, resourceTypes } from '../api/resources'
import type { Reservation, Resource, ResourceCommand } from '../api/resources'
import type { CalendarRange } from '../api/availability'
import { useBusinessSession } from '../crews/useBusinessSession'

const local = (date: Date) => new Date(date.getTime() + 8 * 3_600_000).toISOString().slice(0, 16)
const instant = (value: string) => new Date(`${value}:00+08:00`).getTime()
const nextDay = () => { const start = new Date(Date.now() + 8 * 3_600_000); start.setUTCDate(start.getUTCDate() + 1); start.setUTCHours(9, 0, 0, 0); return new Date(start.getTime() - 8 * 3_600_000) }
const displayTime = (value: string) => new Date(value).toLocaleString('zh-CN', { hour12: false, timeZone: 'Asia/Shanghai' })
const actionNames = { create: '登记资源', edit: '编辑资源', authorize: '负责人及审批授权', apply: '申请预约', accept: '批准预约', reject: '拒绝预约', cancel: '取消预约' }
type Action = keyof typeof actionNames
type Manager = Pick<Member, 'id' | 'display_name' | 'lock_version'>
type Draft = { action: Action; resource?: Resource; booking?: Reservation }

function Pager({ page, total, fetching, onPage }: { page: number; total: number; fetching: boolean; onPage: (page: number) => void }) {
  return <div className="list-pagination"><Button disabled={page === 1 || fetching} onClick={() => onPage(page - 1)}>上一页</Button><span>第 {page} 页 · 共 {total} 项</span><Button disabled={page * 20 >= total || fetching} onClick={() => onPage(page + 1)}>下一页</Button></div>
}

export function ResourceBoard({ crewId, userId, self, canCreate, openResourceId, onResourceOpened }: {
  crewId: string; userId: string; self: Member; canCreate: boolean; openResourceId?: string | null; onResourceOpened?: () => void
}) {
  const { resources } = useAuth()
  const cache = useQueryClient()
  const [page, setPage] = useState(1)
  const [selected, setSelected] = useState<string | null>(null)
  const [draft, setDraft] = useState<Draft | null>(null)
  const list = useQuery({ queryKey: ['crews', userId, crewId, 'resources', page], queryFn: ({ signal }) => resources.list(crewId, page, signal), retry: false })
  useBusinessSession(list.error)
  useEffect(() => { if (openResourceId) { setSelected(openResourceId); onResourceOpened?.() } }, [openResourceId, onResourceOpened])
  async function saved() { setDraft(null); await cache.invalidateQueries({ queryKey: ['crews', userId, crewId] }) }
  return <div className="resource-board">
    <div className="section-heading"><h2>资源登记</h2><div className="member-actions"><Button loading={list.isFetching} onClick={() => { void list.refetch() }}>刷新资源</Button>{canCreate && <Button type="primary" onClick={() => setDraft({ action: 'create' })}>登记资源</Button>}</div></div>
    <p className="caption">登记设备、道具与场地。批准预约后才占用时段容量；借还、耗材库存与勘景图片尚未接入。</p>
    <ErrorNotice error={list.error} />
    {list.isPending && <p role="status">正在读取资源…</p>}
    {!list.isError && list.data && <>{list.data.items.length === 0 ? <p>尚未登记资源。</p> : <div className="directory-scroll"><table><caption className="caption">数量是登记总量；可预约不代表实物已归还</caption><thead><tr><th>资源</th><th>类型与数量</th><th>负责人</th><th>状态</th><th>操作</th></tr></thead><tbody>{list.data.items.map(row => <tr key={row.id}><td>{row.name}</td><td>{resourceTypes[row.resource_type]} · {row.total_quantity} {row.unit}</td><td>{row.manager_display_name}</td><td>{row.status === 'available' ? '可预约' : '已停用'}{!row.review_ready && ' · 待授权审批人'}</td><td><Button aria-label={`查看资源${row.name}`} onClick={() => setSelected(row.id)}>查看详情</Button></td></tr>)}</tbody></table></div>}<Pager page={page} total={list.data.total} fetching={list.isFetching} onPage={setPage} /></>}
    {selected && <ResourceDetail key={selected} crewId={crewId} userId={userId} resourceId={selected} onClose={() => setSelected(null)} onAction={setDraft} />}
    {draft && <ResourceActionDialog key={`${draft.action}-${draft.resource?.id ?? 'new'}-${draft.booking?.id ?? ''}`} crewId={crewId} userId={userId} self={self} draft={draft} onClose={() => setDraft(null)} onSaved={saved} />}
  </div>
}

function ResourceDetail({ crewId, userId, resourceId, onClose, onAction }: { crewId: string; userId: string; resourceId: string; onClose: () => void; onAction: (draft: Draft) => void }) {
  const { resources } = useAuth()
  const [page, setPage] = useState(1)
  const [start, setStart] = useState(() => local(nextDay()))
  const [end, setEnd] = useState(() => local(new Date(nextDay().getTime() + 3_600_000)))
  const [range, setRange] = useState<CalendarRange | null>(null)
  const [error, setError] = useState<unknown>(null)
  const detail = useQuery({ queryKey: ['crews', userId, crewId, 'resource', resourceId], queryFn: ({ signal }) => resources.get(crewId, resourceId, signal), retry: false })
  const bookings = useQuery({ queryKey: ['crews', userId, crewId, 'reservations', resourceId, page], queryFn: ({ signal }) => resources.reservations(crewId, resourceId, page, signal), enabled: !!detail.data && !detail.isError, retry: false })
  const capacity = useQuery({ queryKey: ['crews', userId, crewId, 'resource-capacity', resourceId, range], queryFn: ({ signal }) => resources.availability(crewId, resourceId, range!, signal), enabled: !!range && !!detail.data && !detail.isError, retry: false })
  useBusinessSession(detail.error ?? bookings.error ?? capacity.error)
  const row = detail.isError ? undefined : detail.data
  function check() {
    const left = instant(start), right = instant(end)
    if (!Number.isFinite(left) || !Number.isFinite(right) || right <= left || right - left > 31 * 86_400_000) { setError(new AccountError('VALIDATION_ERROR')); return }
    setError(null); const next = { range_start: new Date(left).toISOString(), range_end: new Date(right).toISOString() }
    if (range?.range_start === next.range_start && range?.range_end === next.range_end) void capacity.refetch()
    else setRange(next)
  }
  return <Modal title={row?.name ?? '资源详情'} open width={880} footer={null} onCancel={onClose}>
    <div className="role-editor"><ErrorNotice error={detail.error} />{detail.isPending && <p role="status">正在读取资源详情…</p>}<Button loading={detail.isFetching} onClick={() => { void detail.refetch(); void bookings.refetch(); if (range) void capacity.refetch() }}>重新读取资源</Button>
      {row && <><p>{resourceTypes[row.resource_type]} · 登记 {row.total_quantity} {row.unit} · {row.status === 'available' ? '可预约' : '已停用'}</p><p>负责人：{row.manager_display_name} · {row.review_ready ? '审批授权已就绪' : '待管理员确认审批授权'}</p>{row.description && <p className="resource-description">{row.description}</p>}
        <div className="member-actions">{row.allowed_actions.map(action => <Button key={action} onClick={() => onAction({ action, resource: row })}>{actionNames[action]}</Button>)}</div>
        <section aria-labelledby="capacity-title"><h3 id="capacity-title">查询预约余量</h3><p className="caption">时间按北京时间输入，单次最多31天。余量只统计已批准预约，申请提交与审批时仍会重新检查。</p><div className="resource-time-grid"><label>查询开始<input type="datetime-local" value={start} onChange={event => setStart(event.target.value)} /></label><label>查询结束<input type="datetime-local" value={end} onChange={event => setEnd(event.target.value)} /></label></div><Button onClick={check} loading={capacity.isFetching}>查询余量</Button><ErrorNotice error={error ?? capacity.error} />
          {range && !capacity.isError && capacity.data && <p role="status">{displayTime(capacity.data.range_start)} 至 {displayTime(capacity.data.range_end)}：同时占用峰值 {capacity.data.peak_confirmed_quantity} {row.unit}，最少余量 {capacity.data.minimum_remaining_quantity} {row.unit}。尚未包含实物借还情况。</p>}</section>
        <section aria-labelledby="reservation-title"><div className="section-heading"><h3 id="reservation-title">可查看的预约</h3><Button loading={bookings.isFetching} onClick={() => { void bookings.refetch() }}>刷新预约</Button></div><p className="caption">展示本人本次入组的申请，以及当前有权审批的申请。批准后修改时段或数量需取消后重新申请。</p><ErrorNotice error={bookings.error} />{bookings.isPending && <p role="status">正在读取预约…</p>}
          {!bookings.isError && bookings.data && <>{bookings.data.items.length === 0 ? <p>暂无可查看的预约。</p> : <ul className="resource-bookings">{bookings.data.items.map(booking => <li key={booking.id}><strong>{reservationStatuses[booking.status]} · {booking.quantity} {row.unit}</strong><p>{displayTime(booking.starts_at)} — {displayTime(booking.ends_at)}</p>{booking.decision_note && <p>审批说明：{booking.decision_note}</p>}{booking.cancel_reason && <p>取消原因：{booking.cancel_reason}</p>}<div className="member-actions">{booking.allowed_actions.map(action => <Button key={action} onClick={() => onAction({ action, resource: row, booking })}>{actionNames[action]}</Button>)}</div></li>)}</ul>}<Pager page={page} total={bookings.data.total} fetching={bookings.isFetching} onPage={setPage} /></>}
        </section><p className="module-note">资源变动暂未接入通知中心，请刷新资源页查看结果。预约到期不表示实物已归还；拍摄计划与借还的依赖检查将在对应功能接入时补齐。</p>
      </>}
    </div>
  </Modal>
}

function ResourceActionDialog({ crewId, userId, self, draft, onClose, onSaved }: { crewId: string; userId: string; self: Member; draft: Draft; onClose: () => void; onSaved: () => Promise<void> }) {
  const { resources, crews } = useAuth()
  const { action } = draft
  const [current, setCurrent] = useState(draft.resource)
  const [booking, setBooking] = useState(draft.booking)
  const [name, setName] = useState(current?.name ?? '')
  const [type, setType] = useState<keyof typeof resourceTypes>(current?.resource_type ?? 'equipment')
  const [unit, setUnit] = useState(current?.unit ?? '件')
  const [quantity, setQuantity] = useState(String(action === 'apply' ? 1 : current?.total_quantity ?? 1))
  const [status, setStatus] = useState<Resource['status']>(current?.status ?? 'available')
  const [description, setDescription] = useState(current?.description ?? '')
  const [manager, setManager] = useState<Manager | null>(() => current ? { id: current.manager_member_id, display_name: current.manager_display_name, lock_version: current.manager_lock_version } : self)
  const [grant, setGrant] = useState(false)
  const [memberPage, setMemberPage] = useState(1)
  const [start, setStart] = useState(() => local(nextDay()))
  const [end, setEnd] = useState(() => local(new Date(nextDay().getTime() + 3_600_000)))
  const [note, setNote] = useState('')
  const [command, setCommand] = useState<ResourceCommand | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [pending, setPending] = useState(false)
  const [uncertain, setUncertain] = useState(false)
  const [needsReload, setNeedsReload] = useState(false)
  const lock = useRef(false)
  const choosing = action === 'create' || action === 'authorize'
  const members = useQuery({ queryKey: ['crews', userId, crewId, 'members', memberPage], queryFn: ({ signal }) => crews.members(crewId, memberPage, signal), enabled: choosing, retry: false })
  useBusinessSession(error ?? members.error)
  function review() {
    try {
      const amount = Number(quantity)
      let body: Record<string, unknown>
      if (['create', 'edit', 'apply'].includes(action) && (!Number.isInteger(amount) || amount < 1 || amount > 1_000_000)) throw new AccountError('VALIDATION_ERROR')
      if (action === 'create' || action === 'edit') {
        if (!name.trim() || Array.from(name.trim()).length > 100 || !unit.trim() || Array.from(unit.trim()).length > 10 || /[\p{Cc}\p{Cs}]/u.test(name + unit) || Array.from(description.trim()).length > 2000) throw new AccountError('VALIDATION_ERROR')
        body = { name: name.trim(), unit: unit.trim(), total_quantity: amount, description: description.trim() || null }
        if (action === 'create') { if (!manager) throw new AccountError('VALIDATION_ERROR'); body = { ...body, resource_type: type, manager_member_id: manager.id } }
        else body = { ...body, status, expected_lock_version: current!.lock_version }
      } else if (action === 'authorize') {
        if (!manager || manager.lock_version < 1) throw new AccountError('VALIDATION_ERROR')
        body = { manager_member_id: manager.id, expected_manager_lock_version: manager.lock_version, expected_lock_version: current!.lock_version, grant_review_permission: grant }
      } else if (action === 'apply') {
        const left = instant(start), right = instant(end)
        if (!Number.isFinite(left) || !Number.isFinite(right) || left <= Date.now() || right <= left || right - left > 31 * 86_400_000 || amount > current!.total_quantity) throw new AccountError('VALIDATION_ERROR')
        body = { resource_id: current!.id, starts_at: new Date(left).toISOString(), ends_at: new Date(right).toISOString(), quantity: amount }
      } else {
        if ((action === 'reject' || action === 'cancel') && !note.trim() || Array.from(note.trim()).length > 500) throw new AccountError('VALIDATION_ERROR')
        body = action === 'cancel' ? { expected_lock_version: booking!.lock_version, reason: note.trim() } : { expected_lock_version: booking!.lock_version, decision: action, decision_note: note.trim() || null }
      }
      setError(null); setCommand({ action: action === 'accept' || action === 'reject' ? 'decide' : action, resourceId: current?.id, reservationId: booking?.id, body, ...(action === 'edit' ? {} : { key: crypto.randomUUID() }) })
    } catch (failure) { setError(failure) }
  }
  async function submit() {
    if (!command || lock.current || needsReload) return
    lock.current = true; setPending(true); setError(null)
    try {
      await resources.execute(crewId, command)
      onClose()
      try { await onSaved() } catch { /* A committed result must not be resubmitted for a refresh failure. */ }
    } catch (failure) {
      setError(failure)
      const unknown = !(failure instanceof AccountError) || ['NETWORK_UNCONFIRMED', 'INVALID_RESPONSE', 'SERVICE_UNAVAILABLE'].includes(failure.code) || failure.status >= 500
      setUncertain(unknown && action !== 'edit')
      if (!unknown || action === 'edit') { setNeedsReload(true); setCommand(null) }
    } finally { lock.current = false; setPending(false) }
  }
  async function reload() {
    if (lock.current || uncertain) return
    lock.current = true; setPending(true); setError(null)
    try {
      if (current) setCurrent(await resources.get(crewId, current.id))
      if (booking) setBooking(await resources.reservation(crewId, booking.id))
      if (choosing) {
        const fresh = await members.refetch()
        if (fresh.isError) throw fresh.error
        setManager(fresh.data?.items.find(row => row.id === manager?.id) ?? null)
      }
      setNeedsReload(false); setCommand(null)
    } catch (failure) { setError(failure) }
    finally { lock.current = false; setPending(false) }
  }
  return <Modal title={actionNames[action]} open footer={null} onCancel={onClose} closable={!pending && !uncertain} keyboard={!pending && !uncertain} mask={{ closable: !pending && !uncertain }} width={620}>
    <div className="role-editor"><ErrorNotice error={error ?? (choosing ? members.error : null)} />
      {uncertain && <p role="alert">尚未确认服务器是否已完成操作。请求内容已冻结，请用原请求重试；不会重新创建另一份申请。</p>}
      {needsReload && <><p role="alert">请先重新读取当前状态，再确认保留的草稿。</p><Button onClick={() => { void reload() }} loading={pending}>重新读取并保留草稿</Button></>}
      {!command ? <>
        {(action === 'create' || action === 'edit') && <><label>资源名称<Input value={name} maxLength={100} onChange={event => setName(event.target.value)} /></label>{action === 'create' && <label>资源类型<select value={type} onChange={event => setType(event.target.value as typeof type)}>{Object.entries(resourceTypes).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>}<label>计量单位<Input value={unit} maxLength={10} onChange={event => setUnit(event.target.value)} /></label><label>登记总量<Input type="number" min={1} max={1_000_000} value={quantity} onChange={event => setQuantity(event.target.value)} /></label><label>资源说明<Input.TextArea value={description} maxLength={2000} onChange={event => setDescription(event.target.value)} /></label>{action === 'edit' && <label>使用状态<select value={status} onChange={event => setStatus(event.target.value as typeof status)}><option value="available">可预约</option><option value="disabled">已停用</option></select></label>}</>}
        {choosing && <><label>资源负责人<select value={manager?.id ?? ''} onChange={event => setManager(members.data?.items.find(row => row.id === event.target.value) ?? null)}><option value="">请选择在组成员</option>{manager && !members.data?.items.some(row => row.id === manager.id) && <option value={manager.id}>{manager.display_name}</option>}{!members.isError && members.data?.items.map(row => <option key={row.id} value={row.id}>{row.display_name}</option>)}</select></label>{members.data && <Pager page={memberPage} total={members.data.total} fetching={members.isFetching} onPage={setMemberPage} />}{members.isError && <Button onClick={() => { void members.refetch() }}>重新读取成员</Button>}<p className="caption">资源登记不会自动赋予负责人审批权，需管理员另行确认。</p></>}
        {action === 'authorize' && <><label><input type="checkbox" checked={grant} onChange={event => setGrant(event.target.checked)} />明确授予该成员预约审批能力</label><p className="module-note">勾选将把该成员的预约审批权限设为允许，包括替换已有拒绝；还会授予本资源的审批范围。转交会撤销旧负责人的本资源审批范围，其在其他资源的权限保留。未勾选时，所选成员必须已经具备审批能力。</p></>}
        {action === 'apply' && <><p>申请 {current?.name}，登记总量 {current?.total_quantity} {current?.unit}。待审批申请不占用容量。</p><label>预约开始<input type="datetime-local" value={start} onChange={event => setStart(event.target.value)} /></label><label>预约结束<input type="datetime-local" value={end} onChange={event => setEnd(event.target.value)} /></label><label>预约数量<Input type="number" min={1} max={current?.total_quantity} value={quantity} onChange={event => setQuantity(event.target.value)} /></label><p className="caption">按北京时间输入，最长31天。</p></>}
        {booking && <><p>{current?.name} · {booking.quantity} {current?.unit}</p><p>{displayTime(booking.starts_at)} — {displayTime(booking.ends_at)}</p><p>当前状态：{reservationStatuses[booking.status]}</p><label>{action === 'cancel' ? '取消原因' : action === 'reject' ? '拒绝原因' : '审批说明（选填）'}<Input.TextArea value={note} maxLength={500} onChange={event => setNote(event.target.value)} /></label>{action === 'accept' && <p className="caption">确认批准时将重新检查负责人权限、申请人成员身份与同时段容量。</p>}</>}
        <Button type="primary" disabled={pending || needsReload || choosing && (members.isError || members.isPending)} onClick={review}>核对本次操作</Button>
      </> : <><p><strong>确认{actionNames[action]}？</strong></p><p>{current?.name ?? name}{['create', 'edit', 'apply'].includes(action) && ` · ${quantity} ${unit}`}</p>{choosing && <p>负责人：{manager?.display_name}</p>}{action === 'authorize' && <p>{grant ? '将明确授予审批能力，并指定本资源审批范围。' : '保留已有审批能力，仅确认本资源负责人及审批范围。'}</p>}{action === 'apply' && <p>{displayTime(String(command.body.starts_at))} — {displayTime(String(command.body.ends_at))}</p>}{note.trim() && <p>{note.trim()}</p>}
        <div className="member-actions"><Button disabled={pending || uncertain} onClick={() => setCommand(null)}>返回修改</Button><Button type="primary" aria-label={uncertain ? '用原请求重试' : '确认提交'} aria-busy={pending} loading={pending} onClick={() => { void submit() }}>{uncertain ? '用原请求重试' : '确认提交'}</Button></div>
      </>}
    </div>
  </Modal>
}
