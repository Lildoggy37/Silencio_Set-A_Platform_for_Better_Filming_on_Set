import { useEffect, useRef, useState } from 'react'
import { Button, Checkbox, Input, Modal } from 'antd'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useAuth } from '../auth/AuthProvider'
import { ErrorNotice } from '../auth/AccountScreens'
import { AccountError } from '../api/accounts'
import type { Plan, PlanVersion, PlanSession, PlanCommand, PlanCheck, PlanChoice, PlanChoiceKind, PlanIssue, PlanPublishBody } from '../api/plans'
import { useBusinessSession } from '../crews/useBusinessSession'
import { CallSheetBoard } from './CallSheetBoard'
import { CastingLibrary } from '../scripts/CastingPanel'
import { PlanWithdrawal } from './PlanWithdrawal'
import { PublicationReceipts } from '../notifications/PublicationReceipts'
import { planReviewNames as reviewNames } from './reviewLabels'
import type { PlanTarget } from '../api/changes'

function local(value: string) {
  if (!value) return ''
  const parts = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23' }).formatToParts(new Date(value))
  const get = (name: string) => parts.find(part => part.type === name)!.value
  return `${get('year')}-${get('month')}-${get('day')}T${get('hour')}:${get('minute')}:${get('second')}`
}
const fromLocal = (value: string) => value ? new Date(`${value}+08:00`).toISOString() : ''
const actionNames = { create: '创建拍摄计划', version: '创建计划草稿', save: '保存场次安排' }


