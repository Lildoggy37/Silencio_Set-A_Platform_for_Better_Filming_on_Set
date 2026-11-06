import { useRef, useState } from 'react'
import { Button, Modal } from 'antd'
import { useAuth } from '../auth/AuthProvider'
import { ErrorNotice } from '../auth/AccountScreens'
import { useBusinessSession } from '../crews/useBusinessSession'
import { AccountError } from '../api/accounts'
import type { Script, ScriptDetail } from '../api/scripts'

export function ScriptFileImport({ crewId, script, onClose, onSaved }: { crewId: string; script: Script; onClose: () => void; onSaved: (value: ScriptDetail) => void }) {
  const { scripts } = useAuth()
  const [file, setFile] = useState<File | null>(null), [version, setVersion] = useState(script.lock_version)
  const [command, setCommand] = useState<{ file: File; version: number; key: string } | null>(null)
  const [pending, setPending] = useState(false), [uncertain, setUncertain] = useState(false), [needsReload, setNeedsReload] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const lock = useRef(false)
  useBusinessSession(error)
  async function submit() {
    if (lock.current) return
    lock.current = true; setPending(true); setError(null)
    try { onSaved(await scripts.importFile(crewId, script.id, command!.file, command!.version, command!.key)) }
    catch (failure) {
      setError(failure)
      const unknown = !(failure instanceof AccountError) || failure.status >= 500 || ['NETWORK_UNCONFIRMED', 'INVALID_RESPONSE', 'SERVICE_UNAVAILABLE'].includes(failure.code)
      setUncertain(unknown)
      if (!unknown) { setCommand(null); setNeedsReload(failure.code === 'VERSION_CONFLICT') }
    } finally { lock.current = false; setPending(false) }
  }
  async function reload() {
    setPending(true); setError(null)
    try { setVersion((await scripts.get(crewId, script.id)).script.lock_version); setNeedsReload(false) }
    catch (failure) { setError(failure) }
    finally { setPending(false) }
  }
  return <Modal title="导入剧本文件" open footer={null} width={760} onCancel={onClose} closable={!pending && !uncertain} keyboard={!pending && !uncertain} mask={{ closable: !pending && !uncertain }}><div className="role-editor">
    <p>{script.title} · 导入后生成待核对的新版本，不自动替换工作稿。</p><ErrorNotice error={error} />
    {needsReload && <Button loading={pending} onClick={() => { void reload() }}>读取最新剧本并保留文件</Button>}
    {uncertain && <p role="alert">导入结果尚未确认，请使用同一个文件和原请求重试。</p>}
    {command ? <><p>{command.file.name} · {(command.file.size / 1024).toFixed(1)} KiB</p><p>请核对文件。导入可能需要数十秒，正文仍需人工核对和确认。</p><div className="member-actions"><Button disabled={pending || uncertain} onClick={() => setCommand(null)}>重新选择文件</Button><Button type="primary" loading={pending} aria-label={uncertain ? '用原文件导入请求重试' : '确认导入剧本'} onClick={() => { void submit() }}>{uncertain ? '用原文件导入请求重试' : '确认导入剧本'}</Button></div></>
      : <form onSubmit={event => { event.preventDefault(); setError(null); setCommand({ file: file!, version, key: crypto.randomUUID() }) }}><fieldset className="custody-fields" disabled={pending || needsReload}>
        <label>剧本文件<input type="file" accept=".docx,.pdf,.txt" required={!file} onChange={event => { const next = event.target.files?.[0] ?? null; setFile(next); setError(next && next.size > 20 * 1024 * 1024 ? new AccountError('DOCUMENT_TOO_LARGE') : null) }} /></label>
        {file && <p>已选：{file.name}</p>}<p className="caption">支持DOCX、文字型PDF和UTF-8 TXT；最多20MiB，PDF最多100页，正文最多100000字符。扫描件需先转为文字。文件仅供当前有权查看剧本的成员访问。</p>
        <Button type="primary" htmlType="submit" disabled={!file || file.size > 20 * 1024 * 1024}>核对导入文件</Button>
      </fieldset></form>}
  </div></Modal>
}
