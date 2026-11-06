import { useState } from 'react'
import { Button, Modal } from 'antd'
import { useQuery } from '@tanstack/react-query'
import { useAuth } from '../auth/AuthProvider'
import { ErrorNotice } from '../auth/AccountScreens'
import { changeCategories } from '../api/changes'
import type { ChangeCategory, ChangeIssue, PlanTarget } from '../api/changes'
import { useBusinessSession } from '../crews/useBusinessSession'
import { CreativePanel } from '../scripts/CreativePanel'
import { CallSheetPreview } from '../plans/CallSheetBoard'
import { categoryNames as titles, changeReasonNames as reasons } from './labels'
import { ImpactHistory } from './ImpactHistory'

type Props = { selfMemberId?: string; crewId: string; userId: string; permissions: string[]; onOpenPlan: (target: PlanTarget) => void; onOpenScript: (scriptId: string) => void }

export function ChangeWorkbench(props: Props) {
  const [open, setOpen] = useState(false)
  const allowed = changeCategories(props.permissions)
  if (!allowed.length) return null
  return <><div className="review-entry"><div><h2>需要重新确认的安排</h2><p>剧本、人员档期或资源有变化时，在这里核对受影响的场戏、分镜、排期和通告。</p></div><Button onClick={() => setOpen(true)}>查看待确认事项</Button></div>{open && <WorkbenchDialog {...props} allowed={allowed} onClose={() => setOpen(false)} />}</>
}

function WorkbenchDialog({ crewId, userId, selfMemberId, permissions, allowed, onOpenPlan, onOpenScript, onClose }: Props & { allowed: ChangeCategory[]; onClose: () => void }) {
  const { changes } = useAuth()
  const [category, setCategory] = useState<ChangeCategory>(allowed.includes('plans') ? 'plans' : allowed[0]), [page, setPage] = useState(1)
  const [selected, setSelected] = useState<ChangeIssue | null>(null)
  const [recordingLocked, setRecordingLocked] = useState(false)
  const list = useQuery({ queryKey: ['crews', userId, crewId, 'change-issues', category, page], queryFn: ({ signal }) => changes.list(crewId, category, page, signal), retry: false })
  useBusinessSession(list.error)
  const canOpenPlan = permissions.some(code => ['plan.read_all', 'plan.edit'].includes(code))
  function scriptAction(item: ChangeIssue) {
    if (item.status === 'no_working_version') return permissions.includes('script.confirm') ? '去设置工作稿' : '查看剧本资料'
    return permissions.includes(item.category === 'breakdowns' ? 'scene.edit' : 'storyboard.edit') ? '到工作稿修订' : '查看工作稿'
  }
  function open(item: ChangeIssue) {
    if (item.category === 'plans') { onOpenPlan({ planId: item.parent_id, versionId: item.id }); onClose() }
    else if (item.category === 'call_sheets' && canOpenPlan) {
      onOpenPlan({ planId: item.parent_id, versionId: item.current_plan_version_id ?? item.source_id!, sheetId: item.container_id!, sheetVersionId: item.id }); onClose()
    } else setSelected(item)
  }
  return <Modal title="需要重新确认的安排" open width={1200} footer={null} closable={!recordingLocked} keyboard={!recordingLocked} mask={{ closable: !recordingLocked }} onCancel={onClose}>
    <p className="caption">资料更新后，原来的场戏、分镜或拍摄安排可能需要调整。选择类别，查看变化原因，再前往对应内容确认或修订。</p>
    <div className="member-actions">{allowed.map(kind => <Button key={kind} disabled={recordingLocked} aria-pressed={category === kind} onClick={() => { setCategory(kind); setPage(1); setSelected(null) }}>{titles[kind]}</Button>)}<Button aria-label="刷新变更清单" loading={list.isFetching} onClick={() => { void list.refetch() }}>刷新变更清单</Button></div>
    <ErrorNotice error={list.error} />{list.isPending && <p role="status">正在读取变更待处理事项…</p>}
    {!list.isError && list.data && <><p>{titles[category]} · 共 {list.data.total} 项 · 读取时间 {new Date(list.data.observed_at).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' })}（北京时间）</p>
      {category === 'breakdowns' || category === 'storyboards' ? <p className="caption">包含未复核的历史资料和旧来源草稿，不表示它们都必须用于当前拍摄；已确认仍适用项会移出本清单。</p> : null}
      <ul className="crew-list">{list.data.items.map(item => <li key={item.id}><div><strong>{item.title}</strong><p>{item.reason_codes.map(code => reasons[code]).join('、')}</p></div><div className="member-actions"><Button disabled={recordingLocked} onClick={() => open(item)}>{item.category === 'plans' || item.category === 'call_sheets' && canOpenPlan ? '前往排期处理' : '查看并处理'}</Button>{(item.category === 'breakdowns' || item.category === 'storyboards') && ['needs_revision', 'no_working_version'].includes(item.status) && <Button disabled={recordingLocked} onClick={() => { onOpenScript(item.parent_id); onClose() }}>{scriptAction(item)}</Button>}</div></li>)}</ul>
      {list.data.items.length === 0 && <p>当前类别暂无需要重新确认的内容。</p>}
      <div className="list-pagination"><Button disabled={page === 1 || recordingLocked} onClick={() => { setPage(page-1); setSelected(null) }}>上一页变更事项</Button><span>第 {page} 页</span><Button disabled={page*20 >= list.data.total || recordingLocked} onClick={() => { setPage(page+1); setSelected(null) }}>下一页变更事项</Button></div></>}
    <ImpactHistory key={category} crewId={crewId} userId={userId} category={category} onLock={setRecordingLocked} />
    {!list.isError && selected && (selected.category === 'breakdowns' || selected.category === 'storyboards') && <CreativePanel selfMemberId={selfMemberId} key={`${selected.category}-${selected.id}`} crewId={crewId} userId={userId} kind={selected.category} sourceId={selected.source_id!} initialId={selected.id} permissions={permissions} />}
    {!list.isError && selected?.category === 'call_sheets' && <AffectedCallSheet key={selected.id} crewId={crewId} userId={userId} versionId={selected.id} />}
  </Modal>
}

function AffectedCallSheet({ crewId, userId, versionId }: { crewId: string; userId: string; versionId: string }) {
  const { callSheets } = useAuth()
  const detail = useQuery({ queryKey: ['crews', userId, crewId, 'call-sheet-version', versionId], queryFn: () => callSheets.get(crewId, versionId), retry: false })
  useBusinessSession(detail.error)
  return <section><h3>受影响通告</h3><ErrorNotice error={detail.error} />{detail.isPending && <p role="status">正在读取通告…</p>}{!detail.isError && detail.data && <><p>请联系制片按当前正式排期更新并重新发布通告。当前权限可查看通告，未获得完整排期读取权限。</p><CallSheetPreview content={detail.data} /></>}</section>
}
