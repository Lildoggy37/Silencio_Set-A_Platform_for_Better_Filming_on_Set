import { useRef, useState } from 'react'
import { Button, Modal } from 'antd'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useAuth } from '../auth/AuthProvider'
import { ErrorNotice } from '../auth/AccountScreens'
import { AccountError } from '../api/accounts'
import type { CastActor, CastingBody, CastingEntry } from '../api/casting'
import { useBusinessSession } from '../crews/useBusinessSession'

type Props = { crewId: string; userId: string; permissions: string[] }

export function CastingLibrary(props: Props) {
  const { creative } = useAuth()
  const [page, setPage] = useState(1), [script, setScript] = useState<string | null>(null), [locked, setLocked] = useState(false)
  const list = useQuery({ queryKey: ['crews', props.userId, props.crewId, 'casting-scripts', page], queryFn: ({ signal }) => creative.castingScripts(props.crewId, page, signal), retry: false })
  useBusinessSession(list.error)
  return <section><h3>人物与演员</h3><ErrorNotice error={list.error} />{list.isPending && <p role="status">正在读取剧本列表…</p>}
    {!list.isError && list.data && <><div className="member-actions">{list.data.items.map(item => <Button key={item.id} disabled={locked} onClick={() => setScript(item.id)}>{item.title} · 演员安排</Button>)}</div>{list.data.total === 0 && <p>请先创建剧本并登记场戏人物。</p>}
      <div className="list-pagination"><Button disabled={page === 1 || locked} onClick={() => setPage(page - 1)}>上一页选角剧本</Button><Button disabled={page * 20 >= list.data.total || locked} onClick={() => setPage(page + 1)}>下一页选角剧本</Button></div></>}
    {script && <CastingPanel key={script} {...props} scriptId={script} onRequestLock={setLocked} />}
  </section>
}

