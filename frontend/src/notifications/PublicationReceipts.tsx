import { useState } from 'react'
import { Button, Modal } from 'antd'
import { useQuery } from '@tanstack/react-query'
import { useAuth } from '../auth/AuthProvider'
import { ErrorNotice } from '../auth/AccountScreens'
import { useBusinessSession } from '../crews/useBusinessSession'
import type { ReceiptKind } from '../api/receiptManagement'

const statuses = { current: '当前有效', updated: '已被新版替代', review_required: '待复核', superseded: '已被新版替代', withdrawn: '已撤回' }
const identities = { active: '当前在组', left: '已离组', rejoined: '入组身份已变化', disabled: '账号不可用' }
const dispatches = { pending_dispatch: '待处理', generated: '已生成站内通知', skipped: '未生成通知' }
const display = (value: string | null) => value ? new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', dateStyle: 'short', timeStyle: 'short' }).format(new Date(value)) : '—'

export function canReadReceipts(kind: ReceiptKind, permissions: string[]) {
  const full = kind === 'plan' ? ['plan.read_all', 'plan.edit'] : ['call_sheet.edit', 'call_sheet.review', 'call_sheet.publish']
  return permissions.includes('member.read') && full.some(code => permissions.includes(code)) && [kind === 'plan' ? 'plan.publish' : 'call_sheet.publish', 'notification.receipts'].some(code => permissions.includes(code))
}

export function PublicationReceipts(props: { crewId: string; userId: string; kind: ReceiptKind; versionId: string; permissions: string[] }) {
  const [open, setOpen] = useState(false)
  if (!canReadReceipts(props.kind, props.permissions)) return null
  return <><Button onClick={() => setOpen(true)}>查看发布通知回执</Button>{open && <ReceiptList {...props} onClose={() => setOpen(false)} />}</>
}

function ReceiptList({ crewId, userId, kind, versionId, onClose }: { crewId: string; userId: string; kind: ReceiptKind; versionId: string; onClose: () => void }) {
  const { notifications } = useAuth()
  const [page, setPage] = useState(1)
  const list = useQuery({ queryKey: ['crews', userId, crewId, 'publication-receipts', kind, versionId, page], queryFn: ({ signal }) => notifications.receipts(crewId, kind, versionId, page, signal), retry: false })
  useBusinessSession(list.error)
  const data = list.isError ? undefined : list.data
  return <Modal title="发布通知回执" open footer={null} width={1000} onCancel={onClose}>
    <p className="caption">按原发布接收集合展示。通知生成不代表用户已收到或已阅读，知悉不代表到场；此处只能查看。</p>
    <Button aria-label="刷新发布回执" loading={list.isFetching} onClick={() => { void list.refetch() }}>刷新发布回执</Button>
    <ErrorNotice error={list.error} />{list.isPending && <p role="status">正在读取发布回执…</p>}
    {data && <><p>第 {data.version_no} 版 · {statuses[data.source_status]} · 截至 {display(data.observed_at)}（北京时间）</p>
      {data.source_status !== 'current' && <p role="alert">以下为历史发布回执，不代表成员已知悉当前有效安排。</p>}
      <p>接收集合 {data.counts.intended} 人 · 通知已生成 {data.counts.generated} · 待处理 {data.counts.pending_dispatch} · 未生成 {data.counts.skipped}</p>
      <p>已读 {data.counts.read} · 已知悉 {data.counts.confirmed} · 已生成通知中需知悉但未确认 {data.counts.unconfirmed}</p>
      {data.dispatch_status === 'pending_dispatch' && <p>发布事件尚待通知消费者处理，请稍后手动刷新。</p>}
      <div className="directory-scroll"><table className="availability-table"><caption>发布时接收人及原回执</caption><thead><tr><th>成员</th><th>通知</th><th>已读时间</th><th>知悉时间</th></tr></thead><tbody>{data.items.map(item => <tr key={`${item.member_id}-${item.membership_joined_at}`}><td><span>{item.display_name}</span><br /><span>{identities[item.identity_status]}</span></td><td>{dispatches[item.notification_status]}<br />{item.requires_confirmation ? '需本人确认' : '仅提醒'}</td><td>{display(item.read_at)}</td><td>{item.requires_confirmation ? display(item.confirmed_at) : '无需确认'}</td></tr>)}</tbody></table></div>
      {data.items.length === 0 && <p>当前页没有发布接收人。</p>}
      <div className="list-pagination"><Button disabled={page === 1} onClick={() => setPage(page - 1)}>上一页回执</Button><span>第 {page} 页 · 共 {data.total} 人</span><Button disabled={page * 20 >= data.total} onClick={() => setPage(page + 1)}>下一页回执</Button></div></>}
  </Modal>
}
