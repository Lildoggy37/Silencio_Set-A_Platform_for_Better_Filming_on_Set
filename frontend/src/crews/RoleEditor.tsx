import { useRef, useState } from 'react'
import { Button, Checkbox, Modal } from 'antd'
import { useAuth } from '../auth/AuthProvider'
import { ErrorNotice } from '../auth/AccountScreens'
import { AccountError } from '../api/accounts'
import { roleNames } from '../api/crews'
import type { Member } from '../api/crews'
import { useBusinessSession } from './useBusinessSession'

const showRoles = (codes: string[]) => codes.map(code => roleNames[code]).join(' / ') || '未分配岗位'

export function RoleEditor({ crewId, member, onClose, onReload, onSaved }: {
  crewId: string; member: Member; onClose: () => void
  onReload: () => Promise<Member>; onSaved: () => Promise<void>
}) {
  const { crews } = useAuth()
  const [current, setCurrent] = useState(member)
  const [selected, setSelected] = useState(member.role_codes)
  const [review, setReview] = useState(false)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [needsReload, setNeedsReload] = useState(false)
  const [notice, setNotice] = useState('')
  const lock = useRef(false)
  useBusinessSession(error)
  const same = [...selected].sort().join(',') === [...current.role_codes].sort().join(',')
  const adminChanged = current.role_codes.includes('admin') !== selected.includes('admin')

  async function submit() {
    if (lock.current || needsReload || same || !review) return
    lock.current = true; setPending(true); setError(null)
    try {
      await crews.replaceRoles(crewId, current.id, selected, current.lock_version)
      // The write is confirmed. Failure to reload should not invite a replay.
      onClose()
      try { await onSaved() } catch { /* Queries show their own refresh errors; the write was confirmed. */ }
    } catch (failure) {
      setError(failure)
      if (!(failure instanceof AccountError) || ['VERSION_CONFLICT', 'NETWORK_UNCONFIRMED', 'INVALID_RESPONSE', 'SERVICE_UNAVAILABLE'].includes(failure.code) || failure.status >= 500) {
        setNeedsReload(true); setReview(false)
      }
    } finally { lock.current = false; setPending(false) }
  }

  async function reload() {
    if (lock.current) return
    lock.current = true; setPending(true); setError(null)
    try {
      const fresh = await onReload()
      setCurrent(fresh); setNeedsReload(false); setReview(false)
      setNotice('已读取当前岗位，你的选择已保留。请重新核对差异后提交。')
    } catch (failure) { setError(failure) }
    finally { lock.current = false; setPending(false) }
  }

  return <Modal title={`管理岗位 · ${current.display_name}`} open footer={null} onCancel={onClose}
    closable={!pending} keyboard={!pending} mask={{ closable: !pending }} width={600}>
    <div className="role-editor">
      <p className="caption">可兼任多个岗位。岗位调整不会清除单独授予的权限。</p>
      {review ? <div className="role-review"><h3>核对岗位变更</h3><p>当前岗位：{showRoles(current.role_codes)}</p><p>拟调整为：{showRoles(selected)}</p>
        {adminChanged && <p className="account-note">{selected.includes('admin') ? '将授予邀请成员、分配岗位等管理权限。' : '将取消管理权限；剧组必须保留至少一名有效管理员。若修改自己，你可能失去岗位管理入口。'}</p>}
      </div> : <fieldset disabled={pending || needsReload} className="role-options"><legend>选择岗位</legend>{Object.entries(roleNames).map(([code, name]) =>
        <Checkbox key={code} checked={selected.includes(code)} onChange={event => setSelected(previous => event.target.checked ? [...previous, code] : previous.filter(item => item !== code))}>{name}</Checkbox>,
      )}</fieldset>}
      <ErrorNotice error={error} />
      {notice && <p role="status" className="caption">{notice} 当前岗位：{showRoles(current.role_codes)}</p>}
      {needsReload && <div className="account-note"><p>未确认修改结果，或成员资料已变化。请先读取当前岗位，输入不会被自动覆盖。</p><Button onClick={() => { void reload() }} loading={pending}>读取当前岗位</Button></div>}
      <div className="role-actions">{review ? <><Button disabled={pending} onClick={() => setReview(false)}>返回修改</Button><Button type="primary" aria-label="确认修改岗位" aria-busy={pending} loading={pending} onClick={() => { void submit() }} disabled={needsReload || same}>确认修改岗位</Button></>
        : <Button type="primary" aria-label="核对岗位变更" onClick={() => { setReview(true); setNotice('') }} disabled={pending || needsReload || same}>核对岗位变更</Button>}</div>
    </div>
  </Modal>
}
