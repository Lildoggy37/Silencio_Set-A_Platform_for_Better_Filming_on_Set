import { useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { Button, Input, Modal } from 'antd'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useAuth } from '../auth/AuthProvider'
import { ErrorNotice } from '../auth/AccountScreens'
import { useBusinessSession } from '../crews/useBusinessSession'
import { AccountError } from '../api/accounts'
import { locationPhotoUrl } from '../api/locations'
import type { LocationCommand, LocationGallery, LocationPhoto } from '../api/locations'

type Action = 'edit_details' | 'upload_photo' | 'edit_photo' | 'remove_photo'
const titles: Record<Action, string> = { edit_details: '编辑场地资料', upload_photo: '上传勘景照片', edit_photo: '编辑照片说明', remove_photo: '移除勘景照片' }
type Command = LocationCommand | { action: 'upload_photo'; file: File; description: string | null; version: number; key: string }

export function LocationPanel({ crewId, userId, resourceId }: { crewId: string; userId: string; resourceId: string }) {
  const { resources } = useAuth()
  const cache = useQueryClient()
  const [action, setAction] = useState<{ kind: Action; photo?: LocationPhoto } | null>(null)
  const key = ['crews', userId, crewId, 'location', resourceId]
  const query = useQuery({ queryKey: key, queryFn: ({ signal }) => resources.location(crewId, resourceId, signal), retry: false })
  useBusinessSession(query.error)
  const current = query.isError ? undefined : query.data
  function saved(value: LocationGallery) {
    setAction(null); cache.setQueryData(key, value)
    void cache.invalidateQueries({ queryKey: key })
    void cache.invalidateQueries({ queryKey: ['crews', userId, crewId, 'resource', resourceId] })
    void cache.invalidateQueries({ queryKey: ['crews', userId, crewId, 'resources'] })
  }
  return <section className="location-panel"><div className="section-heading"><h3>场地勘景</h3><Button aria-label="刷新场地资料" onClick={() => { void query.refetch() }} loading={query.isFetching}>刷新场地资料</Button></div>
    <ErrorNotice error={query.error} />{query.isPending && <p role="status">正在读取场地资料与照片…</p>}
    {current && <><div className="location-notes"><p>地址：{current.details.address ?? '未填写'}</p><p>进入方式：{current.details.access_instructions ?? '未填写'}</p><p>拍摄限制：{current.details.shooting_constraints ?? '未填写'}</p></div>
      <div className="member-actions">{current.allowed_actions.includes('edit_details') && <Button onClick={() => setAction({ kind: 'edit_details' })}>编辑场地资料</Button>}{current.allowed_actions.includes('upload_photo') && <Button disabled={current.photos.length >= 20} onClick={() => setAction({ kind: 'upload_photo' })}>上传勘景照片</Button>}</div>
      <p className="caption">共 {current.photos.length} / 20 张。照片已去除定位等原始元数据，不会自动交给AI分析；剧组内有资源查看权限的成员可查看。</p>
      {current.photos.length === 0 ? <p>尚未上传勘景照片。</p> : <div className="location-photo-grid">{current.photos.map((photo, index) => <PhotoTile key={photo.id} crewId={crewId} resourceId={resourceId} photo={photo} index={index}>
        <div className="member-actions">{current.allowed_actions.includes('edit_photo') && <Button onClick={() => setAction({ kind: 'edit_photo', photo })}>编辑照片说明 {index + 1}</Button>}{current.allowed_actions.includes('remove_photo') && <Button onClick={() => setAction({ kind: 'remove_photo', photo })}>移除照片 {index + 1}</Button>}</div>
      </PhotoTile>)}</div>}
      {action && <LocationForm key={`${action.kind}-${action.photo?.id}`} crewId={crewId} current={current} action={action.kind} photo={action.photo} onClose={() => setAction(null)} onSaved={saved} />}
    </>}
  </section>
}

function PhotoTile({ crewId, resourceId, photo, index, children }: { crewId: string; resourceId: string; photo: LocationPhoto; index: number; children: ReactNode }) {
  const [failed, setFailed] = useState(false), [attempt, setAttempt] = useState(0)
  return <article className="location-photo-card">{failed ? <div className="location-photo-error"><p role="alert">照片读取失败。</p><Button onClick={() => { setFailed(false); setAttempt(attempt + 1) }}>重新读取照片 {index + 1}</Button></div>
    : <img key={attempt} src={locationPhotoUrl(crewId, resourceId, photo.id)} alt={photo.description ?? `勘景照片 ${index + 1}`} loading="lazy" onError={() => setFailed(true)} />}
    <p className="location-notes">{photo.description ?? '未填写照片说明'}</p><p className="caption">{photo.width_px} × {photo.height_px} · {(photo.byte_size / 1024).toFixed(1)} KiB</p>
    <a href={locationPhotoUrl(crewId, resourceId, photo.id, 'full')} target="_blank" rel="noopener noreferrer">查看大图 {index + 1}</a>{children}
  </article>
}

