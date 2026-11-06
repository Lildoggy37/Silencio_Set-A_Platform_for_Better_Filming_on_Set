import { useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { Button, Input, Modal } from 'antd'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useAuth } from '../auth/AuthProvider'
import { ErrorNotice } from '../auth/AccountScreens'
import { AccountError } from '../api/accounts'
import type { ScriptCommand, ScriptDetail } from '../api/scripts'
import { useBusinessSession } from '../crews/useBusinessSession'
import { CreativePanel } from './CreativePanel'
import { ScriptFileImport } from './ScriptFileImport'
import { CastingPanel } from './CastingPanel'
import { SourceImpacts } from './SourceImpacts'

const titles = { create: '创建剧本', add_version: '录入新版本', correct: '修正正文', confirm: '确认正文', set_working: '设为工作版本' }
type Action = keyof typeof titles

export function ScriptLibrary({ crewId, userId, permissions, initialScriptId, selfMemberId }: { selfMemberId?: string; crewId: string; userId: string; permissions: string[]; initialScriptId?: string }) {
  const { scripts } = useAuth()
  const cache = useQueryClient()
  const [page, setPage] = useState(1)
  const [versionPage, setVersionPage] = useState(1)
  const [id, setId] = useState<string | null>(initialScriptId ?? null)
  const [versionId, setVersionId] = useState<string | undefined>()
  const [action, setAction] = useState<Action | null>(null)
  const [importing, setImporting] = useState(false)
  const [casting, setCasting] = useState(false)
  const [impacts, setImpacts] = useState(false)
  const [castingLocked, setCastingLocked] = useState(false)
  const list = useQuery({ queryKey: ['crews', userId, crewId, 'scripts', page], queryFn: ({ signal }) => scripts.list(crewId, page, signal), retry: false })
  const detail = useQuery({ queryKey: ['crews', userId, crewId, 'script', id, versionId], queryFn: () => scripts.get(crewId, id!, versionId), enabled: !!id, retry: false })
  const versions = useQuery({ queryKey: ['crews', userId, crewId, 'script-versions', id, versionPage], queryFn: ({ signal }) => scripts.versions(crewId, id!, versionPage, signal), enabled: !!id, retry: false })
  useBusinessSession(list.error ?? detail.error ?? versions.error)
  const current = detail.isError ? undefined : detail.data
  const canEdit = permissions.includes('script.edit'), canConfirm = permissions.includes('script.confirm')
  function saved(result: ScriptDetail) {
    setAction(null); setImporting(false); setId(result.script.id); setVersionId(result.version?.id)
    void cache.invalidateQueries({ queryKey: ['crews', userId, crewId] })
  }
  return <div className="role-editor"><div className="section-heading"><h2>剧本资料</h2><div className="member-actions"><Button onClick={() => { void list.refetch(); if (id) { void detail.refetch(); void versions.refetch() } }}>刷新剧本</Button>{canEdit && <Button disabled={castingLocked} type="primary" onClick={() => setAction('create')}>创建剧本</Button>}</div></div>
    <p className="caption">支持正文录入、DOCX／文字型PDF／UTF-8 TXT导入及已确认正文的场戏拆解。</p><ErrorNotice error={list.error} />{list.isPending && <p role="status">正在读取剧本…</p>}
    {!list.isError && list.data && <>{list.data.items.length === 0 ? <p>尚未创建剧本。</p> : <ul className="crew-list">{list.data.items.map(item => <li key={item.id}><Button disabled={castingLocked} onClick={() => { setId(item.id); setVersionId(undefined); setVersionPage(1) }}>{item.title}</Button><span>{item.current_version_id ? '已有工作版本' : '尚无工作版本'}</span></li>)}</ul>}
      <div className="list-pagination"><Button disabled={page === 1 || list.isFetching} onClick={() => setPage(page - 1)}>上一页剧本</Button><span>第 {page} 页 · 共 {list.data.total} 部</span><Button disabled={page * 20 >= list.data.total || list.isFetching} onClick={() => setPage(page + 1)}>下一页剧本</Button></div></>}
    {id && <><ErrorNotice error={detail.error ?? versions.error} />{detail.isPending && <p role="status">正在读取版本正文…</p>}{current && <><h3>{current.script.title}</h3><div className="member-actions">{canEdit && <><Button disabled={castingLocked} onClick={() => setAction('add_version')}>录入新版本</Button><Button disabled={castingLocked} onClick={() => setImporting(true)}>导入文件新版本</Button></>}<Button disabled={castingLocked} onClick={() => setVersionId(undefined)}>查看当前工作稿</Button></div>
      {!versions.isError && versions.data && <><div className="member-actions">{versions.data.items.map(item => <Button disabled={castingLocked} key={item.id} onClick={() => setVersionId(item.id)}>版本 {item.version_no} · {item.status === 'confirmed' ? '已确认' : '待核对'}{current.script.current_version_id === item.id ? ' · 工作稿' : ''}</Button>)}</div><div className="list-pagination"><Button disabled={versionPage === 1} onClick={() => setVersionPage(versionPage - 1)}>上一页版本</Button><span>第 {versionPage} 页 · 共 {versions.data.total} 版</span><Button disabled={versionPage * 20 >= versions.data.total} onClick={() => setVersionPage(versionPage + 1)}>下一页版本</Button></div></>}
      {current.version ? <><h4>版本 {current.version.version_no} · {current.version.status === 'confirmed' ? '已确认' : '待核对'}</h4><div className="member-actions">{current.version.status === 'pending_check' && <>{canEdit && <Button disabled={castingLocked} onClick={() => setAction('correct')}>修正正文</Button>}{canConfirm && <Button disabled={castingLocked} onClick={() => setAction('confirm')}>确认正文</Button>}</>}{current.version.status === 'confirmed' && canConfirm && current.script.current_version_id !== current.version.id && <Button disabled={castingLocked} onClick={() => setAction('set_working')}>设为工作版本</Button>}</div>
        {current.version.source_file && <div className="account-note"><p>来源：{current.version.source_file.original_name} <a href={`/api/v1/crews/${crewId}/scripts/${id}/versions/${current.version.id}/source-file`} target="_blank" rel="noopener noreferrer">下载原始文件</a></p>{current.version.source_file.extraction_notes.map((note, index) => <p key={index}>{note}</p>)}</div>}
        <ol className="script-paragraphs">{current.version.paragraphs.map(item => <li key={item.id} value={item.paragraph_no}>{item.text}{item.source_page !== null && <span className="caption"> · 原文件第 {item.source_page} 页</span>}</li>)}</ol><p className="caption">{current.version.status === 'confirmed' ? '已确认正文保留原样；后续修改请录入新版本。' : '请核对正文后确认。确认正文与切换工作版本是两个独立操作。'}</p>
        {current.version.status === 'confirmed' && permissions.includes('scene.read') && <CreativePanel selfMemberId={selfMemberId} key={current.version.id} crewId={crewId} userId={userId} sourceId={current.version.id} permissions={permissions} />}
      </> : <p>尚无工作版本。请先录入、核对并确认正文，再设为工作版本。</p>}</>}</>}
    {action && <ScriptForm key={`${action}-${id}-${current?.version?.id}`} action={action} crewId={crewId} current={action === 'create' ? undefined : current} onClose={() => setAction(null)} onSaved={saved} />}
    {importing && current && <ScriptFileImport crewId={crewId} script={current.script} onClose={() => setImporting(false)} onSaved={saved} />}
    {current && permissions.some(code => ['scene.read', 'storyboard.read'].includes(code)) && <><Button disabled={castingLocked} onClick={() => setImpacts(true)}>查看剧本变更影响</Button>{impacts && <SourceImpacts key={current.script.id} crewId={crewId} userId={userId} scriptId={current.script.id} permissions={permissions} />}</>}
    {current && permissions.includes('scene.read') && permissions.includes('member.read') && <><Button onClick={() => setCasting(true)}>查看人物与演员</Button>{casting && <CastingPanel key={current.script.id} crewId={crewId} userId={userId} scriptId={current.script.id} permissions={permissions} onRequestLock={setCastingLocked} />}</>}
  </div>
}

