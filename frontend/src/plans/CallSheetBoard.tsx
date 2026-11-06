import { useRef, useState } from 'react'
import { Button, Checkbox, Input, Modal } from 'antd'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useAuth } from '../auth/AuthProvider'
import { ErrorNotice } from '../auth/AccountScreens'
import { AccountError } from '../api/accounts'
import type { PlanVersion } from '../api/plans'
import type { PlanTarget } from '../api/changes'
import type { CallSheetVersion, SheetAction, SheetCommand, SheetContent } from '../api/callSheets'
import { useBusinessSession } from '../crews/useBusinessSession'
import { PublicationReceipts } from '../notifications/PublicationReceipts'
import { CallSheetPdfExport } from './CallSheetPdfExport'

const names = { draft: '草稿', pending_review: '待审核', approved: '已审核', published: '已发布' }
const validity = { valid: '当前有效', review_required: '来源有变化，待复核', superseded: '已有新版', withdrawn: '已撤回，不再作为拍摄安排' }
const titles: Record<SheetAction, string> = { create: '按拍摄日生成通告', version: '创建通告新版本', edit: '编辑集合与注意事项', recipients: '选择通告接收人', submit: '提交通告审核', review: '审核通告', publish: '发布通告' }
function local(value: string) {
  const parts = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23' }).formatToParts(new Date(value))
  const get = (name: string) => parts.find(part => part.type === name)!.value
  return `${get('year')}-${get('month')}-${get('day')}T${get('hour')}:${get('minute')}:${get('second')}`
}
const display = (value: string) => local(value).replace('T', ' ')

function gatheringIssue(draft: SheetContent) {
  if (!draft.gathering_at || !draft.gathering_location?.trim()) {
    return '请在「编辑通告内容」中补充集合时间和地点后再发布。'
  }
  const gatheringTime = Date.parse(draft.gathering_at)
  if (draft.arrangement_snapshot.sessions.some(session => Date.parse(session.starts_at) < gatheringTime)) {
    return '集合时间晚于当天第一场拍摄，请在「编辑通告内容」中调整后再发布。'
  }
  return null
}

