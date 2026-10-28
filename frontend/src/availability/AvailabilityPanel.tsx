import { useEffect, useRef, useState } from 'react'
import { Button, Modal } from 'antd'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useAuth } from '../auth/AuthProvider'
import { AccountError } from '../api/accounts'
import type { Calendar, CalendarInterval, CalendarRange } from '../api/availability'
import { ErrorNotice } from '../auth/AccountScreens'
import { useBusinessSession } from '../crews/useBusinessSession'

type Draft = { key: number; starts: string; ends: string; status: '' | CalendarInterval['availability'] }
const day = 86_400_000
function local(value: string) {
  const parts = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23' }).formatToParts(new Date(value))
  const part = (type: string) => parts.find(item => item.type === type)!.value
  return `${part('year')}-${part('month')}-${part('day')}T${part('hour')}:${part('minute')}:${part('second')}`
}
const display = (value: string) => local(value).replace('T', ' ')
function iso(value: string) {
  if (!/^\d{4}-\d\d-\d\dT\d\d:\d\d(?::\d\d)?$/.test(value)) throw new AccountError('VALIDATION_ERROR')
  const date = new Date(`${value}+08:00`)
  if (!Number.isFinite(date.getTime())) throw new AccountError('VALIDATION_ERROR')
  return date.toISOString()
}
function initialWindow(): CalendarRange {
  const start = new Date(`${local(new Date().toISOString()).slice(0, 10)}T00:00:00+08:00`)
  return { range_start: start.toISOString(), range_end: new Date(start.getTime() + 7 * day).toISOString() }
}
function canonical(values: CalendarInterval[]) {
  return JSON.stringify([...values].sort((a, b) => Date.parse(a.starts_at) - Date.parse(b.starts_at)).map(item => [Date.parse(item.starts_at), Date.parse(item.ends_at), item.availability]))
}

function CalendarRows({ data }: { data: Calendar }) {
  const rows = [...data.intervals.map(item => ({ ...item, label: item.availability === 'available' ? '可用' : '不可用' })),
    ...data.unregistered.map(item => ({ ...item, label: '未登记（未知）' }))].sort((a, b) => Date.parse(a.starts_at) - Date.parse(b.starts_at))
  return <div className="directory-scroll"><table className="availability-table"><caption>当前登记 · 北京时间，结束时刻不包含在区间内</caption><thead><tr><th scope="col">开始</th><th scope="col">结束</th><th scope="col">状态</th></tr></thead><tbody>{rows.map((item, index) => <tr key={index}><td>{display(item.starts_at)}</td><td>{display(item.ends_at)}</td><td>{item.label}</td></tr>)}</tbody></table></div>
}