function LocationForm({ crewId, current, action, photo, onClose, onSaved }: { crewId: string; current: LocationGallery; action: Action; photo?: LocationPhoto; onClose: () => void; onSaved: (value: LocationGallery) => void }) {
  const { resources } = useAuth()
  const [base, setBase] = useState(current)
  const [address, setAddress] = useState(current.details.address ?? ''), [access, setAccess] = useState(current.details.access_instructions ?? ''), [constraints, setConstraints] = useState(current.details.shooting_constraints ?? '')
  const [description, setDescription] = useState(photo?.description ?? ''), [file, setFile] = useState<File | null>(null)
  const [command, setCommand] = useState<Command | null>(null)
  const [pending, setPending] = useState(false), [uncertain, setUncertain] = useState(false), [needsReload, setNeedsReload] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const lock = useRef(false)
  useBusinessSession(error)
  const stillAllowed = base.allowed_actions.includes(action) && (!photo || base.photos.some(item => item.id === photo.id)) && (action !== 'upload_photo' || base.photos.length < 20)
  function prepare() {
    const body = { expected_lock_version: base.resource_lock_version, ...(action === 'edit_details' ? { address: address.trim() || null, access_instructions: access.trim() || null, shooting_constraints: constraints.trim() || null }
      : action === 'edit_photo' ? { description: description.trim() || null } : {}) }
    if (action === 'upload_photo') setCommand({ action, key: crypto.randomUUID(), file: file!, description: description.trim() || null, version: base.resource_lock_version })
    else setCommand({ action, photoId: photo?.id, body, key: crypto.randomUUID() })
    setError(null)
  }
  async function submit() {
    if (lock.current) return
    const frozen = command!
    lock.current = true; setPending(true); setError(null)
    try { onSaved(await (frozen.action === 'upload_photo' ? resources.uploadPhoto(crewId, current.resource_id, frozen.file, frozen.description, frozen.version, frozen.key)
      : resources.locationWrite(crewId, current.resource_id, frozen))) }
    catch (failure) {
      setError(failure)
      const unknown = !(failure instanceof AccountError) || failure.status >= 500 || ['NETWORK_UNCONFIRMED', 'INVALID_RESPONSE', 'SERVICE_UNAVAILABLE'].includes(failure.code)
      setUncertain(unknown)
      if (!unknown) { setCommand(null); setNeedsReload(['VERSION_CONFLICT', 'LOCATION_PHOTO_LIMIT', 'NOT_FOUND', 'FORBIDDEN'].includes(failure.code)) }
    } finally { lock.current = false; setPending(false) }
  }
  async function reload() {
    setPending(true); setError(null)
    try { setBase(await resources.location(crewId, current.resource_id)); setNeedsReload(false) }
    catch (failure) { setError(failure) }
    finally { setPending(false) }
  }
  return <Modal title={titles[action]} open footer={null} width={720} onCancel={onClose} closable={!pending && !uncertain} keyboard={!pending && !uncertain} mask={{ closable: !pending && !uncertain }}><div className="role-editor"><ErrorNotice error={error} />
    {uncertain && <p role="alert">结果尚未确认，请使用原请求重试，文件和填写内容保持不变。</p>}{needsReload && <Button loading={pending} onClick={() => { void reload() }}>读取最新场地并保留输入</Button>}
    {command ? <><p><strong>确认{titles[action]}？</strong></p><p>{current.resource_name}</p>{command.action === 'upload_photo' && <p>{command.file.name} · 上传后统一校正方向、移除原始元数据，保存展示图。</p>}{action === 'remove_photo' && <p>照片将从场地资料中移除，已有查看链接也将失效。</p>}
      <div className="member-actions"><Button disabled={pending || uncertain} onClick={() => setCommand(null)}>返回修改场地内容</Button><Button type="primary" loading={pending} aria-label={uncertain ? '用原场地请求重试' : '确认场地操作'} onClick={() => { void submit() }}>{uncertain ? '用原场地请求重试' : '确认场地操作'}</Button></div></>
      : <form onSubmit={event => { event.preventDefault(); prepare() }}>{!stillAllowed && <p role="alert">场地权限、照片或数量已变化，请关闭窗口后重新核对。</p>}<fieldset className="custody-fields" disabled={pending || needsReload || !stillAllowed}>
        {action === 'edit_details' ? <><label>场地地址<Input value={address} maxLength={300} onChange={event => setAddress(event.target.value)} /></label><label>进入方式<Input.TextArea value={access} maxLength={1000} onChange={event => setAccess(event.target.value)} /></label><label>拍摄限制<Input.TextArea value={constraints} maxLength={2000} onChange={event => setConstraints(event.target.value)} /></label></>
          : action === 'remove_photo' ? <p>{photo?.description ?? '未填写说明的勘景照片'}</p>
            : <>{action === 'upload_photo' && <><label>勘景照片文件<input type="file" accept="image/jpeg,image/png,image/webp" required={!file} onChange={event => { const next = event.target.files?.[0] ?? null; setFile(next); setError(next && next.size > 10 * 1024 * 1024 ? new AccountError('PHOTO_TOO_LARGE') : null) }} /></label>{file && <p>已选：{file.name}</p>}<p className="caption">JPEG、PNG或静态WebP，最多10MiB、2500万像素。动图和HEIC请先转换；照片不会自动发给模型。</p></>}<label>照片说明<Input.TextArea value={description} maxLength={500} onChange={event => setDescription(event.target.value)} /></label></>}
        <Button htmlType="submit" type="primary" disabled={action === 'upload_photo' && (!file || file.size > 10 * 1024 * 1024)}>核对场地操作</Button>
      </fieldset></form>}
  </div></Modal>
}
