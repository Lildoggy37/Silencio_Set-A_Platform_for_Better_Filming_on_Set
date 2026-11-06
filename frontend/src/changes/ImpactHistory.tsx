import { useRef, useState } from 'react'
import { Button } from 'antd'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useAuth } from '../auth/AuthProvider'
import { ErrorNotice } from '../auth/AccountScreens'
import { AccountError } from '../api/accounts'
import type { ChangeCategory, ImpactCheck } from '../api/changes'
import { useBusinessSession } from '../crews/useBusinessSession'
import { changeReasonNames, resolutionNames, transitionNames } from './labels'

const display = (value: string) => new Date(value).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' })
const stateName = (state: string) => state === 'open' ? '记录为待处理' : '记录为不再待处理'

export function ImpactHistory({ crewId, userId, category, onLock }: { crewId: string; userId: string; category: ChangeCategory; onLock: (locked: boolean) => void }) {
  const { changes } = useAuth()
  const cache = useQueryClient()
  const [open, setOpen] = useState(false), [status, setStatus] = useState<'all' | 'open' | 'resolved'>('all'), [page, setPage] = useState(1)
  const [issueId, setIssueId] = useState<string | null>(null), [eventPage, setEventPage] = useState(1)
  const [command, setCommand] = useState<{ key: string } | null>(null), [busy, setBusy] = useState(false), [uncertain, setUncertain] = useState(false)
  const [result, setResult] = useState<ImpactCheck | null>(null), [error, setError] = useState<unknown>(null)
  const running = useRef(false)
  const list = useQuery({ queryKey: ['crews',userId,crewId,'change-records',category,status,page], queryFn: ({ signal }) => changes.records(crewId,category,status,page,signal), enabled: open, retry: false })
  const history = useQuery({ queryKey: ['crews',userId,crewId,'change-record-history',category,issueId,eventPage], queryFn: ({ signal }) => changes.history(crewId,category,issueId!,eventPage,signal), enabled: open && !!issueId && !list.isError, retry: false })
  useBusinessSession(error ?? list.error ?? history.error)
  async function check(key: string) {
    if (running.current) return
    running.current=true; setBusy(true); setError(null); onLock(true)
    try {
      const checked=await changes.check(crewId,category,key)
      setResult(checked); setCommand(null); setUncertain(false); setOpen(true); onLock(false)
      void cache.invalidateQueries({ queryKey: ['crews',userId,crewId] })
    } catch (failure) {
      setError(failure)
      const unknown=!(failure instanceof AccountError) || ['NETWORK_UNCONFIRMED','INVALID_RESPONSE','SERVICE_UNAVAILABLE'].includes(failure.code) || failure.status>=500
      setUncertain(unknown)
      if (!unknown) { setCommand(null); onLock(false); void cache.invalidateQueries({ queryKey: ['crews',userId,crewId] }) }
    } finally { running.current=false; setBusy(false) }
  }
  function start() {
    if (running.current) return
    const next={ key:crypto.randomUUID() }
    setCommand(next); setResult(null); void check(next.key)
  }
  const data=list.isError ? undefined : list.data
  const detail=!list.isError && !history.isError ? history.data : undefined
  return <section className="creative-panel"><h3>检查与问题记录</h3>
    <p className="caption">普通刷新不写历史。检查会完整记录当前类别，单次最多1000个待处理目标；历史只记录实际检查到的变化，不能替代实时清单。</p>
    <div className="member-actions"><Button aria-label={uncertain ? '用原检查请求重试' : '检查并记录当前类别'} loading={busy} onClick={() => { if (uncertain && command) void check(command.key); else start() }}>{uncertain ? '用原检查请求重试' : '检查并记录当前类别'}</Button><Button disabled={!!command} onClick={() => setOpen(!open)}>查看问题记录</Button></div>
    <ErrorNotice error={error} />{uncertain && <p role="alert">检查结果尚未确认，请用原请求重试，暂不能切换类别。</p>}
    {result && <p role="status">本次记录：发现 {result.detected_count} · 依据更新 {result.updated_count} · 不再待处理 {result.resolved_count} · 再次发现 {result.reopened_count}；截至 {display(result.checked_at)}（北京时间）。</p>}
    {open && <><label>记录状态<select value={status} disabled={!!command} onChange={event => { setStatus(event.target.value as typeof status); setPage(1); setIssueId(null) }}><option value="all">全部记录</option><option value="open">记录为待处理</option><option value="resolved">记录为不再待处理</option></select></label><Button aria-label="刷新问题记录" loading={list.isFetching} disabled={!!command} onClick={() => { void list.refetch() }}>刷新问题记录</Button>
      <ErrorNotice error={list.error} />{list.isPending && <p role="status">正在读取问题记录…</p>}
      {data && <>{data.latest_check ? <p>最近成功记录：{display(data.latest_check.checked_at)}（北京时间），当时待处理 {data.latest_check.open_count} 项。后续业务变化需再次检查。</p> : <p>此类别尚未执行检查记录。</p>}
        <ul className="crew-list">{data.items.map(item => <li key={item.id}><div><strong>{item.target.title}</strong><p>{stateName(item.status)} · 观察到复发 {item.reopened_count} 次</p><p>首次发现 {display(item.first_detected_at)} · 最近检查 {display(item.last_checked_at)}</p>{item.resolution_reason && <p>{resolutionNames[item.resolution_reason]}</p>}</div><Button disabled={!!command} onClick={() => { setIssueId(item.id); setEventPage(1) }}>查看变化历史</Button></li>)}</ul>
        {data.items.length===0 && <p>当前筛选下没有已记录的问题。</p>}
        <div className="list-pagination"><Button disabled={page===1 || !!command} onClick={() => { setPage(page-1); setIssueId(null) }}>上一页问题记录</Button><Button disabled={page*20>=data.total || !!command} onClick={() => { setPage(page+1); setIssueId(null) }}>下一页问题记录</Button></div></>}
      {issueId && !list.isError && <><ErrorNotice error={history.error} />{history.isPending && <p role="status">正在读取变化历史…</p>}{detail && <section><h4>{detail.issue.target.title} · 观察历史</h4><ul className="resource-bookings">{detail.items.map(event => <li key={event.id}><strong>{transitionNames[event.kind]} · {display(event.recorded_at)}（北京时间）</strong><p>{event.before_status ? `${stateName(event.before_status)} → ` : ''}{stateName(event.after_status)}</p><p>当时的问题：{event.target.reason_codes.map(code => changeReasonNames[code]).join('、')}</p>{event.resolution_reason && <p>本次不再列出的依据：{resolutionNames[event.resolution_reason]}</p>}</li>)}</ul><div className="list-pagination"><Button disabled={eventPage===1 || !!command} onClick={() => setEventPage(eventPage-1)}>上一页变化历史</Button><Button disabled={eventPage*20>=detail.total || !!command} onClick={() => setEventPage(eventPage+1)}>下一页变化历史</Button></div></section>}</>}
    </>}
  </section>
}