export function CastingPanel({ crewId, userId, scriptId, permissions, onRequestLock }: Props & { scriptId: string; onRequestLock?: (locked: boolean) => void }) {
  const { creative } = useAuth()
  const cache = useQueryClient()
  const [page, setPage] = useState(1), [selected, setSelected] = useState<CastingEntry | null>(null)
  const [command, setCommand] = useState<{ body: CastingBody; key: string; actorName: string | null } | null>(null)
  const [error, setError] = useState<unknown>(null), [busy, setBusy] = useState(false), [uncertain, setUncertain] = useState(false)
  const lock = useRef(false)
  const canEdit = permissions.includes('plan.edit') || permissions.includes('scene.review')
  const queryKey = ['crews', userId, crewId, 'casting', scriptId]
  const list = useQuery({ queryKey: [...queryKey, page], queryFn: ({ signal }) => creative.casting(crewId, scriptId, page, signal), retry: false })
  useBusinessSession(error ?? list.error)
  function prepare(entry: CastingEntry, actor: CastActor | null) {
    setSelected(null); setError(null)
    onRequestLock?.(true)
    setCommand({ body: { expected_lock_version: list.data!.lock_version, character_name: entry.character_name, member_id: actor?.id ?? null, membership_joined_at: actor?.membership_joined_at ?? null }, key: crypto.randomUUID(), actorName: actor?.display_name ?? null })
  }
  async function save() {
    if (lock.current) return
    lock.current = true; setBusy(true); setError(null)
    try {
      await creative.saveCasting(crewId, scriptId, command!.body, command!.key)
      setCommand(null); setUncertain(false)
      onRequestLock?.(false)
      // Casting affects plans, call sheets, notification validity and next-shoot summaries.
      void cache.invalidateQueries({ queryKey: ['crews', userId, crewId] })
    } catch (failure) {
      setError(failure)
      const unknown = !(failure instanceof AccountError) || ['NETWORK_UNCONFIRMED', 'INVALID_RESPONSE', 'SERVICE_UNAVAILABLE'].includes(failure.code) || failure.status >= 500
      setUncertain(unknown)
      if (!unknown) { setCommand(null); onRequestLock?.(false); void list.refetch() }
    } finally { lock.current = false; setBusy(false) }
  }
  return <section className="creative-panel"><div className="section-heading"><h3>人物与演员绑定</h3><Button aria-label="刷新演员绑定" onClick={() => { void list.refetch() }} loading={list.isFetching}>刷新演员绑定</Button></div>
    <p className="caption">同一剧本中的同名人物共用绑定。这里显示工作稿已登记人物及历史绑定；绑定不会自动添加排期参与人。改绑后须复核相关正式安排。</p>
    <ErrorNotice error={error ?? list.error} />{list.isPending && <p role="status">正在读取演员绑定…</p>}
    {!list.isError && list.data && <>{list.data.items.length === 0 && <p>请先设置剧本工作版本，并在场戏中登记人物。</p>}<ul className="crew-list">{list.data.items.map(entry => <li key={entry.character_name}><div><strong>{entry.character_name}</strong><p>{entry.display_name ?? '尚未绑定演员'}{entry.status === 'invalid' && ' · 身份已失效，请重新绑定'}{!entry.is_current_character && ' · 工作稿未登记此人物'}</p></div>{canEdit && <div className="member-actions"><Button disabled={!!command || list.isFetching} onClick={() => setSelected(entry)}>选择演员 · {entry.character_name}</Button>{entry.member_id && <Button disabled={!!command || list.isFetching} onClick={() => prepare(entry, null)}>解除绑定 · {entry.character_name}</Button>}</div>}</li>)}</ul>
      <div className="list-pagination"><Button disabled={page === 1 || !!command} onClick={() => setPage(page - 1)}>上一页人物</Button><span>共 {list.data.total} 位人物</span><Button disabled={page * 20 >= list.data.total || !!command} onClick={() => setPage(page + 1)}>下一页人物</Button></div></>}
    {selected && <ActorPicker key={selected.character_name} crewId={crewId} userId={userId} scriptId={scriptId} onClose={() => setSelected(null)} onSelect={actor => prepare(selected, actor)} />}
    <Modal title="确认演员绑定" open={!!command} footer={null} closable={!busy && !uncertain} keyboard={!busy && !uncertain} mask={{ closable: !busy && !uncertain }} onCancel={() => { setCommand(null); onRequestLock?.(false) }}>
      <p>{command?.body.character_name}：{command?.actorName ? `确认绑定 ${command.actorName}？` : '确认解除演员绑定？'}</p><p>相关已发布排期和通告将标记为待复核。</p><ErrorNotice error={error} />{uncertain && <p role="alert">结果尚未确认，请使用原请求重试。</p>}
      <Button type="primary" aria-label={uncertain ? '用原绑定请求重试' : '确认绑定操作'} loading={busy} onClick={() => { void save() }}>{uncertain ? '用原绑定请求重试' : '确认绑定操作'}</Button>
    </Modal>
  </section>
}

function ActorPicker({ crewId, userId, scriptId, onSelect, onClose }: { crewId: string; userId: string; scriptId: string; onSelect: (actor: CastActor) => void; onClose: () => void }) {
  const { creative } = useAuth()
  const [page, setPage] = useState(1)
  const list = useQuery({ queryKey: ['crews', userId, crewId, 'casting-actors', scriptId, page], queryFn: ({ signal }) => creative.castingActors(crewId, scriptId, page, signal), retry: false })
  useBusinessSession(list.error)
  return <Modal title="选择在组演员" open footer={null} onCancel={onClose}><ErrorNotice error={list.error} />{list.isPending && <p role="status">正在读取演员…</p>}
    {!list.isError && list.data && <><p>仅显示具有演员岗位的有效在组成员。</p><ul className="crew-list">{list.data.items.map(actor => <li key={actor.id}><Button onClick={() => onSelect(actor)}>{actor.display_name}</Button></li>)}</ul>{list.data.total === 0 && <p>暂无可选演员，请先邀请成员并设置演员岗位。</p>}
      <div className="list-pagination"><Button disabled={page === 1} onClick={() => setPage(page - 1)}>上一页演员</Button><Button disabled={page * 20 >= list.data.total} onClick={() => setPage(page + 1)}>下一页演员</Button></div></>}
  </Modal>
}