export function CallSheetBoard({ crewId, userId, source, sourceIsCurrent, permissions, initialTarget }: { crewId: string; userId: string; source: PlanVersion; sourceIsCurrent: boolean; permissions: string[]; initialTarget?: PlanTarget }) {
  const { callSheets } = useAuth()
  const cache = useQueryClient()
  const [page, setPage] = useState(1), [versionPage, setVersionPage] = useState(1), [reviewPage, setReviewPage] = useState(1)
  const [sheetId, setSheetId] = useState<string | null>(initialTarget?.sheetId ?? null), [versionId, setVersionId] = useState<string | null>(initialTarget?.sheetVersionId ?? null)
  const [action, setAction] = useState<SheetAction | null>(null), [history, setHistory] = useState(false)
  const canEdit = permissions.includes('call_sheet.edit'), canReview = permissions.includes('call_sheet.review'), canPublish = permissions.includes('call_sheet.publish')
  const full = canEdit || canReview || canPublish
  const canGenerate = canEdit && sourceIsCurrent && !source.withdrawn_at && source.review_reasons.length === 0 && permissions.some(code => ['plan.edit', 'plan.read_all'].includes(code))
  const list = useQuery({ queryKey: ['crews', userId, crewId, 'call-sheets', source.plan_id, page], queryFn: ({ signal }) => callSheets.list(crewId, source.plan_id, page, signal), retry: false })
  const versions = useQuery({ queryKey: ['crews', userId, crewId, 'call-sheet-versions', sheetId, versionPage], queryFn: ({ signal }) => callSheets.versions(crewId, sheetId!, versionPage, signal), enabled: !!sheetId, retry: false })
  const detail = useQuery({ queryKey: ['crews', userId, crewId, 'call-sheet-version', versionId], queryFn: () => callSheets.get(crewId, versionId!), enabled: !!versionId, retry: false })
  const reviews = useQuery({ queryKey: ['crews', userId, crewId, 'call-sheet-reviews', versionId, reviewPage], queryFn: () => callSheets.reviews(crewId, versionId!, reviewPage), enabled: history && !!versionId && full, retry: false })
  useBusinessSession(list.error ?? versions.error ?? detail.error ?? reviews.error)
  const current = detail.isError ? undefined : detail.data
  const submissionIssue = (canEdit || canPublish) && current?.status === 'draft' ? gatheringIssue(current) : null
  function saved(result: CallSheetVersion) {
    setAction(null); setSheetId(result.call_sheet.id); setVersionId(result.id)
    cache.setQueryData(['crews', userId, crewId, 'call-sheet-version', result.id], result)
    void cache.invalidateQueries({ queryKey: ['crews', userId, crewId] })
  }
  return <section className="plan-editor call-sheet-board"><header className="call-sheet-heading"><div><p className="schedule-eyebrow">DAILY CALL SHEET</p><h3>每日通告</h3><p className="call-sheet-intro">把拍摄安排送达每一位成员。</p></div><div className="member-actions"><Button onClick={() => { void list.refetch(); if (versionId) { void detail.refetch(); void versions.refetch() } }}>刷新通告</Button>{canEdit && <Button type="primary" disabled={!canGenerate} onClick={() => setAction('create')}>生成每日通告</Button>}</div></header>
    {canEdit && !canGenerate && <p className="call-sheet-availability">生成新稿需先选择当前且无需复核的正式排期。</p>}
    <ErrorNotice error={list.error ?? versions.error ?? detail.error} />{list.isPending && <p role="status">正在读取通告…</p>}
    {!list.isError && list.data && <><div className="member-actions">{list.data.items.map(sheet => <Button key={sheet.id} onClick={() => { setSheetId(sheet.id); setVersionId(null); setVersionPage(1); setHistory(false) }}>{sheet.shooting_date} 通告</Button>)}</div>{list.data.total === 0 && <p>暂无可查看的通告。</p>}<div className="list-pagination"><Button disabled={page === 1} onClick={() => setPage(page - 1)}>上一页通告</Button><span>共 {list.data.total} 份</span><Button disabled={page * 20 >= list.data.total} onClick={() => setPage(page + 1)}>下一页通告</Button></div></>}
    {sheetId && <>{canEdit && <Button disabled={!canGenerate} onClick={() => setAction('version')}>按当前排期创建通告新版本</Button>}{versions.isPending && <p role="status">正在读取通告版本…</p>}{!versions.isError && versions.data && <><div className="member-actions">{versions.data.items.map(version => <Button key={version.id} onClick={() => { setVersionId(version.id); setHistory(false); setReviewPage(1) }}>通告第 {version.version_no} 版 · {names[version.status]}</Button>)}</div><div className="list-pagination"><Button disabled={versionPage === 1} onClick={() => setVersionPage(versionPage - 1)}>上一页通告版本</Button><span>共 {versions.data.total} 版</span><Button disabled={versionPage * 20 >= versions.data.total} onClick={() => setVersionPage(versionPage + 1)}>下一页通告版本</Button></div></>}</>}
    {versionId && detail.isPending && <p role="status">正在读取通告正文…</p>}
    {current && <><h4>通告第 {current.version_no} 版 · {names[current.status]}</h4>{current.validity_status && <p role={current.validity_status === 'valid' ? 'status' : 'alert'}>{validity[current.validity_status]}</p>}{!current.source_current && <p role="alert">来源排期已更新或待复核；请按当前正式排期创建新版本。</p>}
      <div className="member-actions">{canEdit && ['draft', 'approved'].includes(current.status) && <><Button onClick={() => setAction('edit')}>编辑通告内容</Button>{permissions.includes('member.read') && <Button onClick={() => setAction('recipients')}>编辑通告接收人</Button>}</>}{canEdit && !canPublish && current.status === 'draft' && <Button disabled={!current.source_current || submissionIssue !== null} onClick={() => setAction('submit')}>送审通告</Button>}{canReview && current.status === 'pending_review' && <Button onClick={() => setAction('review')}>审核当前通告</Button>}{canPublish && (current.status === 'approved' || canReview && ['draft', 'pending_review'].includes(current.status)) && <Button type="primary" aria-label="发布当前通告" disabled={!current.source_current || submissionIssue !== null} onClick={() => setAction('publish')}>预览并发布通告</Button>}{full && <Button onClick={() => setHistory(!history)}>通告审核历史</Button>}</div>
      {submissionIssue && <p className="caption" role="status">{submissionIssue}</p>}
      {current.status === 'published' && <PublicationReceipts key={current.id} crewId={crewId} userId={userId} kind="call_sheet" versionId={current.id} permissions={permissions} />}
      <CallSheetPreview content={current} /><p>接收人：{current.recipients.map(person => `${person.display_name}${person.required ? '（必选）' : ''}`).join('、')}</p>
      {permissions.includes('call_sheet.export') && <CallSheetPdfExport key={`${current.id}-${current.lock_version}`} crewId={crewId} version={current} />}
      {history && <><ErrorNotice error={reviews.error} />{reviews.isPending && <p role="status">正在读取审核历史…</p>}{!reviews.isError && reviews.data && <><ul className="resource-bookings">{reviews.data.items.map(review => <li key={review.id}><strong>{review.decision === 'accept' ? '通过' : '退回'} · {display(review.reviewed_at)} · 依据版本 {review.reviewed_lock_version}</strong><p>{review.review_note}</p><details><summary>查看当时审核内容与接收人</summary><CallSheetPreview content={review.content_snapshot} /><p>接收人：{review.content_snapshot.recipients.map(person => person.display_name).join('、')}</p></details></li>)}</ul><div className="list-pagination"><Button disabled={reviewPage === 1} onClick={() => setReviewPage(reviewPage - 1)}>上一页通告审核</Button><Button disabled={reviewPage * 20 >= reviews.data.total} onClick={() => setReviewPage(reviewPage + 1)}>下一页通告审核</Button></div></>}</>}
    </>}
    {action && <CallSheetAction key={`${action}-${current?.id}`} action={action} crewId={crewId} userId={userId} sheetId={sheetId} source={source} current={action === 'create' ? undefined : current} onClose={() => setAction(null)} onSaved={saved} />}
  </section>
}