export function PlanBoard({ crewId, userId, permissions, initialTarget }: { crewId: string; userId: string; permissions: string[]; initialTarget?: PlanTarget }) {
  const { plans } = useAuth()
  const cache = useQueryClient()
  const [page, setPage] = useState(1), [versionPage, setVersionPage] = useState(1)
  const [planId, setPlanId] = useState<string | null>(initialTarget?.planId ?? null), [versionId, setVersionId] = useState<string | null>(initialTarget?.versionId ?? null)
  const [creating, setCreating] = useState(false), [name, setName] = useState('')
  const [command, setCommand] = useState<PlanCommand | null>(null)
  const [dirty, setDirty] = useState(false)
  const [casting, setCasting] = useState(false)
  const canEdit = permissions.includes('plan.edit')
  const list = useQuery({ queryKey: ['crews', userId, crewId, 'plans', page], queryFn: ({ signal }) => plans.list(crewId, page, signal), retry: false })
  const selectedPlan = useQuery({ queryKey: ['crews', userId, crewId, 'plan', planId], queryFn: ({ signal }) => plans.plan(crewId, planId!, signal), enabled: !!planId, retry: false })
  const versions = useQuery({ queryKey: ['crews', userId, crewId, 'plan-versions', planId, versionPage], queryFn: ({ signal }) => plans.versions(crewId, planId!, versionPage, signal), enabled: !!planId, retry: false })
  const detail = useQuery({ queryKey: ['crews', userId, crewId, 'plan-version', versionId], queryFn: () => plans.get(crewId, versionId!), enabled: !!versionId, retry: false })
  useBusinessSession(list.error ?? selectedPlan.error ?? versions.error ?? detail.error)
  function prepareVersion(copy?: string) { setCommand({ action: 'version', target: planId, body: { copy_from: copy ?? null }, key: crypto.randomUUID() }) }
  function saved(result: Plan | PlanVersion) {
    setCommand(null); setCreating(false); setName('')
    if ('sessions' in result) { setVersionId(result.id); cache.setQueryData(['crews', userId, crewId, 'plan-version', result.id], result) }
    else { setPlanId(result.id); setVersionId(null) }
    void cache.invalidateQueries({ queryKey: ['crews', userId, crewId] })
  }
  return <div className="role-editor"><div className="section-heading"><h3>拍摄计划</h3><div className="member-actions">{canEdit && permissions.includes('member.read') && <Button disabled={dirty} onClick={() => setCasting(!casting)}>安排人物与演员</Button>}{canEdit && <Button type="primary" disabled={dirty} onClick={() => setCreating(true)}>创建拍摄计划</Button>}</div></div>
    <p className="caption">选择计划查看拍摄安排；从正式排期生成每日通告。</p>
    {casting && <CastingLibrary crewId={crewId} userId={userId} permissions={permissions} />}
    <ErrorNotice error={list.error ?? selectedPlan.error ?? versions.error ?? detail.error} />
    {list.isPending && <p role="status">正在读取拍摄计划…</p>}
    {!list.isError && list.data && <><div className="plan-selector" aria-label="拍摄计划列表">{list.data.items.map((plan, index) => <button type="button" aria-label={plan.name} aria-pressed={planId === plan.id} disabled={dirty} key={plan.id} onClick={() => { setPlanId(plan.id); setVersionId(null); setVersionPage(1) }}><span className="plan-index" aria-hidden="true">{String((page - 1) * 20 + index + 1).padStart(2, '0')}</span><span className="plan-card-copy"><strong>{plan.name}</strong><small>{plan.current_published_version_id ? '已有正式排期' : '尚未发布正式排期'}</small></span><span aria-hidden="true">↗</span></button>)}</div>{list.data.total === 0 && <p>尚无拍摄计划。</p>}<div className="list-pagination"><Button disabled={page === 1 || dirty} onClick={() => setPage(page - 1)}>上一页计划</Button><span>共 {list.data.total} 份</span><Button disabled={page * 20 >= list.data.total || dirty} onClick={() => setPage(page + 1)}>下一页计划</Button></div></>}
    {planId && <><div className="plan-version-heading"><h4>选择计划版本</h4><div className="plan-toolbar">{canEdit && <Button disabled={dirty} onClick={() => prepareVersion()}>新建空白草稿</Button>}{canEdit && versionId && <Button disabled={dirty} onClick={() => prepareVersion(versionId)}>复制为新计划草稿</Button>}</div></div>
      {versions.isPending && <p role="status">正在读取计划版本…</p>}
      {!versions.isError && versions.data && <><div className="version-selector" aria-label="计划版本列表">{versions.data.items.map(version => <button type="button" className="version-option" key={version.id} aria-label={`计划第 ${version.version_no} 版 · ${version.withdrawn_at ? '已撤回' : version.status === 'published' ? '已发布' : '草稿'}`} aria-pressed={versionId === version.id} disabled={dirty} onClick={() => setVersionId(version.id)}><span className="version-number">{String(version.version_no).padStart(2, '0')}</span><span className="version-copy"><strong>第 {version.version_no} 版</strong><small>{version.withdrawn_at ? '已撤回' : version.status === 'published' ? '已发布' : '草稿'}</small></span></button>)}</div><div className="list-pagination"><Button disabled={versionPage === 1 || dirty} onClick={() => setVersionPage(versionPage - 1)}>上一页计划版本</Button><span>共 {versions.data.total} 版</span><Button disabled={versionPage * 20 >= versions.data.total || dirty} onClick={() => setVersionPage(versionPage + 1)}>下一页计划版本</Button></div></>}
    </>}
    {detail.isPending && versionId && <p role="status">正在读取场次安排…</p>}
    {!detail.isError && detail.data && <VersionEditor key={`editor-${detail.data.id}`} crewId={crewId} userId={userId} version={detail.data} canEdit={canEdit} canPublish={permissions.includes('plan.publish')} canCheck={canEdit && permissions.includes('availability.read_all') && permissions.includes('resource.read')} onDirty={setDirty} onSaved={saved} />}
    {!detail.isError && detail.data?.withdrawn_at && <p role="alert">本排期已撤回：{detail.data.withdrawal_reason}。原版本仅供历史核对。</p>}
    {!detail.isError && detail.data?.status === 'published' && <div className="member-actions plan-publication-actions"><PublicationReceipts key={detail.data.id} crewId={crewId} userId={userId} kind="plan" versionId={detail.data.id} permissions={permissions} />{canEdit && permissions.includes('plan.publish') && !detail.data.withdrawn_at && !selectedPlan.isError && selectedPlan.data?.current_published_version_id === detail.data.id && <PlanWithdrawal key={detail.data.id} crewId={crewId} userId={userId} version={detail.data} />}</div>}
    {!detail.isError && detail.data?.status === 'published' && permissions.some(code => ['call_sheet.read_related', 'call_sheet.edit', 'call_sheet.review', 'call_sheet.publish'].includes(code)) && <CallSheetBoard key={`call-sheet-${detail.data.id}`} crewId={crewId} userId={userId} source={detail.data} sourceIsCurrent={!selectedPlan.isError && selectedPlan.data?.current_published_version_id === detail.data.id} initialTarget={detail.data.id === initialTarget?.versionId ? initialTarget : undefined} permissions={permissions} />}
    <Modal title="创建拍摄计划" open={creating} footer={null} onCancel={() => setCreating(false)}><form className="role-editor" onSubmit={event => { event.preventDefault(); setCommand({ action: 'create', target: null, body: { name: name.trim() }, key: crypto.randomUUID() }); setCreating(false) }}><label>计划名称<Input required maxLength={100} value={name} onChange={event => setName(event.target.value)} /></label><Button htmlType="submit">核对计划名称</Button></form></Modal>
    {command && <PlanAction crewId={crewId} command={command} onClose={() => setCommand(null)} onSaved={saved} />}
  </div>
}

