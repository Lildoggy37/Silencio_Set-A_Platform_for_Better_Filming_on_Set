import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { Button, Input, Modal } from 'antd'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useAuth } from '../auth/AuthProvider'
import { ErrorNotice } from '../auth/AccountScreens'
import { AccountError } from '../api/accounts'
import type { Member } from '../api/crews'
import { movementNames } from '../api/stock'
import type { Stock, StockCommand } from '../api/stock'
import { useBusinessSession } from '../crews/useBusinessSession'

type Action = 'create' | 'change' | 'authorize'
const titles = { create: '登记耗材', change: '库存变动', authorize: '库存管理权限' }

export function StockBoard({ crewId, userId, canCreate, openId, onOpened }: { crewId: string; userId: string; canCreate: boolean; openId?: string | null; onOpened?: () => void }) {
  const { resources } = useAuth()
  const cache = useQueryClient()
  const [page, setPage] = useState(1)
  const [selected, setSelected] = useState<string | null>(openId ?? null)
  const [editing, setEditing] = useState<{ action: Action; row?: Stock } | null>(null)
  useEffect(() => { if (openId) setSelected(openId) }, [openId])
  const list = useQuery({ queryKey: ['crews', userId, crewId, 'stocks', page], queryFn: ({ signal }) => resources.stocks(crewId, page, signal), retry: false })
  useBusinessSession(list.error)
  function saved() { setEditing(null); void cache.invalidateQueries({ queryKey: ['crews', userId, crewId] }) }
  return <div><div className="section-heading"><h2>耗材库存</h2><div className="member-actions"><Button onClick={() => { void list.refetch() }}>刷新库存</Button>{canCreate && <Button type="primary" onClick={() => setEditing({ action: 'create' })}>登记耗材</Button>}</div></div>
    <p className="caption">记录耗材的补充、领用、退回与盘点；在详情中设置谁可以修改库存。</p><ErrorNotice error={list.error} />
    {list.isPending && <p role="status">正在读取库存…</p>}
    {!list.isError && list.data && <>{list.data.items.length === 0 ? <p>尚未登记耗材。</p> : <div className="directory-scroll"><table><thead><tr><th>耗材</th><th>当前库存</th><th>状态</th><th>操作</th></tr></thead><tbody>{list.data.items.map(row => <tr key={row.id}><td>{row.name}</td><td>{row.stock_quantity} {row.unit}</td><td>{row.status === 'available' ? '可用' : '停用'}</td><td><Button onClick={() => setSelected(row.id)} aria-label={`查看耗材${row.name}`}>详情</Button></td></tr>)}</tbody></table></div>}
      <div className="list-pagination"><Button disabled={page === 1 || list.isFetching} onClick={() => setPage(page - 1)}>上一页耗材</Button><span>第 {page} 页 · 共 {list.data.total} 项</span><Button disabled={page * 20 >= list.data.total || list.isFetching} onClick={() => setPage(page + 1)}>下一页耗材</Button></div></>}
    {selected && !editing && <StockDetail key={selected} crewId={crewId} userId={userId} id={selected} onClose={() => { setSelected(null); onOpened?.() }} onAction={(action, row) => setEditing({ action, row })} />}
    {editing && <StockForm key={`${editing.action}-${editing.row?.id}`} crewId={crewId} userId={userId} action={editing.action} row={editing.row} onClose={() => setEditing(null)} onSaved={saved} />}
  </div>
}