export function CallSheetPreview({ content }: { content: SheetContent }) {
  const snapshot = content.arrangement_snapshot
  return <div className="call-sheet-preview"><p><strong>{snapshot.project_name} · {snapshot.shooting_date}</strong> · {snapshot.plan_name}第 {snapshot.plan_version_no} 版</p><p>集合：{content.gathering_at ? display(content.gathering_at) : '未填写'} · {content.gathering_location ?? '未填写'}</p><p>联络说明：{content.contact_note ?? '未填写'}</p><p className="preserve-lines">注意事项：{content.notes ?? '未填写'}</p><div className="directory-scroll"><table className="availability-table"><caption>当天拍摄安排 · 北京时间</caption><thead><tr><th>场次</th><th>起止时间</th><th>场地</th><th>人员与资源</th></tr></thead><tbody>{snapshot.sessions.map((item, index) => <tr key={item.id ?? index}><td>{item.scene_label}</td><td>{display(item.starts_at)}<br />— {display(item.ends_at)}</td><td>{item.location_name}</td><td>{item.members.map(person => person.display_name).join('、')}<br />{item.resources.map(resource => `${resource.name} ${resource.quantity}${resource.unit}`).join('、')}</td></tr>)}</tbody></table></div>{snapshot.overnight_continuations.map((item, index) => <p className="caption" key={index}>前一日延续拍摄：{item.scene_label}，至 {display(item.ends_at)}；请核对休息与集合安排。</p>)}</div>
}