type Picker = { kind: PlanChoiceKind; index: number; resourceIndex?: number; location?: boolean }
function VersionEditor({ crewId, userId, version, canEdit, canCheck, canPublish, onDirty, onSaved }: { crewId: string; userId: string; version: PlanVersion; canEdit: boolean; canCheck: boolean; canPublish: boolean; onDirty: (dirty: boolean) => void; onSaved: (result: Plan | PlanVersion) => void }) {
  const { plans } = useAuth()
  const cache = useQueryClient()
  const [draft, setDraft] = useState<PlanSession[] | null>(null), [baseVersion, setBaseVersion] = useState<number | null>(null)
  const [picker, setPicker] = useState<Picker | null>(null), [command, setCommand] = useState<PlanCommand | null>(null)
  const [report, setReport] = useState<PlanCheck | null>(null), [error, setError] = useState<unknown>(null), [busy, setBusy] = useState(false)
  const [confirmed, setConfirmed] = useState<string[]>([])
  const [publishCommand, setPublishCommand] = useState<{ body: PlanPublishBody; key: string } | null>(null)
  const [justPublished, setJustPublished] = useState(false)
  const editable = canEdit && version.status === 'draft' && !justPublished
  const items = draft ?? version.sessions
  useBusinessSession(error)
  function change(next: PlanSession[]) { setBaseVersion(baseVersion ?? version.lock_version); setDraft(next); setReport(null); onDirty(true) }
  function edit(index: number, update: Partial<PlanSession>) { change(items.map((item, i) => i === index ? { ...item, ...update } : item)) }
  function select(choice: PlanChoice) {
    const target = picker!
    if (target.kind === 'scenes') {
      if (target.index === items.length) change([...items, { scene_revision_id: choice.id, scene_label: choice.label, starts_at: '', ends_at: '', transition_minutes: 0, location_resource_id: null, location_reservation_id: null, location_name: null, members: [], resources: [], note: null }])
      else edit(target.index, { scene_revision_id: choice.id, scene_label: choice.label })
    } else if (target.kind === 'members') edit(target.index, { members: [...items[target.index].members, { member_id: choice.id, membership_joined_at: choice.membership_joined_at!, display_name: choice.label }] })
    else if (target.kind === 'resources' && target.location) edit(target.index, { location_resource_id: choice.id, location_name: choice.label, location_reservation_id: null })
    else if (target.kind === 'resources') edit(target.index, { resources: [...items[target.index].resources, { resource_id: choice.id, name: choice.label, quantity: 1, unit: choice.unit!, resource_type: choice.resource_type!, reservation_id: null }] })
    else if (target.location) edit(target.index, { location_reservation_id: choice.id })
    else edit(target.index, { resources: items[target.index].resources.map((resource, i) => i === target.resourceIndex ? { ...resource, reservation_id: choice.id } : resource) })
    setPicker(null)
  }
  function save() {
    setCommand({ action: 'save', target: version.id, key: crypto.randomUUID(), body: { expected_lock_version: baseVersion ?? version.lock_version, sessions: items.map(item => ({
      scene_revision_id: item.scene_revision_id, starts_at: item.starts_at, ends_at: item.ends_at, transition_minutes: item.transition_minutes, note: item.note?.trim() || null,
      location_resource_id: item.location_resource_id, location_reservation_id: item.location_reservation_id,
      members: item.members.map(({ member_id, membership_joined_at }) => ({ member_id, membership_joined_at })),
      resources: item.resources.map(({ resource_id, quantity, reservation_id }) => ({ resource_id, quantity, reservation_id })),
    })) } })
  }
  async function check(forPublish = false) {
    setBusy(true); setError(null); setReport(null); setConfirmed([])
    try {
      const result = await plans.check(crewId, version.id, version.lock_version)
      setReport(result)
      if (forPublish && result.can_publish && result.unknown_items.length === 0) {
        setPublishCommand({ key: crypto.randomUUID(), body: { expected_lock_version: result.checked_lock_version,
          expected_current_published_version_id: result.expected_current_published_version_id, checked_data_revision: result.checked_data_revision,
          confirmed_unknown_item_ids: [] } })
      }
    }
    catch (failure) { setError(failure) }
    finally { setBusy(false) }
  }
  async function reload() {
    setBusy(true); setError(null); setReport(null)
    try { const result = await plans.get(crewId, version.id); setBaseVersion(result.lock_version); cache.setQueryData(['crews', userId, crewId, 'plan-version', version.id], result) }
    catch (failure) { setError(failure) }
    finally { setBusy(false) }
  }
  function saved(result: Plan | PlanVersion) { setCommand(null); setDraft(null); onDirty(false); setReport(null); setBaseVersion(null); onSaved(result) }
  function issueText(issue: PlanIssue) {
    const index = items.findIndex(item => item.id === issue.session_id)
    const person = items.flatMap(item => item.members).find(member => member.member_id === issue.member_id)
    return `${index >= 0 ? `第 ${index + 1} 场 · ` : ''}${person ? `${person.display_name} · ` : ''}${issue.message}${issue.starts_at ? `：${local(issue.starts_at).replace('T', ' ')} — ${local(issue.ends_at!).replace('T', ' ')}` : ''}`
  }
  const chosenResource = picker?.kind === 'reservations' ? picker.location ? items[picker.index].location_resource_id! : items[picker.index].resources[picker.resourceIndex!].resource_id : undefined
  function disabledChoice(choice: PlanChoice) {
    if (!picker) return false
    if (picker.kind === 'members') return items[picker.index].members.some(member => member.member_id === choice.id)
    if (picker.kind === 'resources') return picker.location ? choice.resource_type !== 'location' : choice.resource_type === 'location' || items[picker.index].resources.some(resource => resource.resource_id === choice.id)
    return false
  }
  if (!editable) return <section className="plan-editor schedule-publication">
    <header className="schedule-heading"><div><p className="schedule-eyebrow">SHOOTING SCHEDULE <span>VERSION {String(version.version_no).padStart(2, '0')}</span></p><h3 aria-label={`计划第 ${version.version_no} 版 · ${version.withdrawn_at ? '已撤回' : version.status === 'published' || justPublished ? '已发布' : '草稿'}`}>拍摄安排 <span className="schedule-status">{version.withdrawn_at ? '已撤回' : version.status === 'published' || justPublished ? '已发布' : '草稿'}</span></h3></div><Button loading={busy} onClick={() => { void reload() }}>刷新拍摄安排</Button></header>
    <ErrorNotice error={error} />
    {version.review_reasons.length > 0 && <p role="alert">排期待复核：{version.review_reasons.map(reason => reviewNames[reason]).join('、')}。</p>}
    <div className="schedule-table-wrap"><table className="plan-summary schedule-table"><caption className="sr-only">拍摄安排 · 北京时间</caption><thead><tr><th scope="col">时间 <span>北京时间</span></th><th scope="col">场戏与场地</th><th scope="col">参与人员与资源</th></tr></thead><tbody>{items.map((item, index) => {
      const starts = local(item.starts_at), ends = local(item.ends_at)
      const clock = (value: string) => value.slice(17) === '00' ? value.slice(11, 16) : value.slice(11)
      return <tr key={item.id ?? index}>
        <td className="schedule-time"><span className="schedule-date">{starts.slice(0, 10).replaceAll('-', '.')}</span><time className="schedule-start" dateTime={item.starts_at} aria-label={`开始 ${starts.replace('T', ' ')}`}>{clock(starts)}</time><time className="schedule-end" dateTime={item.ends_at} aria-label={`结束 ${ends.replace('T', ' ')}`}>— {starts.slice(0, 10) !== ends.slice(0, 10) && <span>{ends.slice(0, 10).replaceAll('-', '.')} / </span>}{clock(ends)}</time>{item.transition_minutes > 0 && <p className="schedule-detail">转场 {item.transition_minutes} 分钟</p>}</td>
        <td className="schedule-scene"><span className="schedule-sequence">场次 {String(index + 1).padStart(2, '0')}</span><strong>{item.scene_label}</strong>{item.location_name && <p className="schedule-location">{item.location_name}</p>}{item.note && <p className="schedule-detail">{item.note}</p>}</td>
        <td className="schedule-people"><div className="schedule-member-names">{item.members.map(member => member.display_name).join('、')}</div>{item.characters && item.characters.length > 0 && <p className="schedule-detail">{item.characters.map(person => `${person.character_name} · ${person.display_name ?? '未绑定'}`).join('、')}</p>}{item.resources.length > 0 && <p className="schedule-resources">{item.resources.map(resource => `${resource.name} ${resource.quantity}${resource.unit}`).join('、')}</p>}</td>
      </tr>
    })}</tbody></table></div>
  </section>
  return <section className="plan-editor"><h3>计划第 {version.version_no} 版 · {version.withdrawn_at ? '已撤回' : version.status === 'published' || justPublished ? '已发布' : '草稿'}{draft && ' · 有未保存修改'}</h3>{version.review_reasons.length > 0 && <p role="alert">排期待复核：{version.review_reasons.map(reason => reviewNames[reason]).join('、')}。请检查后发布调整版本。</p>}<p className="caption">所有时间均为北京时间，支持跨日；转场分钟计入参与人员的占用时间。</p><ErrorNotice error={error} />
    <div className="member-actions"><Button loading={busy} onClick={() => { void reload() }}>读取最新版本并保留编辑</Button>{editable && <Button disabled={busy || items.length >= 100} onClick={() => setPicker({ kind: 'scenes', index: items.length })}>添加拍摄场次</Button>}{canCheck && editable && <Button disabled={!!draft || busy} onClick={() => { void check() }}>检查排期冲突</Button>}{canPublish && editable && <Button type="primary" disabled={!!draft || busy} onClick={() => { void check(true) }}>发布排期</Button>}{draft && <Button onClick={() => { setDraft(null); setBaseVersion(null); onDirty(false); setReport(null) }}>放弃排期编辑</Button>}</div>
    <form onSubmit={event => { event.preventDefault(); save() }}><fieldset className="custody-fields" disabled={!editable || busy}>
      {items.map((item, index) => <fieldset className="creative-item" key={index}><legend>拍摄场次 {index + 1}</legend><strong>{item.scene_label}</strong>
        {version.status === 'published' && item.characters && <p>发布时演员：{item.characters.map(row => `${row.character_name} · ${row.display_name ?? '未绑定'}`).join('、') || '未登记出场人物'}</p>}
        <label>拍摄开始 {index + 1}<Input required type="datetime-local" step={1} value={local(item.starts_at)} onChange={event => edit(index, { starts_at: fromLocal(event.target.value) })} /></label>
        <label>拍摄结束 {index + 1}<Input required type="datetime-local" step={1} value={local(item.ends_at)} onChange={event => edit(index, { ends_at: fromLocal(event.target.value) })} /></label>
        <label>转场分钟 {index + 1}<Input required type="number" min={0} max={1440} value={item.transition_minutes} onChange={event => edit(index, { transition_minutes: Number(event.target.value) })} /></label>
        <p>拍摄场地：{item.location_name ?? '未选择'} · {item.location_reservation_id ? '已关联预约，待检查' : '未关联场地预约'}</p><div className="member-actions"><Button onClick={() => setPicker({ kind: 'resources', index, location: true })}>选择场地 {index + 1}</Button>{item.location_resource_id && <><Button onClick={() => setPicker({ kind: 'reservations', index, location: true })}>选择场地预约 {index + 1}</Button><Button onClick={() => edit(index, { location_resource_id: null, location_name: null, location_reservation_id: null })}>清除场地 {index + 1}</Button></>}</div>
        <h4>参与人员</h4><ul>{item.members.map(member => <li key={member.member_id}>{member.display_name} <Button onClick={() => edit(index, { members: item.members.filter(value => value.member_id !== member.member_id) })}>移除 {member.display_name}</Button></li>)}</ul><Button disabled={item.members.length >= 100} onClick={() => setPicker({ kind: 'members', index })}>添加参与人员 {index + 1}</Button>
        <h4>设备、道具与耗材</h4>{item.resources.map((resource, resourceIndex) => <div className="scene-need" key={resource.resource_id}><label>{resource.name} · 数量（{resource.unit}）<Input required type="number" min={1} max={1000000} value={resource.quantity} onChange={event => edit(index, { resources: item.resources.map((value, i) => i === resourceIndex ? { ...value, quantity: Number(event.target.value) } : value) })} /></label>
          <p>{resource.resource_type === 'consumable' ? '检查时核对库存，不自动扣减' : resource.reservation_id ? '已关联预约，待检查' : '未关联预约'}</p><div className="member-actions">{resource.resource_type !== 'consumable' && <><Button onClick={() => setPicker({ kind: 'reservations', index, resourceIndex })}>选择 {resource.name} 预约</Button>{resource.reservation_id && <Button onClick={() => edit(index, { resources: item.resources.map((value, i) => i === resourceIndex ? { ...value, reservation_id: null } : value) })}>解除 {resource.name} 预约</Button>}</>}<Button onClick={() => edit(index, { resources: item.resources.filter((_, i) => i !== resourceIndex) })}>移除 {resource.name}</Button></div></div>)}<Button disabled={item.resources.length >= 100} onClick={() => setPicker({ kind: 'resources', index })}>添加资源分配 {index + 1}</Button>
        <label>场次备注 {index + 1}<Input.TextArea maxLength={2000} value={item.note ?? ''} onChange={event => edit(index, { note: event.target.value })} /></label>
        <div className="member-actions"><Button onClick={() => setPicker({ kind: 'scenes', index })}>更换来源场戏 {index + 1}</Button><Button onClick={() => change(items.filter((_, i) => i !== index))}>删除拍摄场次 {index + 1}</Button>{index > 0 && <Button onClick={() => { const next = [...items]; [next[index - 1], next[index]] = [next[index], next[index - 1]]; change(next) }}>上移场次 {index + 1}</Button>}</div>
      </fieldset>)}
      {editable && <Button type="primary" htmlType="submit">保存排期</Button>}
    </fieldset></form>
    {report && <section aria-label="排期检查结果"><h4>检查依据：版本 {report.checked_lock_version}</h4><p>阻断 {report.blockers.length} 项 · 档期未知 {report.unknown_items.length} 段</p><ul>{report.blockers.map((issue, index) => <li key={index}>{issueText(issue)}</li>)}</ul><ul>{report.warnings.map((issue, index) => <li key={index}>提醒：{issueText(issue)}</li>)}</ul><ul>{report.unknown_items.map((issue, index) => <li key={index}>{canPublish ? <Checkbox checked={confirmed.includes(issue.item_id)} onChange={event => setConfirmed(event.target.checked ? [...confirmed, issue.item_id] : confirmed.filter(id => id !== issue.item_id))}>已核对并接受档期未知：{issueText(issue)}</Checkbox> : issueText(issue)}</li>)}</ul>{report.blockers.length === 0 && <p>提交时将再次核对依据；未知档期确认不会豁免已知冲突。</p>}{report.limitations.map(text => <p className="caption" key={text}>{text}</p>)}{canPublish && editable && <Button type="primary" disabled={!report.can_publish || !!draft || confirmed.length !== report.unknown_items.length} onClick={() => setPublishCommand({ key: crypto.randomUUID(), body: { expected_lock_version: report.checked_lock_version, expected_current_published_version_id: report.expected_current_published_version_id, checked_data_revision: report.checked_data_revision, confirmed_unknown_item_ids: [...confirmed] } })}>核对并发布排期</Button>}</section>}
    {picker && <PlanChoicePicker key={`${picker.kind}-${picker.index}-${chosenResource}`} crewId={crewId} userId={userId} kind={picker.kind} resourceId={chosenResource} disabledChoice={disabledChoice} onSelect={select} onClose={() => setPicker(null)} />}
    {command && <PlanAction crewId={crewId} command={command} onSaved={saved} onClose={() => setCommand(null)} />}
    {publishCommand && <PublishPlan crewId={crewId} versionId={version.id} command={publishCommand} onClose={() => setPublishCommand(null)} onPublished={() => { setPublishCommand(null); setJustPublished(true); setReport(null); void cache.invalidateQueries({ queryKey: ['crews', userId, crewId] }) }} />}
  </section>
}