function StockDetail({ crewId, userId, id, onClose, onAction }: { crewId: string; userId: string; id: string; onClose: () => void; onAction: (action: Action, row: Stock) => void }) {
  const { resources } = useAuth()
  const [page, setPage] = useState(1)
  const [managing, setManaging] = useState(false), [locked, setLocked] = useState(false)
  const [notice, setNotice] = useState('')
  const cache = useQueryClient()
  const detail = useQuery({ queryKey: ['crews', userId, crewId, 'stock', id], queryFn: () => resources.stock(crewId, id), retry: false })
  const row = detail.isError ? undefined : detail.data
  const movements = useQuery({ queryKey: ['crews', userId, crewId, 'movements', id, page], queryFn: ({ signal }) => resources.movements(crewId, id, page, signal), enabled: !!row?.allowed_actions.includes('change'), retry: false })
  useBusinessSession(detail.error ?? movements.error)
  return <Modal title={row?.name ?? '耗材详情'} open footer={null} onCancel={onClose} closable={!locked} keyboard={!locked} mask={{ closable: !locked }} width={960}><div className="role-editor stock-detail"><ErrorNotice error={detail.error} />{notice && <p role="status" className="stock-saved-notice">{notice}</p>}
    {row && <><div className="stock-detail-summary"><p>当前库存：{row.stock_quantity} {row.unit}</p><Button disabled={locked} onClick={() => { void detail.refetch() }}>读取最新库存</Button></div><div className="workspace-tabs" aria-label="耗材详情内容"><Button disabled={locked} aria-pressed={!managing} onClick={() => setManaging(false)}>库存与流水</Button>{row.allowed_actions.includes('authorize') && <Button disabled={locked} aria-pressed={managing} onClick={() => { setNotice(''); setManaging(true) }}>库存管理权限</Button>}</div>
      {managing ? <StockForm embedded crewId={crewId} userId={userId} action="authorize" row={row} onLockChange={setLocked} onClose={() => setManaging(false)} onSaved={() => { setLocked(false); setManaging(false); setNotice('库存权限已更新。'); void cache.invalidateQueries({ queryKey: ['crews', userId, crewId] }) }} /> : <><div className="member-actions">{row.allowed_actions.includes('change') && <Button type="primary" onClick={() => onAction('change', row)}>库存变动</Button>}</div>
      {row.allowed_actions.includes('change') ? <><h3>库存流水</h3><ErrorNotice error={movements.error} />{movements.isPending && <p role="status">正在读取流水…</p>}
        {!movements.isError && movements.data && <>{movements.data.items.length === 0 ? <p>尚无库存流水，零初始化不生成变动。</p> : <ul className="resource-bookings">{movements.data.items.map(item => <li key={item.id}><strong>{movementNames[item.movement_type]} {item.quantity_delta > 0 ? '+' : ''}{item.quantity_delta} {row.unit}</strong><p>变动后 {item.balance_after} {row.unit} · {item.reason}</p><p>{new Date(item.created_at).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' })}</p></li>)}</ul>}
          <div className="list-pagination"><Button disabled={page === 1 || movements.isFetching} onClick={() => setPage(page - 1)}>上一页流水</Button><span>第 {page} 页 · 共 {movements.data.total} 条</span><Button disabled={page * 20 >= movements.data.total || movements.isFetching} onClick={() => setPage(page + 1)}>下一页流水</Button></div></>}
      </> : <p className="caption">当前可查看库存余额，修改库存和查看流水需要对应权限。</p>}</>}</>}
  </div></Modal>
}

function StockForm({ crewId, userId, action, row, onClose, onSaved, embedded = false, onLockChange }: { crewId: string; userId: string; action: Action; row?: Stock; onClose: () => void; onSaved: () => void; embedded?: boolean; onLockChange?: (locked: boolean) => void }) {
  const { resources, crews } = useAuth()
  const [base, setBase] = useState(row)
  const [name, setName] = useState('')
  const [unit, setUnit] = useState('件')
  const [amount, setAmount] = useState(action === 'create' ? 0 : 1)
  const [kind, setKind] = useState('restock')
  const [reason, setReason] = useState('')
  const [member, setMember] = useState<Member | null>(null)
  const [page, setPage] = useState(1)
  const [authorization, setAuthorization] = useState<'allow' | 'remove' | ''>('')
  const [transfer, setTransfer] = useState(false)
  const [command, setCommand] = useState<StockCommand | null>(null)
  const [pending, setPending] = useState(false)
  const [uncertain, setUncertain] = useState(false)
  const [needsReload, setNeedsReload] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const lock = useRef(false)
  const choosing = action !== 'change'
  const members = useQuery({ queryKey: ['crews', userId, crewId, 'members', page], queryFn: ({ signal }) => crews.members(crewId, page, signal), enabled: choosing, retry: false })
  useBusinessSession(error ?? members.error)
  useEffect(() => { onLockChange?.(pending || uncertain) }, [pending, uncertain, onLockChange])
  function review(event: FormEvent) {
    event.preventDefault()
    if (action === 'change' && amount === 0) { setError(new AccountError('VALIDATION_ERROR')); return }
    const body = action === 'create' ? { name: name.trim(), unit: unit.trim(), initial_quantity: amount, manager_member_id: member!.id }
      : action === 'change' ? { expected_lock_version: base!.lock_version, movement_type: kind, quantity_delta: kind === 'issue' ? -amount : amount, reason: reason.trim() }
        : { expected_lock_version: base!.lock_version, member_id: member!.id, expected_member_lock_version: member!.lock_version, permission_codes: authorization === 'allow' ? ['stock.manage'] : [], grant_capabilities: authorization === 'allow', transfer_management: authorization === 'allow' && transfer }
    const prepared = { action, id: base?.id, key: crypto.randomUUID(), body }
    setCommand(prepared); setError(null)
    if (action === 'authorize') void submit(prepared)
  }
  async function submit(prepared = command) {
    if (lock.current) return
    lock.current = true; setPending(true); setError(null)
    try { await resources.stockWrite(crewId, prepared!); onSaved() }
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
      if (base) setBase(await resources.stock(crewId, base.id))
      if (choosing) { const fresh = await crews.members(crewId, page); setMember(fresh.items.find(item => item.id === member?.id) ?? null) }
      setNeedsReload(false)
    } catch (failure) { setError(failure) }
    finally { setPending(false) }
  }
  const content = <div className="role-editor">
    {action === 'authorize' && <header className="stock-permission-heading"><div><h3>谁可以修改这项库存</h3><p>为「{base!.name}」指定成员及操作权限。</p></div></header>}
    <ErrorNotice error={error} />{needsReload && <Button onClick={() => { void reload() }} loading={pending}>读取最新库存并保留输入</Button>}
    {uncertain && <p role="alert">操作结果尚未确认，请用原请求重试，内容保持不变。</p>}
    {command && action !== 'authorize' ? <><p><strong>确认{titles[action]}？</strong></p><p>{base?.name ?? name}</p><p>{action === 'create' ? '初始库存' : movementNames[kind]}：{String(command.body.quantity_delta ?? amount)} {base?.unit ?? unit}</p>{reason && <p>{reason}</p>}
      <div className="member-actions"><Button disabled={pending || uncertain} onClick={() => setCommand(null)}>返回修改</Button><Button type="primary" aria-label={uncertain ? '用原库存请求重试' : '确认库存操作'} loading={pending} onClick={() => { void submit() }}>{uncertain ? '用原库存请求重试' : '确认库存操作'}</Button></div>
    </> : <form onSubmit={review}><fieldset className="custody-fields" disabled={pending || needsReload || uncertain}>
      {action === 'create' && <><label>耗材名称<Input required maxLength={100} value={name} onChange={event => setName(event.target.value)} /></label><label>库存单位<Input required maxLength={10} value={unit} onChange={event => setUnit(event.target.value)} /></label></>}
      {action === 'change' && <><p>当前库存：{base!.stock_quantity} {base!.unit}</p><label>变动类型<select value={kind} onChange={event => { setKind(event.target.value); setAmount(1) }}>{Object.entries(movementNames).filter(([code]) => code !== 'initial').map(([code, title]) => <option key={code} value={code}>{title}</option>)}</select></label></>}
      {action !== 'authorize' && <label>{action === 'create' ? '初始库存' : kind === 'adjustment' ? '盘点差额（正增负减）' : '变动数量'}<Input type="number" required min={action === 'create' ? 0 : kind === 'adjustment' ? -1_000_000 : 1} max={1_000_000} step={1} value={amount} onChange={event => setAmount(Number(event.target.value))} /></label>}
      {action === 'change' && <label>变动原因<Input.TextArea required maxLength={500} value={reason} onChange={event => setReason(event.target.value)} /></label>}
      {choosing && <><ErrorNotice error={members.error} /><label>{action === 'create' ? '耗材负责人' : '选择成员'}<select required value={member?.id ?? ''} onChange={event => setMember(members.data!.items.find(item => item.id === event.target.value)!)}><option value="">请选择在组成员</option>{members.data?.items.map(item => <option key={item.id} value={item.id}>{item.display_name}</option>)}</select></label>{(members.data?.total ?? 0) > 20 && <div className="list-pagination"><Button disabled={page === 1} onClick={() => { setMember(null); setPage(page - 1) }}>上一页候选</Button><Button disabled={!members.data || page * 20 >= members.data.total} onClick={() => { setMember(null); setPage(page + 1) }}>下一页候选</Button></div>}</>}
      {action === 'authorize' && <><fieldset className="stock-permission-options"><legend>设置权限</legend><label className="stock-permission-option"><input type="radio" name="stock-permission" required checked={authorization === 'allow'} onChange={() => setAuthorization('allow')} /><span><strong>允许修改这项库存</strong><span>可登记补充、领用、退回和盘点。</span></span></label><label className="stock-permission-option"><input type="radio" name="stock-permission" required checked={authorization === 'remove'} onChange={() => { setAuthorization('remove'); setTransfer(false) }} /><span><strong>移除这项库存的修改权限</strong><span>保留已有流水，其他耗材的权限不变。</span></span></label></fieldset>
        {authorization === 'allow' && <><p className="stock-permission-impact">同时启用该成员的库存操作权限，已有禁用设置将改为允许；其他耗材仍需单独授权。</p><details className="stock-permission-extra"><summary>负责人设置（可选）</summary><label><input type="checkbox" checked={transfer} onChange={event => setTransfer(event.target.checked)} />同时将这位成员设为耗材负责人</label></details></>}
      </>}
      {action !== 'authorize' && <Button type="primary" htmlType="submit" disabled={choosing && (!member || members.isFetching || members.isError)}>核对库存操作</Button>}
    </fieldset>{action === 'authorize' && <div className="stock-permission-footer"><Button disabled={pending || uncertain} onClick={onClose}>返回库存</Button>{uncertain ? <Button type="primary" aria-label="用原库存请求重试" loading={pending} onClick={() => { void submit() }}>用原库存请求重试</Button> : <Button type="primary" htmlType="submit" loading={pending} disabled={!member || !authorization || needsReload || members.isFetching || members.isError}>{authorization === 'remove' ? '确认移除权限' : '确认授权'}</Button>}</div>}</form>}
  </div>
  return embedded ? <section className="stock-permission-panel">{content}</section> : <Modal title={titles[action]} open footer={null} onCancel={onClose} closable={!pending && !uncertain} keyboard={!pending && !uncertain} mask={{ closable: !pending && !uncertain }}>{content}</Modal>
}