export function AvailabilityPanel({ crewId, userId, selfMemberId, mode, onClose }: {
  crewId: string; userId: string; selfMemberId: string; mode: 'own' | 'crew'; onClose: () => void
}) {
  const { availability } = useAuth()
  const cache = useQueryClient()
  const [range, setRange] = useState(initialWindow)
  const [start, setStart] = useState(() => local(range.range_start))
  const [end, setEnd] = useState(() => local(range.range_end))
  const [page, setPage] = useState(1)
  const [memberFilter, setMemberFilter] = useState<{ id: string; name: string } | null>(null)
  const [baseline, setBaseline] = useState<Calendar | null>(null)
  const [draft, setDraft] = useState<Draft[]>([])
  const [review, setReview] = useState(false)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [needsReload, setNeedsReload] = useState(false)
  const [message, setMessage] = useState('')
  const lock = useRef(false)
  const sequence = useRef(0)
  const prefix = ['crews', userId, crewId, 'availability']
  const ownKey = [...prefix, 'own', range.range_start, range.range_end]
  const own = useQuery({ queryKey: ownKey, queryFn: ({ signal }) => availability.own(crewId, selfMemberId, range, signal), enabled: mode === 'own', retry: false })
  const group = useQuery({ queryKey: [...prefix, 'crew', range.range_start, range.range_end, page, memberFilter?.id],
    queryFn: ({ signal }) => availability.list(crewId, range, page, memberFilter?.id, signal), enabled: mode === 'crew', retry: false })
  useBusinessSession(error ?? (mode === 'own' ? own.error : group.error))
  const staleBaseline = baseline !== null && own.data !== undefined && (baseline.lock_version !== own.data.lock_version || baseline.membership_joined_at !== own.data.membership_joined_at)
  function adopt(data: Calendar) {
    setBaseline(data)
    setDraft(data.intervals.length <= 100 ? data.intervals.map(item => ({ key: ++sequence.current, starts: local(item.starts_at), ends: local(item.ends_at), status: item.availability })) : [])
    setNeedsReload(false); setReview(false)
  }
  useEffect(() => { if (mode === 'own' && own.data && baseline === null && !own.isError) adopt(own.data) }, [mode, own.data, own.isError, baseline])

  function values(): CalendarInterval[] {
    if (!baseline) throw new AccountError('VALIDATION_ERROR')
    const result = draft.map(item => {
      if (!item.status) throw new AccountError('VALIDATION_ERROR')
      return { starts_at: iso(item.starts), ends_at: iso(item.ends), availability: item.status }
    }).sort((a, b) => Date.parse(a.starts_at) - Date.parse(b.starts_at))
    let cursor = Date.parse(baseline.range_start)
    for (const item of result) {
      if (Date.parse(item.starts_at) < cursor || Date.parse(item.ends_at) <= Date.parse(item.starts_at) || Date.parse(item.ends_at) > Date.parse(baseline.range_end)) throw new AccountError('VALIDATION_ERROR')
      cursor = Date.parse(item.ends_at)
    }
    return result
  }
  let changed = false
  try { changed = baseline !== null && canonical(values()) !== canonical(baseline.intervals) } catch { changed = draft.length > 0 }
  const tooDense = (baseline?.intervals.length ?? 0) > 100
  const editable = mode === 'own' && baseline !== null && !own.isError && !tooDense
  const disabled = pending || needsReload || staleBaseline || review || own.isFetching

  async function readRange() {
    if (lock.current) return
    lock.current = true; setPending(true); setError(null)
    try {
      const next = { range_start: iso(start), range_end: iso(end) }
      if (Date.parse(next.range_end) <= Date.parse(next.range_start) || Date.parse(next.range_end) - Date.parse(next.range_start) > 31 * day) throw new AccountError('VALIDATION_ERROR')
      setMessage(''); setReview(false); setNeedsReload(false); setPage(1)
      if (next.range_start !== range.range_start || next.range_end !== range.range_end) {
        setBaseline(null); setDraft([]); setRange(next)
      } else if (mode === 'own') {
        const fresh = await own.refetch(); if (fresh.isError) throw fresh.error; adopt(fresh.data!)
      } else { const fresh = await group.refetch(); if (fresh.isError) throw fresh.error }
    } catch (failure) { setError(failure) }
    finally { lock.current = false; setPending(false) }
  }

  async function rereadKeepingDraft() {
    if (lock.current) return
    lock.current = true; setPending(true); setError(null)
    try {
      const fresh = await own.refetch(); if (fresh.isError) throw fresh.error
      setBaseline(fresh.data!); setNeedsReload(false); setReview(false)
      setMessage('已读取当前登记，草稿仍保留。请比较当前登记与草稿，重新核对后再决定是否保存。')
    } catch (failure) { setError(failure) }
    finally { lock.current = false; setPending(false) }
  }

  async function save() {
    if (lock.current || needsReload || staleBaseline || !review || !editable || !changed) return
    lock.current = true; setPending(true); setError(null)
    try {
      const result = await availability.replace(crewId, selfMemberId, { range_start: baseline!.range_start, range_end: baseline!.range_end,
        expected_lock_version: baseline!.lock_version, expected_membership_joined_at: baseline!.membership_joined_at, intervals: values() })
      cache.setQueryData(ownKey, result); adopt(result); setMessage('本人档期已保存。')
      try { await cache.invalidateQueries({ queryKey: [...prefix, 'crew'] }) } catch { /* Save is confirmed; a view refresh must not invite a replay. */ }
    } catch (failure) {
      setError(failure)
      if (!(failure instanceof AccountError) || ['VERSION_CONFLICT', 'MEMBERSHIP_CHANGED', 'NETWORK_UNCONFIRMED', 'INVALID_RESPONSE', 'SERVICE_UNAVAILABLE'].includes(failure.code) || failure.status >= 500) { setNeedsReload(true); setReview(false) }
    } finally { lock.current = false; setPending(false) }
  }

  const patchDraft = (key: number, change: Partial<Draft>) => { setMessage(''); setDraft(previous => previous.map(item => item.key === key ? { ...item, ...change } : item)) }
  return <Modal title={mode === 'own' ? '我的档期' : '剧组档期（只读）'} open onCancel={onClose} footer={null} width={900} closable={!pending} keyboard={!pending} mask={{ closable: !pending }}>
    <div className="availability-panel"><p>未登记的时间是未知状态，不代表可用。所有时间按北京时间输入和展示，可跨日。</p>
      <div className="task-form-grid"><div className="account-field"><label htmlFor="calendar-range-start">查询开始</label><input id="calendar-range-start" type="datetime-local" step="1" value={start} disabled={pending || review} onChange={event => setStart(event.target.value)} /></div>
        <div className="account-field"><label htmlFor="calendar-range-end">查询结束</label><input id="calendar-range-end" type="datetime-local" step="1" value={end} disabled={pending || review} onChange={event => setEnd(event.target.value)} /></div></div>
      <Button disabled={pending} onClick={() => { void readRange() }}>{mode === 'own' ? '读取所选范围并替换草稿' : '读取所选范围'}</Button><p className="caption">每次最多31天。{mode === 'own' && '读取所选范围会替换当前草稿；保存只替换已读取范围，范围外登记保留。'}</p>
      <ErrorNotice error={mode === 'own' ? own.error : group.error} /><ErrorNotice error={error} />
      {mode === 'own' && own.isPending && <p role="status">正在读取本人档期…</p>}
      {mode === 'own' && baseline && !own.isError && <>
        <h3>{baseline.display_name}的当前登记</h3><CalendarRows data={own.data ?? baseline} />
        {tooDense ? <p className="account-note">当前范围登记超过100段，请缩小范围后编辑。</p> : <>
          <h3>范围内登记草稿</h3><p className="caption">省略的时间段会变为未登记。时间段不允许重叠，相邻区间可以衔接。</p>
          {draft.length === 0 && <p>当前草稿没有时间段；保存后该范围将全部变为未登记。</p>}
          {draft.map((item, index) => <fieldset className="availability-draft" key={item.key} disabled={disabled}><legend>时间段 {index + 1}</legend>
            <div className="task-form-grid"><div className="account-field"><label htmlFor={`calendar-start-${item.key}`}>开始时间 {index + 1}</label><input id={`calendar-start-${item.key}`} type="datetime-local" step="1" value={item.starts} onChange={event => patchDraft(item.key, { starts: event.target.value })} /></div>
              <div className="account-field"><label htmlFor={`calendar-end-${item.key}`}>结束时间 {index + 1}</label><input id={`calendar-end-${item.key}`} type="datetime-local" step="1" value={item.ends} onChange={event => patchDraft(item.key, { ends: event.target.value })} /></div></div>
            <div className="account-field"><label htmlFor={`calendar-status-${item.key}`}>可用状态 {index + 1}</label><select id={`calendar-status-${item.key}`} value={item.status} onChange={event => patchDraft(item.key, { status: event.target.value as Draft['status'] })}><option value="">请选择状态</option><option value="available">可用</option><option value="unavailable">不可用</option></select></div>
            <Button onClick={() => { setMessage(''); setDraft(previous => previous.filter(value => value.key !== item.key)) }}>移除时间段 {index + 1}</Button>
          </fieldset>)}
          <Button disabled={disabled || draft.length >= 100} onClick={() => { setMessage(''); setDraft(previous => [...previous, { key: ++sequence.current, starts: '', ends: '', status: '' }]) }}>添加时间段</Button>
          {review && <div className="account-note"><h3>核对档期替换</h3><p>{display(baseline.range_start)} 至 {display(baseline.range_end)}（北京时间）</p><p>将用草稿中的 {draft.length} 段登记替换该范围；未列出的部分变为未知，范围外保持不变。</p>{draft.length === 0 && <p>本次将清空所选范围内的登记。</p>}</div>}
          {(needsReload || staleBaseline) && <div className="account-note"><p>保存结果未确认或当前版本已变化。草稿保留，不自动再次提交。</p><Button disabled={pending} onClick={() => { void rereadKeepingDraft() }}>读取当前档期并保留草稿</Button></div>}
          <div className="role-actions">{review ? <><Button disabled={pending} onClick={() => setReview(false)}>返回修改草稿</Button><Button type="primary" aria-label="确认替换本人档期" loading={pending} disabled={!changed || needsReload || staleBaseline || own.isFetching} onClick={() => { void save() }}>确认替换本人档期</Button></>
            : <Button type="primary" disabled={!changed || pending || needsReload || staleBaseline || own.isFetching} onClick={() => { try { values(); setError(null); setReview(true) } catch (failure) { setError(failure) } }}>核对档期修改</Button>}</div>
        </>}
      </>}
      {mode === 'crew' && <>
        <div className="member-actions">{memberFilter && <><span>仅查看：{memberFilter.name}</span><Button onClick={() => { setMemberFilter(null); setPage(1) }}>返回全组档期</Button></>}</div>
        {group.isPending && <p role="status">正在读取授权范围内的档期…</p>}
        {!group.isError && group.data && <>{group.data.items.length === 0 && <p>当前范围没有可查看的在组成员。</p>}{group.data.items.map(item => <section key={item.member_id} className="task-submission"><h3>{item.display_name}</h3><CalendarRows data={item} />{!memberFilter && <Button onClick={() => { setMemberFilter({ id: item.member_id, name: item.display_name }); setPage(1) }}>仅查看{item.display_name}的档期</Button>}</section>)}
          <div className="list-pagination"><Button disabled={page === 1 || group.isFetching} onClick={() => setPage(page - 1)}>上一页成员档期</Button><span>第 {page} 页 · 共 {group.data.total} 名成员</span><Button disabled={page * 20 >= group.data.total || group.isFetching} onClick={() => setPage(page + 1)}>下一页成员档期</Button></div></>}
        <p className="caption">这里仅查看登记，不能代替其他成员修改。</p>
      </>}
      {message && <p role="status">{message}</p>}<p className="module-note">档期仅表示成员登记，相关拍摄安排请另行与剧组核对。</p>
    </div>
  </Modal>
}