function ScriptForm({ action, crewId, current, onClose, onSaved }: { action: Action; crewId: string; current?: ScriptDetail; onClose: () => void; onSaved: (result: ScriptDetail) => void }) {
  const { scripts } = useAuth()
  const [base, setBase] = useState(current)
  const [title, setTitle] = useState('')
  const [text, setText] = useState(action === 'correct' ? current!.version!.paragraphs.map(item => item.text).join('\n') : '')
  const [command, setCommand] = useState<ScriptCommand | null>(null)
  const [pending, setPending] = useState(false)
  const [uncertain, setUncertain] = useState(false)
  const [needsReload, setNeedsReload] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const lock = useRef(false)
  useBusinessSession(error)
  function review(event: FormEvent) {
    event.preventDefault()
    if (Array.from(text).length > 100000) { setError(new AccountError('VALIDATION_ERROR')); return }
    const body = action === 'create' ? { title: title.trim() } : action === 'set_working' ? { expected_lock_version: base!.script.lock_version, version_id: base!.version!.id }
      : { expected_lock_version: action === 'add_version' ? base!.script.lock_version : base!.version!.lock_version, ...(action === 'confirm' ? {} : { text }) }
    setCommand({ action, scriptId: base?.script.id, versionId: base?.version?.id, body, key: crypto.randomUUID() }); setError(null)
  }
  async function submit() {
    if (lock.current) return
    lock.current = true; setPending(true); setError(null)
    try { onSaved(await scripts.execute(crewId, command!)) }
    catch (failure) {
      setError(failure)
      const unknown = !(failure instanceof AccountError) || ['NETWORK_UNCONFIRMED', 'INVALID_RESPONSE', 'SERVICE_UNAVAILABLE'].includes(failure.code) || failure.status >= 500
      setUncertain(unknown)
      if (!unknown) { setCommand(null); setNeedsReload(true) }
    } finally { lock.current = false; setPending(false) }
  }
  async function reload() {
    setPending(true); setError(null)
    try { if (base) setBase(await scripts.get(crewId, base.script.id, action === 'add_version' ? undefined : base.version!.id)); setNeedsReload(false) }
    catch (failure) { setError(failure) }
    finally { setPending(false) }
  }
  return <Modal title={titles[action]} open width={760} footer={null} onCancel={onClose} closable={!pending && !uncertain} keyboard={!pending && !uncertain} mask={{ closable: !pending && !uncertain }}><div className="role-editor"><ErrorNotice error={error} />
    {needsReload && <Button loading={pending} onClick={() => { void reload() }}>读取最新剧本并保留正文</Button>}{uncertain && <p role="alert">结果尚未确认，请使用原请求重试；正文和操作内容保持不变。</p>}{action === 'correct' && base?.version?.source_file && <p className="caption">修正后保留原始文件供对照，正文段落的原页码标注会取消。</p>}
    {command ? <><p><strong>确认{titles[action]}？</strong></p><p>{base?.script.title ?? title}</p>{['add_version', 'correct'].includes(action) && <p>正文 {Array.from(text).length} 字符，保存为待核对正文。</p>}{action === 'confirm' && <p>确认后本版本正文不可覆盖；此操作不会自动切换工作版本。</p>}{action === 'set_working' && <p>将版本 {base!.version!.version_no} 设为当前工作稿。</p>}<div className="member-actions"><Button disabled={pending || uncertain} onClick={() => setCommand(null)}>返回修改</Button><Button type="primary" aria-label={uncertain ? '用原剧本请求重试' : '确认剧本操作'} loading={pending} onClick={() => { void submit() }}>{uncertain ? '用原剧本请求重试' : '确认剧本操作'}</Button></div></>
      : <form onSubmit={review}><fieldset className="custody-fields" disabled={pending || needsReload}>{action === 'create' ? <label>剧本标题<Input required maxLength={100} value={title} onChange={event => setTitle(event.target.value)} /></label> : ['add_version', 'correct'].includes(action) ? <div className="account-field"><label htmlFor="script-text">剧本正文</label><Input.TextArea id="script-text" aria-describedby="script-text-hint" required rows={16} value={text} onChange={event => setText(event.target.value)} /><span id="script-text-hint" className="caption">最多100000字符；非空行将依次编号，无来源页码。</span></div> : <p>请核对版本 {base!.version!.version_no} 后继续。{action === 'confirm' ? '确认正文后应另行选择工作版本。' : '仅能选择已确认版本作为工作稿。'}</p>}<Button htmlType="submit" type="primary">核对剧本操作</Button></fieldset></form>}
  </div></Modal>
}
