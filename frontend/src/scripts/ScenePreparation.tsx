import { useState } from 'react'
import { Button, Input, Modal } from 'antd'
import { useQuery } from '@tanstack/react-query'
import type { Page } from '../api/crews'
import type { SceneCharacter, SceneResourceNeed } from '../api/creative'
import { useAuth } from '../auth/AuthProvider'
import { ErrorNotice } from '../auth/AccountScreens'
import { useBusinessSession } from '../crews/useBusinessSession'

const types = { equipment: '设备', prop: '道具', location: '场地', consumable: '耗材' }

export function ScenePreparation({ crewId, userId, characters, needs, canReadResources, onCharacters, onNeeds }: {
  crewId: string; userId: string; characters: SceneCharacter[]; needs: SceneResourceNeed[]; canReadResources: boolean
  onCharacters: (value: SceneCharacter[]) => void; onNeeds: (value: SceneResourceNeed[]) => void
}) {
  const [picking, setPicking] = useState<number | null>(null)
  function change(index: number, update: Partial<SceneResourceNeed>) {
    onNeeds(needs.map((need, i) => i === index ? { ...need, ...update } : need))
  }
  return <div className="scene-preparation">
    <h4>出场人物</h4>
    <p className="caption">登记剧中人物名称；在“人物与演员”中统一绑定演员，排期时再添加参与人。</p>
    {characters.map((character, index) => <div className="member-actions" key={index}>
      <label>人物名称 {index + 1}<Input maxLength={100} value={character.name} onChange={event => onCharacters(characters.map((item, i) => i === index ? { name: event.target.value } : item))} /></label>
      <Button onClick={() => onCharacters(characters.filter((_, i) => i !== index))}>移除人物 {index + 1}</Button>
    </div>)}
    <Button disabled={characters.length >= 100} onClick={() => onCharacters([...characters, { name: '' }])}>添加出场人物</Button>
    <h4>资源需求</h4>
    <p className="caption">可先列需求再关联资源。关联不代表预约获批，也不会扣减库存。</p>
    {needs.map((need, index) => <div className="scene-need" key={index}>
      <label>需求类型 {index + 1}<select value={need.resource_type} disabled={!!need.resource_id} onChange={event => change(index, { resource_type: event.target.value as SceneResourceNeed['resource_type'] })}>{Object.entries(types).map(([code, label]) => <option key={code} value={code}>{label}</option>)}</select></label>
      <label>需求名称 {index + 1}<Input maxLength={100} value={need.name} onChange={event => change(index, { name: event.target.value })} /></label>
      <label>需求数量 {index + 1}<Input type="number" min={1} max={1000000} value={need.quantity} onChange={event => change(index, { quantity: Number(event.target.value) })} /></label>
      <label>需求单位 {index + 1}<Input maxLength={10} disabled={!!need.resource_id} value={need.unit} onChange={event => change(index, { unit: event.target.value })} /></label>
      <p>{need.resource_id ? '已关联登记资源' : '待选资源'}</p>
      <div className="member-actions">{canReadResources && <Button onClick={() => setPicking(index)}>选择已登记资源 {index + 1}</Button>}{need.resource_id && <Button onClick={() => change(index, { resource_id: null })}>解除关联 {index + 1}</Button>}<Button onClick={() => onNeeds(needs.filter((_, i) => i !== index))}>移除需求 {index + 1}</Button></div>
    </div>)}
    <Button disabled={needs.length >= 100} onClick={() => onNeeds([...needs, { resource_type: 'equipment', name: '', quantity: 1, unit: '台', resource_id: null }])}>添加资源需求</Button>
    {picking !== null && <ResourcePicker key={`${picking}-${needs[picking].resource_type}`} crewId={crewId} userId={userId} type={needs[picking].resource_type} onClose={() => setPicking(null)} onSelect={resource => { change(picking, { resource_id: resource.id, name: resource.name, unit: resource.unit }); setPicking(null) }} />}
  </div>
}

export function ScenePreparationSummary({ characters, needs }: { characters: SceneCharacter[]; needs: SceneResourceNeed[] }) {
  return <><p>出场人物：{characters.map(item => item.name).join('、') || '未登记'}</p><ul>{needs.map((need, index) => <li key={index}>{types[need.resource_type]} · {need.name} · {need.quantity} {need.unit} · {need.resource_id ? '已关联登记资源' : '待选资源'}</li>)}</ul></>
}

type Choice = { id: string; name: string; unit: string; status: string; resource_type?: string }
function ResourcePicker({ crewId, userId, type, onSelect, onClose }: { crewId: string; userId: string; type: SceneResourceNeed['resource_type']; onSelect: (resource: Choice) => void; onClose: () => void }) {
  const { resources } = useAuth()
  const [page, setPage] = useState(1)
  const list = useQuery<Page<Choice>>({ queryKey: ['crews', userId, crewId, 'scene-resource-choices', type, page], queryFn: ({ signal }) => type === 'consumable' ? resources.stocks(crewId, page, signal) : resources.list(crewId, page, signal), retry: false })
  useBusinessSession(list.error)
  return <Modal title={`选择${types[type]}`} open footer={null} onCancel={onClose}>
    <ErrorNotice error={list.error} />{list.isPending && <p role="status">正在读取资源…</p>}
    {!list.isError && list.data && <><p className="caption">仅可选择当前可用且类型匹配的资源；数量与预约需在排期阶段核对。</p><ul className="crew-list">{list.data.items.map(resource => <li key={resource.id}><Button disabled={resource.status !== 'available' || type !== 'consumable' && resource.resource_type !== type} onClick={() => onSelect(resource)}>{resource.name} · {resource.unit}</Button></li>)}</ul>
      {list.data.items.length === 0 && <p>暂无资源，可保留未关联需求。</p>}
      <div className="list-pagination"><Button disabled={page === 1} onClick={() => setPage(page - 1)}>上一页资源</Button><span>第 {page} 页 · 共 {list.data.total} 项</span><Button disabled={page * 20 >= list.data.total} onClick={() => setPage(page + 1)}>下一页资源</Button></div></>}
  </Modal>
}
