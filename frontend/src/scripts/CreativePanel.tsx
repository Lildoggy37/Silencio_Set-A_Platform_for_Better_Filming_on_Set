import { useRef, useState } from 'react'
import { Button, Input, Modal } from 'antd'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useAuth } from '../auth/AuthProvider'
import { ErrorNotice } from '../auth/AccountScreens'
import { AccountError } from '../api/accounts'
import type { CreativeItem, CreativeKind } from '../api/creative'
import { useBusinessSession } from '../crews/useBusinessSession'
import { ScenePreparation, ScenePreparationSummary } from './ScenePreparation'
import { SourceReviewPanel } from './SourceReviewPanel'

const statuses = { draft: '草稿', pending_review: '待审核', confirmed: '已确认', rejected: '已退回' }
const actionNames: Record<string, string> = { create: '创建版本', save: '保存草稿', submit: '提交审核', review: '审核', reopen: '重新打开', confirm: '确认稿件' }

export function CreativePanel({ crewId, userId, sourceId, permissions, kind = 'breakdowns', initialId, selfMemberId }: { selfMemberId?: string; crewId: string; userId: string; sourceId: string; permissions: string[]; kind?: CreativeKind; initialId?: string }) {
  const { creative } = useAuth()
  const cache = useQueryClient()
  const [page, setPage] = useState(1)
  const [id, setId] = useState<string | null>(initialId ?? null)
  const [draft, setDraft] = useState<CreativeItem[] | null>(null)
  const [baseVersion, setBaseVersion] = useState<number | null>(null)
  const [sceneRevision, setSceneRevision] = useState<string | null>(null)
  const [history, setHistory] = useState(false)
  const [reviewPage, setReviewPage] = useState(1)
  const [decision, setDecision] = useState('accept')
  const [note, setNote] = useState('')
  const [command, setCommand] = useState<{ action: string; body: Record<string, unknown>; key: string } | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [pending, setPending] = useState(false)
  const [uncertain, setUncertain] = useState(false)
  const [needsReload, setNeedsReload] = useState(false)
  const lock = useRef(false)
  const prefix = kind === 'breakdowns' ? 'scene' : 'storyboard'
  const canEdit = permissions.includes(`${prefix}.edit`), canReview = permissions.includes(`${prefix}.review`)
  const title = kind === 'breakdowns' ? '场戏拆解' : '文字分镜'
  const list = useQuery({ queryKey: ['crews', userId, crewId, kind, sourceId, page], queryFn: ({ signal }) => creative.list(crewId, kind, sourceId, page, signal), retry: false })
  const detail = useQuery({ queryKey: ['crews', userId, crewId, kind, id], queryFn: () => creative.get(crewId, kind, id!), enabled: !!id, retry: false })
  const reviews = useQuery({ queryKey: ['crews', userId, crewId, kind, id, 'reviews', reviewPage], queryFn: () => creative.reviews(crewId, kind, id!, reviewPage), enabled: history && !!id, retry: false })
  useBusinessSession(error ?? list.error ?? detail.error ?? reviews.error)
  const current = detail.isError ? undefined : detail.data
  const items = draft ?? current?.items ?? []
  function prepare(action: string, extra: Record<string, unknown> = {}) {
    const body = action === 'create' ? { source_id: sourceId, ...extra } : { expected_lock_version: baseVersion ?? current!.lock_version, ...extra }
    if (lock.current || uncertain) return
    const prepared = { action, body, key: crypto.randomUUID() }
    setCommand(prepared); setError(null)
    if (action === 'save') void submit(prepared)
  }
  function save() {
    const values = items.map(item => kind === 'breakdowns' ? { scene_id: item.scene_id ?? null, scene_number: item.scene_number, location_text: item.location_text, time_of_day: item.time_of_day,
      source_start: item.source_start, source_end: item.source_end, production_note: item.production_note || null, characters: item.characters ?? [], resource_needs: item.resource_needs ?? [] }
      : { shot_number: item.shot_number, shot_size: item.shot_size, visual_content: item.visual_content, camera_movement: item.camera_movement || null, dialogue_sound: item.dialogue_sound || null, duration_seconds: item.duration_seconds ?? null })
    prepare('save', { [kind === 'breakdowns' ? 'scenes' : 'shots']: values })
  }
  function edit(index: number, field: keyof CreativeItem, value: CreativeItem[keyof CreativeItem]) {
    setBaseVersion(baseVersion ?? current!.lock_version)
    setDraft(items.map((item, i) => i === index ? { ...item, [field]: value } : item))
  }
  function add() {
    setBaseVersion(baseVersion ?? current!.lock_version)
    setDraft([...items, kind === 'breakdowns' ? { scene_number: String(items.length + 1), location_text: '', time_of_day: 'unknown', source_start: 1, source_end: 1 } : { shot_number: String(items.length + 1), shot_size: '', visual_content: '' }])
  }
  async function submit(prepared = command) {
    if (lock.current || !prepared) return
    lock.current = true; setPending(true); setError(null)
    try {
      const result = await creative.write(crewId, kind, prepared.action, id, prepared.body, prepared.key)
      setId(result.id); setDraft(null); setBaseVersion(null); setCommand(null); setUncertain(false); setNote(''); setSceneRevision(null)
      cache.setQueryData(['crews', userId, crewId, kind, result.id], result)
      void cache.invalidateQueries({ queryKey: ['crews', userId, crewId, kind] })
    } catch (failure) {
      setError(failure)
      const unknown = !(failure instanceof AccountError) || ['NETWORK_UNCONFIRMED', 'INVALID_RESPONSE', 'SERVICE_UNAVAILABLE'].includes(failure.code) || failure.status >= 500
      setUncertain(unknown)
      if (!unknown) { setCommand(null); setNeedsReload(true) }
    } finally { lock.current = false; setPending(false) }
  }
  async function reload() {
    setPending(true); setError(null)
    try { if (id) { const fresh = await creative.get(crewId, kind, id); cache.setQueryData(['crews', userId, crewId, kind, id], fresh); setBaseVersion(fresh.lock_version) } setNeedsReload(false) }
    catch (failure) { setError(failure) }
    finally { setPending(false) }
  }
  return <section className="creative-panel"><div className="section-heading"><h3>{title}</h3><div className="member-actions"><Button onClick={() => { void list.refetch(); if (id) void detail.refetch() }}>刷新{title}</Button>{canEdit && <Button disabled={!!draft} onClick={() => prepare('create')}>新建{title}</Button>}</div></div>
    <ErrorNotice error={error ?? list.error ?? detail.error} />{needsReload && <Button onClick={() => { void reload() }} loading={pending}>读取最新创作版本并保留输入</Button>}
    {list.isPending && <p role="status">正在读取{title}…</p>}
    {!list.isError && list.data && <><div className="member-actions">{list.data.items.map(item => <Button key={item.id} disabled={!!draft} onClick={() => { setId(item.id); setBaseVersion(null); setSceneRevision(null); setHistory(false) }}>第 {item.version_no} 版 · {statuses[item.review_status]}</Button>)}</div><div className="list-pagination"><Button disabled={page === 1 || !!draft} onClick={() => setPage(page - 1)}>上一页{title}</Button><span>共 {list.data.total} 版</span><Button disabled={page * 20 >= list.data.total || !!draft} onClick={() => setPage(page + 1)}>下一页{title}</Button></div></>}
    {current && <><p>第 {current.version_no} 版 · {statuses[current.review_status]}{draft && ' · 有未保存修改'}</p>
      <div className="member-actions">{canEdit && current.review_status === 'draft' && <><Button disabled={items.length >= 100 || needsReload} onClick={add}>添加{kind === 'breakdowns' ? '场戏' : '镜头'}</Button><Button disabled={needsReload} onClick={save}>保存草稿</Button><Button disabled={!!draft || needsReload} onClick={() => prepare(canReview && current.created_by === selfMemberId ? 'confirm' : 'submit')}>{canReview && current.created_by === selfMemberId ? '确认稿件' : '提交审核'}</Button>{draft && <Button onClick={() => { setDraft(null); setBaseVersion(null) }}>放弃本次编辑</Button>}</>}
        {canEdit && current.review_status === 'rejected' && <Button onClick={() => prepare('reopen')}>重新打开</Button>}{canEdit && current.review_status === 'confirmed' && <Button onClick={() => prepare('create', { copy_from: current.id })}>复制为新草稿</Button>}<Button onClick={() => setHistory(!history)}>审核历史</Button>
      </div>
      {items.map((item, index) => <fieldset className="creative-item" key={item.id ?? index} disabled={pending || needsReload || !canEdit || current.review_status !== 'draft'}><legend>{kind === 'breakdowns' ? `场戏 ${index + 1}` : `镜头 ${index + 1}`}</legend>
        {kind === 'breakdowns' ? <><label>场次编号<Input required maxLength={20} value={item.scene_number} onChange={e => edit(index, 'scene_number', e.target.value)} /></label><label>剧情地点<Input required maxLength={100} value={item.location_text} onChange={e => edit(index, 'location_text', e.target.value)} /></label><label>日夜<select value={item.time_of_day} onChange={e => edit(index, 'time_of_day', e.target.value)}><option value="unknown">待确认</option><option value="day">日</option><option value="night">夜</option><option value="other">其他</option></select></label><label>原文起始段落<Input type="number" min={1} max={100000} value={item.source_start} onChange={e => edit(index, 'source_start', Number(e.target.value))} /></label><label>原文结束段落<Input type="number" min={1} max={100000} value={item.source_end} onChange={e => edit(index, 'source_end', Number(e.target.value))} /></label><label>制作备注<Input.TextArea maxLength={2000} value={item.production_note ?? ''} onChange={e => edit(index, 'production_note', e.target.value)} /></label></>
          : <><label>镜号<Input maxLength={20} value={item.shot_number} onChange={e => edit(index, 'shot_number', e.target.value)} /></label><label>景别<Input maxLength={30} value={item.shot_size} onChange={e => edit(index, 'shot_size', e.target.value)} /></label><label>画面内容<Input.TextArea maxLength={2000} value={item.visual_content} onChange={e => edit(index, 'visual_content', e.target.value)} /></label><label>运镜<Input maxLength={200} value={item.camera_movement ?? ''} onChange={e => edit(index, 'camera_movement', e.target.value)} /></label><label>对白与声音<Input.TextArea maxLength={2000} value={item.dialogue_sound ?? ''} onChange={e => edit(index, 'dialogue_sound', e.target.value)} /></label><label>预计时长（秒）<Input type="number" min={1} value={item.duration_seconds ?? ''} onChange={e => edit(index, 'duration_seconds', e.target.value ? Number(e.target.value) : null)} /></label></>}
        {kind === 'breakdowns' && <ScenePreparation crewId={crewId} userId={userId} characters={item.characters ?? []} needs={item.resource_needs ?? []} canReadResources={permissions.includes('resource.read')} onCharacters={value => edit(index, 'characters', value)} onNeeds={value => edit(index, 'resource_needs', value)} />}
        {canEdit && current.review_status === 'draft' && <Button onClick={() => { setBaseVersion(baseVersion ?? current.lock_version); setDraft(items.filter((_, i) => i !== index)) }}>移除此条</Button>}
      </fieldset>)}
      {kind === 'breakdowns' && current.review_status === 'confirmed' && permissions.includes('storyboard.read') && <div className="member-actions">{current.items.map(item => <Button key={item.id} onClick={() => setSceneRevision(item.id!)}>场戏 {item.scene_number} 的分镜</Button>)}</div>}
      {canReview && current.review_status === 'pending_review' && <form onSubmit={event => { event.preventDefault(); prepare('review', { decision, review_note: note.trim() || null }) }} className="role-editor"><label>审核决定<select value={decision} onChange={event => setDecision(event.target.value)}><option value="accept">确认通过</option><option value="reject">退回修改</option></select></label><label>审核说明<Input.TextArea required={decision === 'reject'} maxLength={500} value={note} onChange={event => setNote(event.target.value)} /></label><Button htmlType="submit">核对审核决定</Button></form>}
      {history && <><ErrorNotice error={reviews.error} />{!reviews.isError && reviews.data && <><ul className="resource-bookings">{reviews.data.items.map(review => <li key={review.id}><strong>{review.decision === 'accept' ? '确认通过' : '退回修改'} · 依据版本 {review.reviewed_lock_version}</strong><p>{review.review_note}</p><details><summary>查看当时的内容快照</summary>{review.content_snapshot.items.map((entry, i) => <div key={i}>{kind === 'breakdowns' ? <><p>{entry.scene_number} · {entry.location_text} · 原文段落 {entry.source_start}—{entry.source_end}</p><p>日夜：{entry.time_of_day} · 制作备注：{entry.production_note ?? '—'}</p><ScenePreparationSummary characters={entry.characters ?? []} needs={entry.resource_needs ?? []} /></> : <><p>{entry.shot_number} · {entry.shot_size} · {entry.visual_content}</p><p>运镜：{entry.camera_movement ?? '—'}</p><p>对白与声音：{entry.dialogue_sound ?? '—'}</p><p>预计时长：{entry.duration_seconds ?? '未填写'} 秒</p></>}</div>)}</details></li>)}</ul><div className="list-pagination"><Button disabled={reviewPage === 1} onClick={() => setReviewPage(reviewPage - 1)}>上一页审核</Button><Button disabled={reviewPage * 20 >= reviews.data.total} onClick={() => setReviewPage(reviewPage + 1)}>下一页审核</Button></div></>}</>}
    </>}
    {current && <SourceReviewPanel key={current.id} crewId={crewId} userId={userId} kind={kind} objectId={current.id} permissions={permissions} />}
    {sceneRevision && <CreativePanel selfMemberId={selfMemberId} key={sceneRevision} crewId={crewId} userId={userId} sourceId={sceneRevision} permissions={permissions} kind="storyboards" />}
    <Modal title={command ? actionNames[command.action] : ''} open={!!command} footer={null} onCancel={() => setCommand(null)} closable={!pending && !uncertain} keyboard={!pending && !uncertain} mask={{ closable: !pending && !uncertain }}>
      <ErrorNotice error={error} /><p>确认{command ? actionNames[command.action] : ''}？</p><p>{command?.action === 'save' ? `将保存 ${items.length} 条内容。` : '审核状态与来源将在提交时重新检查。'}</p>{uncertain && <p role="alert">结果尚未确认，请用原请求重试。</p>}
      <Button type="primary" aria-label={uncertain ? '用原创作请求重试' : '确认创作操作'} loading={pending} onClick={() => { void submit() }}>{uncertain ? '用原创作请求重试' : '确认创作操作'}</Button>
    </Modal>
  </section>
}