function PublishPlan({ crewId, versionId, command, onClose, onPublished }: { crewId: string; versionId: string; command: { body: PlanPublishBody; key: string }; onClose: () => void; onPublished: () => void }) {
  const { plans } = useAuth()
  const [error, setError] = useState<unknown>(null), [pending, setPending] = useState(false), [uncertain, setUncertain] = useState(false)
  const lock = useRef(false)
  useBusinessSession(error)
  async function submit() {
    if (lock.current) return
    lock.current = true; setPending(true); setError(null)
    try { await plans.publish(crewId, versionId, command.body, command.key); onPublished() }
    catch (failure) { setError(failure); setUncertain(!(failure instanceof AccountError) || failure.status >= 500 || ['NETWORK_UNCONFIRMED', 'INVALID_RESPONSE', 'SERVICE_UNAVAILABLE'].includes(failure.code)) }
    finally { lock.current = false; setPending(false) }
  }
  return <Modal title="发布正式排期" open footer={null} onCancel={onClose} closable={!pending && !uncertain} keyboard={!pending && !uncertain} mask={{ closable: !pending && !uncertain }}><ErrorNotice error={error} /><p>将本草稿设为当前正式排期，替换原当前版本。已发布内容不能直接编辑；本次确认 {command.body.confirmed_unknown_item_ids.length} 项未知档期。</p><p className="caption">发布不自动创建预约或扣减库存。请主动通知剧组成员，当前版本暂不自动发送发布提醒。</p>{uncertain && <p role="alert">发布结果尚未确认，请使用原请求重试。</p>}<Button type="primary" loading={pending} aria-label={uncertain ? '用原发布请求重试' : '确认发布正式排期'} onClick={() => { void submit() }}>{uncertain ? '用原发布请求重试' : '确认发布正式排期'}</Button></Modal>
}