function CallSheetAction({ action, crewId, userId, sheetId, source, current, onClose, onSaved }: { action: SheetAction; crewId: string; userId: string; sheetId: string | null; source: PlanVersion; current?: CallSheetVersion; onClose: () => void; onSaved: (value: CallSheetVersion) => void }) {
  const { callSheets } = useAuth()
  const [base, setBase] = useState(current)
  const dates = [...new Set(source.sessions.map(item => local(item.starts_at).slice(0, 10)))].sort()
  const [day, setDay] = useState('')
  const [gathering, setGathering] = useState(current?.gathering_at ? local(current.gathering_at) : ''), [location, setLocation] = useState(current?.gathering_location ?? '')
  const [contact, setContact] = useState(current?.contact_note ?? ''), [notes, setNotes] = useState(current?.notes ?? '')
  const [selected, setSelected] = useState(current?.recipients.map(person => person.member_id) ?? []), [page, setPage] = useState(1)
  const [decision, setDecision] = useState('accept'), [reviewNote, setReviewNote] = useState('')
  const [command, setCommand] = useState<SheetCommand | null>(null), [pending, setPending] = useState(false), [uncertain, setUncertain] = useState(false), [needsReload, setNeedsReload] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const lock = useRef(false)
  const stateAllowsAction = !base || ['create', 'version'].includes(action)
    || ['edit', 'recipients'].includes(action) && ['draft', 'approved'].includes(base.status)
    || action === 'submit' && base.status === 'draft'
    || action === 'review' && base.status === 'pending_review'
    || action === 'publish' && ['draft', 'pending_review', 'approved'].includes(base.status)
  const options = useQuery({ queryKey: ['crews', userId, crewId, 'call-sheet-recipient-options', base?.id, page], queryFn: ({ signal }) => callSheets.recipients(crewId, base!.id, page, signal), enabled: action === 'recipients', retry: false })
  useBusinessSession(error ?? options.error)
  function prepare() {
    if (lock.current || uncertain || needsReload) return
    const body = action === 'create' ? { source_plan_version_id: source.id, shooting_date: day }
      : action === 'version' ? { source_plan_version_id: source.id, copy_from: base?.id ?? null }
      : { expected_lock_version: base!.lock_version, ...(action === 'edit' ? { gathering_at: gathering ? new Date(`${gathering}+08:00`).toISOString() : null, gathering_location: location.trim() || null, contact_note: contact.trim() || null, notes: notes.trim() || null }
        : action === 'recipients' ? { member_ids: selected } : action === 'review' ? { decision, review_note: reviewNote.trim() || null }
          : action === 'publish' ? { expected_current_published_version_id: base!.call_sheet.current_published_version_id } : {}) }
    const prepared: SheetCommand = { action, target: action === 'create' ? null : action === 'version' ? sheetId : base!.id, body, key: crypto.randomUUID() }
    setCommand(prepared); setError(null)
    if (['edit', 'recipients', 'publish'].includes(action)) void submit(prepared)
  }
  async function submit(prepared = command) {
    if (lock.current || !prepared) return
    lock.current = true; setPending(true); setError(null)
    try { onSaved(await callSheets.write(crewId, prepared)) }
    catch (failure) {
      setError(failure)
      const unknown = !(failure instanceof AccountError) || failure.status >= 500 || ['NETWORK_UNCONFIRMED', 'INVALID_RESPONSE', 'SERVICE_UNAVAILABLE'].includes(failure.code)
      setUncertain(unknown)
      if (!unknown) { setCommand(null); setNeedsReload(!!base && failure instanceof AccountError && ['VERSION_CONFLICT', 'CURRENT_VERSION_CHANGED', 'SOURCE_PLAN_STALE', 'RECIPIENT_SET_INVALID', 'RECIPIENT_INACTIVE'].includes(failure.code)) }
    } finally { lock.current = false; setPending(false) }
  }
  async function reload() {
    setPending(true); setError(null)
    try { setBase(await callSheets.get(crewId, base!.id)); setNeedsReload(false) }
    catch (failure) { setError(failure) }
    finally { setPending(false) }
  }
  return <Modal title={titles[action]} open width={760} footer={null} onCancel={onClose} closable={!pending && !uncertain} keyboard={!pending && !uncertain} mask={{ closable: !pending && !uncertain }}><div className="role-editor"><ErrorNotice error={error} />
    {needsReload && <Button loading={pending} onClick={() => { void reload() }}>读取最新通告并保留输入</Button>}{uncertain && <p role="alert">结果尚未确认，请使用原请求重试，内容与接收人保持不变。</p>}
    {command ? <><p><strong>确认{titles[action]}？</strong></p>{action === 'recipients' && <p>共选择 {selected.length} 人，内容与接收集合需要一并审核。</p>}{action === 'publish' && <p>发布后不可覆盖，并将生成站内通知。发布成功不代表成员已经收到或知悉，请提醒成员主动查收。</p>}<div className="member-actions"><Button disabled={pending || uncertain} onClick={() => setCommand(null)}>返回修改通告</Button><Button type="primary" loading={pending} aria-label={uncertain ? '用原通告请求重试' : '确认通告操作'} onClick={() => { void submit() }}>{uncertain ? '用原通告请求重试' : '确认通告操作'}</Button></div></>
      : <form onSubmit={event => { event.preventDefault(); prepare() }}>{!stateAllowsAction && <p role="alert">通告状态已变化，请关闭窗口后选择当前可执行的操作。</p>}<fieldset className="custody-fields" disabled={pending || needsReload || !stateAllowsAction}>
        {action === 'create' ? <label>通告拍摄日期<select required value={day} onChange={event => setDay(event.target.value)}><option value="">请选择日期</option>{dates.map(value => <option key={value} value={value}>{value}</option>)}</select></label>
          : action === 'version' ? <p>按当前选中的正式排期重新生成正文，可复制补充内容；额外接收人需要重新选择。</p>
            : action === 'edit' ? <><label>集合时间<Input type="datetime-local" step={1} value={gathering} onChange={event => setGathering(event.target.value)} /></label><p className="caption">北京时间；可早于拍摄当天，不得晚于第一场拍摄。</p><label>集合地点<Input maxLength={300} value={location} onChange={event => setLocation(event.target.value)} /></label><label>联络说明<Input.TextArea maxLength={500} value={contact} onChange={event => setContact(event.target.value)} /></label><label>注意事项<Input.TextArea maxLength={2000} value={notes} onChange={event => setNotes(event.target.value)} /></label>{base?.status === 'approved' && <p>修改后将恢复草稿，发布前需要重新预览确认。</p>}</>
              : action === 'recipients' ? <><p>当天参与人员为必选，额外接收人可跨页选择。</p><ErrorNotice error={options.error} />{base!.recipients.map(person => <div className="member-actions" key={person.member_id}><Checkbox checked={selected.includes(person.member_id)} disabled={person.required} onChange={event => setSelected(event.target.checked ? [...selected, person.member_id] : selected.filter(id => id !== person.member_id))}>{person.display_name}{person.required ? '（必选）' : '（原接收人）'}</Checkbox></div>)}{options.isPending && <p role="status">正在读取可选成员…</p>}{!options.isError && options.data && <><div className="role-editor">{options.data.items.filter(person => !base!.recipients.some(existing => existing.member_id === person.member_id)).map(person => <Checkbox key={person.member_id} checked={selected.includes(person.member_id)} disabled={person.required} onChange={event => setSelected(event.target.checked ? [...selected, person.member_id] : selected.filter(id => id !== person.member_id))}>{person.display_name}</Checkbox>)}</div><div className="list-pagination"><Button disabled={page === 1} onClick={() => setPage(page - 1)}>上一页通告成员</Button><span>已选 {selected.length} 人</span><Button disabled={page * 20 >= options.data.total} onClick={() => setPage(page + 1)}>下一页通告成员</Button></div></>}</>
                : action === 'review' ? <><label>通告审核决定<select value={decision} onChange={event => setDecision(event.target.value)}><option value="accept">通过</option><option value="reject">退回草稿</option></select></label><label>通告审核说明<Input.TextArea required={decision === 'reject'} maxLength={500} value={reviewNote} onChange={event => setReviewNote(event.target.value)} /></label></>
                  : action === 'publish' ? <><CallSheetPreview content={base!} /><p>接收人：{base!.recipients.map(person => person.display_name).join('、')}</p><p className="caption">确认后发布这份通告并生成站内通知。发布成功不代表成员已经收到或知悉，请提醒成员主动查收。</p></> : <p>提交后交给发布负责人确认。</p>}
        <Button htmlType="submit" type="primary">{action === 'publish' ? '确认发布通告' : ['edit', 'recipients'].includes(action) ? '保存通告修改' : '核对通告操作'}</Button>
      </fieldset></form>}
  </div></Modal>
}
