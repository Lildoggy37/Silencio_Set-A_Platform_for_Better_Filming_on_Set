import { useState } from 'react'
import { Button } from 'antd'
import { useQuery } from '@tanstack/react-query'
import { useAuth } from '../auth/AuthProvider'
import { ErrorNotice } from '../auth/AccountScreens'
import { useBusinessSession } from '../crews/useBusinessSession'
import type { CreativeKind } from '../api/creative'
import type { SourceAssessment } from '../api/sourceReviews'
import { CreativePanel } from './CreativePanel'
import { sourceStatuses } from './SourceReviewPanel'

export function SourceImpacts({ crewId, userId, scriptId, permissions }: { crewId: string; userId: string; scriptId: string; permissions: string[] }) {
  const { creative } = useAuth()
  const [kind, setKind] = useState<CreativeKind>(permissions.includes('scene.read') ? 'breakdowns' : 'storyboards'), [page, setPage] = useState(1)
  const [selected, setSelected] = useState<SourceAssessment | null>(null)
  const list = useQuery({ queryKey: ['crews', userId, crewId, 'source-impacts', scriptId, kind, page], queryFn: ({ signal }) => creative.sourceImpacts(crewId, scriptId, kind, page, signal), retry: false })
  useBusinessSession(list.error)
  return <section className="creative-panel"><h3>剧本变更影响</h3><p className="caption">列出固定来源不同于当前工作稿的资料，包括已复核仍适用项。正式排期与通告的复核提示仍在排期页处理。</p>
    <div className="member-actions">{permissions.includes('scene.read') && <Button onClick={() => { setKind('breakdowns'); setPage(1); setSelected(null) }}>场戏来源影响</Button>}{permissions.includes('storyboard.read') && <Button onClick={() => { setKind('storyboards'); setPage(1); setSelected(null) }}>分镜来源影响</Button>}<Button aria-label="刷新变更影响" loading={list.isFetching} onClick={() => { void list.refetch() }}>刷新变更影响</Button></div>
    <ErrorNotice error={list.error} />{list.isPending && <p role="status">正在读取变更影响…</p>}
    {!list.isError && list.data && <><ul className="crew-list">{list.data.items.map(item => <li key={item.id}><div><strong>{kind === 'breakdowns' ? '场戏拆解' : '文字分镜'}第 {item.version_no} 版 · 来源剧本第 {item.source_script_version_no} 版</strong><p>{sourceStatuses[item.status]}</p></div><Button onClick={() => setSelected(item)}>查看受影响资料</Button></li>)}</ul>{list.data.total === 0 && <p>没有已录入且来源不同于工作稿的此类资料；这不代表所有筹备事项已就绪。</p>}<div className="list-pagination"><Button disabled={page === 1} onClick={() => setPage(page-1)}>上一页变更影响</Button><span>共 {list.data.total} 份资料</span><Button disabled={page*20 >= list.data.total} onClick={() => setPage(page+1)}>下一页变更影响</Button></div></>}
    {!list.isError && selected && <CreativePanel key={`${selected.kind}-${selected.id}`} crewId={crewId} userId={userId} sourceId={selected.source_id} kind={selected.kind} initialId={selected.id} permissions={permissions} />}
  </section>
}