function PlanChoicePicker({ crewId, userId, kind, resourceId, disabledChoice, onSelect, onClose }: { crewId: string; userId: string; kind: PlanChoiceKind; resourceId?: string; disabledChoice: (choice: PlanChoice) => boolean; onSelect: (choice: PlanChoice) => void; onClose: () => void }) {
  const { plans } = useAuth()
  const [page, setPage] = useState(1)
  const list = useQuery({ queryKey: ['crews', userId, crewId, 'plan-choices', kind, resourceId, page], queryFn: ({ signal }) => plans.choices(crewId, kind, page, resourceId, signal), retry: false })
  useBusinessSession(list.error)
  return <Modal title="选择排期关联" open footer={null} onCancel={onClose}><ErrorNotice error={list.error} />{list.isPending && <p role="status">正在读取可选项目…</p>}{!list.isError && list.data && <><ul className="crew-list">{list.data.items.map(choice => <li key={choice.id}><Button disabled={disabledChoice(choice)} onClick={() => onSelect(choice)}>{choice.label}{choice.quantity !== null ? ` · 批准数量 ${choice.quantity}` : ''}</Button></li>)}</ul>{list.data.total === 0 && <p>暂无可选项目。场戏须先确认；资源预约须先获批准。</p>}<div className="list-pagination"><Button disabled={page === 1} onClick={() => setPage(page - 1)}>上一页关联</Button><span>共 {list.data.total} 项</span><Button disabled={page * 20 >= list.data.total} onClick={() => setPage(page + 1)}>下一页关联</Button></div></>}</Modal>
}

