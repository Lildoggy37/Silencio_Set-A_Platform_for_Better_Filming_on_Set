import { Modal } from 'antd'
import { useQuery } from '@tanstack/react-query'
import { useAuth } from '../auth/AuthProvider'
import { ErrorNotice } from '../auth/AccountScreens'
import { useBusinessSession } from '../crews/useBusinessSession'
import { CallSheetPreview } from '../plans/CallSheetBoard'
import type { Notice } from '../api/notifications'

const display = (value: string) => new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', dateStyle: 'short', timeStyle: 'short' }).format(new Date(value))

export function PublicationNoticePreview({ crewId, userId, notice, onClose }: { crewId: string; userId: string; notice: Notice; onClose: () => void }) {
  const { plans, callSheets } = useAuth()
  const plan = useQuery({ queryKey: ['crews', userId, crewId, 'plan-version', notice.source_id], queryFn: () => plans.get(crewId, notice.source_id), enabled: notice.source_type === 'plan', retry: false })
  const sheet = useQuery({ queryKey: ['crews', userId, crewId, 'call-sheet-version', notice.source_id], queryFn: () => callSheets.get(crewId, notice.source_id), enabled: notice.source_type === 'call_sheet', retry: false })
  const error = notice.source_type === 'plan' ? plan.error : sheet.error
  const pending = notice.source_type === 'plan' ? plan.isPending : sheet.isPending
  useBusinessSession(error)
  return <Modal title={notice.object_title} open footer={null} onCancel={onClose} width={960}>
    <ErrorNotice error={error} />{pending && <p role="status">正在读取通知对应安排…</p>}
    {!error && notice.source_type === 'plan' && plan.data && <><p>正式排期第 {plan.data.version_no} 版 · 北京时间</p>{plan.data.review_reasons.length > 0 && <p role="alert">此排期来源已有变化，请联系制片复核。</p>}
      <div className="directory-scroll"><table className="availability-table"><caption>通知对应拍摄安排</caption><thead><tr><th>场次</th><th>起止时间</th><th>场地</th><th>人员</th></tr></thead><tbody>{plan.data.sessions.map((item, index) => <tr key={item.id ?? index}><td>{item.scene_label}</td><td>{display(item.starts_at)}<br />— {display(item.ends_at)}</td><td>{item.location_name}</td><td>{item.members.map(person => person.display_name).join('、')}</td></tr>)}</tbody></table></div></>}
    {!error && notice.source_type === 'call_sheet' && sheet.data && <>{sheet.data.validity_status !== 'valid' || !sheet.data.source_current ? <p role="alert">此通告已非当前有效安排，请核对最新通告。</p> : null}<CallSheetPreview content={sheet.data} /></>}
    <p className="caption">这里只查看通知对应内容。返回通知后可单独标记已读或确认知悉。</p>
  </Modal>
}
