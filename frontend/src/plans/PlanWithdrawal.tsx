import { useRef, useState } from 'react'
import { Button, Input, Modal } from 'antd'
import { useQueryClient } from '@tanstack/react-query'
import { useAuth } from '../auth/AuthProvider'
import { ErrorNotice } from '../auth/AccountScreens'
import { AccountError } from '../api/accounts'
import { useBusinessSession } from '../crews/useBusinessSession'
import type { PlanVersion, PlanWithdrawBody } from '../api/plans'

export function PlanWithdrawal({ crewId, userId, version }: { crewId: string; userId: string; version: PlanVersion }) {
  const { plans } = useAuth()
  const cache = useQueryClient()
  const [open, setOpen] = useState(false), [reason, setReason] = useState('')
  const [command, setCommand] = useState<{ body: PlanWithdrawBody; key: string } | null>(null)
  const [error, setError] = useState<unknown>(null), [busy, setBusy] = useState(false), [uncertain, setUncertain] = useState(false)
  const lock = useRef(false)
  useBusinessSession(error)
  async function withdraw() {
    if (lock.current) return
    lock.current = true; setBusy(true); setError(null)
    try {
      await plans.withdraw(crewId, version.id, command!.body, command!.key)
      setOpen(false); setCommand(null); setUncertain(false); setReason('')
      void cache.invalidateQueries({ queryKey: ['crews', userId, crewId] })
    } catch (failure) {
      setError(failure)
      const unknown = !(failure instanceof AccountError) || ['NETWORK_UNCONFIRMED', 'INVALID_RESPONSE', 'SERVICE_UNAVAILABLE'].includes(failure.code) || failure.status >= 500
      setUncertain(unknown)
      if (!unknown) { setCommand(null); setOpen(false); void cache.invalidateQueries({ queryKey: ['crews', userId, crewId] }) }
    } finally { lock.current = false; setBusy(false) }
  }
  return <><Button onClick={() => { setOpen(true); setError(null) }}>撤回当前正式排期</Button><ErrorNotice error={!open ? error : null} />
    <Modal title="撤回当前正式排期" open={open} footer={null} closable={!busy && !uncertain} keyboard={!busy && !uncertain} mask={{ closable: !busy && !uncertain }} onCancel={() => { setOpen(false); setCommand(null) }}>
      <p>撤回第 {version.version_no} 版后，该计划暂时没有正式安排，关联通告将撤回，并生成取消通知。原版本和回执保留；预约与库存需另行处理。</p><ErrorNotice error={error} />
      {!command ? <form className="role-editor" onSubmit={event => { event.preventDefault(); setCommand({ body: { expected_lock_version: version.lock_version, reason: reason.trim() }, key: crypto.randomUUID() }) }}><label>撤回原因<Input.TextArea required maxLength={500} value={reason} onChange={event => setReason(event.target.value)} /></label><Button htmlType="submit" disabled={!reason.trim()}>核对撤回影响</Button></form>
        : <><p>撤回原因：{command.body.reason}</p>{uncertain && <p role="alert">结果尚未确认，请用原撤回请求重试。</p>}<Button type="primary" aria-label={uncertain ? '用原撤回请求重试' : '确认撤回排期'} loading={busy} onClick={() => { void withdraw() }}>{uncertain ? '用原撤回请求重试' : '确认撤回排期'}</Button></>}
    </Modal></>
}