function PlanAction({ crewId, command, onSaved, onClose }: { crewId: string; command: PlanCommand; onSaved: (value: Plan | PlanVersion) => void; onClose: () => void }) {
  const { plans } = useAuth()
  const [error, setError] = useState<unknown>(null), [pending, setPending] = useState(false), [uncertain, setUncertain] = useState(false)
  const lock = useRef(false)
  useBusinessSession(error)
  async function submit() {
    if (lock.current) return
    lock.current = true; setPending(true); setError(null)
    try { onSaved(await plans.write(crewId, command)) }
    catch (failure) { setError(failure); setUncertain(!(failure instanceof AccountError) || failure.status >= 500 || ['NETWORK_UNCONFIRMED', 'INVALID_RESPONSE', 'SERVICE_UNAVAILABLE'].includes(failure.code)) }
    finally { lock.current = false; setPending(false) }
  }
  useEffect(() => { if (command.action === 'save') void submit() }, [command])
  return <Modal title={actionNames[command.action]} open footer={null} onCancel={onClose} closable={!pending && !uncertain} keyboard={!pending && !uncertain} mask={{ closable: !pending && !uncertain }}><ErrorNotice error={error} /><p>{command.action === 'save' && !error ? '正在保存排期。' : `确认${actionNames[command.action]}？`}{command.action === 'create' ? String(command.body.name) : command.action === 'save' ? `共 ${(command.body.sessions as unknown[]).length} 场。` : '创建独立草稿，不创建资源预约。'}</p>{uncertain && <p role="alert">结果尚未确认，请用原请求重试。</p>}{(command.action !== 'save' || error != null) && <Button type="primary" loading={pending} aria-label={uncertain ? '用原排期请求重试' : '确认排期操作'} onClick={() => { void submit() }}>{uncertain ? '用原排期请求重试' : '确认排期操作'}</Button>}</Modal>
}
