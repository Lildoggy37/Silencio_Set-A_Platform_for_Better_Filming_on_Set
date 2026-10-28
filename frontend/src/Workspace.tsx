import { useState } from 'react'
import { Button, Modal } from 'antd'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { fetchHealth } from './api/health'
import type { Dependencies, DependencyStatus } from './api/health'
import type { User } from './api/accounts'
import { useAuth } from './auth/AuthProvider'
import { ErrorNotice } from './auth/AccountScreens'
import { CrewPicker, MemberDirectory, useBusinessSession } from './crews/CrewScreens'
import { MemberExitDialog } from './crews/MemberExitDialog'
import { TaskBoard } from './tasks/TaskBoard'
import { NotificationCenter } from './notifications/NotificationCenter'
import { AvailabilityPanel } from './availability/AvailabilityPanel'
import { ResourceBoard } from './resources/ResourceBoard'

const tabs = ['总览', '排期', '资源', '成员'] as const
type Tab = typeof tabs[number]
const titles: Record<Tab, string> = { 总览: '剧组总览', 排期: '拍摄排期', 资源: '场地与资源', 成员: '剧组成员' }
const descriptions: Record<Tab, string> = {
  总览: '进入剧组后，在这里查看拍摄安排、待完成与待确认任务。',
  排期: '在这里整理拍摄计划、检查冲突，并生成和发布通告单。',
  资源: '在这里查看设备与场地，管理资源预约、借还和勘景照片。',
  成员: '在这里查看剧组岗位、个人档期、待办和通告确认情况。',
}
const statusNames: Record<DependencyStatus, string> = {
  ok: '已连接', unavailable: '连接不可用', not_configured: '未配置',
}

function UtilityIcon({ kind }: { kind: 'folder' | 'bell' | 'account' }) {
  const paths = {
    folder: 'M3 7h7l2 2h9v11H3V7Zm0 0V4h7l2 3',
    bell: 'M5 17h14l-2-3V9a5 5 0 0 0-10 0v5l-2 3Zm5 3h4',
    account: 'M5 21v-3a7 7 0 0 1 14 0v3M16 7a4 4 0 1 1-8 0 4 4 0 0 1 8 0Z',
  }
  return <svg width="21" height="21" viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.4"><path d={paths[kind]} /></svg>
}

function ServiceStatus() {
  const live = useQuery({ queryKey: ['health', 'live'], queryFn: ({ signal }) => fetchHealth('live', signal) })
  const ready = useQuery({ queryKey: ['health', 'ready'], queryFn: ({ signal }) => fetchHealth('ready', signal) })
  const busy = live.isFetching || ready.isFetching
  const names: Record<keyof Dependencies, string> = { database: 'PostgreSQL', redis: 'Redis', rabbitmq: 'RabbitMQ' }
  const apiText = live.isPending ? '检查中' : live.isError ? '无法连接' : '在线'

  return <section className="service-status" aria-labelledby="service-title">
    <div className="section-heading">
      <h2 id="service-title">开发环境连接</h2>
      <Button size="small" onClick={() => { void live.refetch(); void ready.refetch() }} loading={busy}>重新检查</Button>
    </div>
    <div className="connection-grid" aria-live="polite">
      <div><span>API</span><strong>{apiText}</strong></div>
      {(Object.keys(names) as (keyof Dependencies)[]).map(name => <div key={name}>
        <span>{names[name]}</span>
        <strong>{ready.data?.dependencies ? statusNames[ready.data.dependencies[name]] : ready.isPending ? '检查中' : '状态未知'}</strong>
      </div>)}
    </div>
    <p className="caption">{live.isError
      ? '尚未连接API。请先启动后端，再重新检查；页面不会以示例数据替代服务结果。'
      : ready.data?.ready
        ? '依赖连通检查通过。不代表业务模块、数据库迁移或后台任务消费已经通过验收。'
        : '尚不能确认全部依赖就绪。缺配置或依赖未启动时，检查会返回503。'}</p>
    {ready.data && <p className="request-id">检查标识 · {ready.data.requestId}</p>}
  </section>
}

export default function Workspace({ user, preview, onAccount, initialCrewId }: { user?: User; preview: boolean; onAccount: () => void; initialCrewId?: string }) {
  const [tab, setTab] = useState<Tab>('总览')
  const [dialog, setDialog] = useState<'项目资料' | '通知' | null>(null)
  const [picker, setPicker] = useState(false)
  const [leaving, setLeaving] = useState(false)
  const [taskToOpen, setTaskToOpen] = useState<string | null>(null)
  const [resourceToOpen, setResourceToOpen] = useState<string | null>(null)
  const [calendar, setCalendar] = useState<'own' | 'crew' | null>(null)
  const cache = useQueryClient()
  const [crewId, setCrewId] = useState<string | null>(initialCrewId ?? null)
  const { crews } = useAuth()
  const detail = useQuery({ queryKey: ['crews', user?.id, crewId, 'detail'], queryFn: ({ signal }) => crews.get(crewId!, signal), enabled: !preview && !!user && crewId !== null, retry: false })
  useBusinessSession(detail.error)
  const selected = detail.isError ? undefined : detail.data
  const openTask = (id: string) => { setLeaving(false); setDialog(null); setTab('总览'); setTaskToOpen(id) }
  const openResource = (id: string) => { setLeaving(false); setDialog(null); setTab('资源'); setResourceToOpen(id) }

  return <>
    <a className="skip-link" href="#main">跳转到内容</a>
    <header className="topbar">
      <div className="identity"><span className="monogram">SS</span><span>Silencio Set<small>寂静片场</small></span></div>
      {preview ? <div className="project-switch"><span>当前项目</span><strong>尚未选择剧组</strong></div>
        : <button type="button" className="project-switch" aria-label="切换剧组" onClick={() => setPicker(true)}><span>当前项目 · 切换剧组</span><strong>{selected?.project_name ?? '尚未选择剧组'}</strong></button>}
      <nav aria-label="主要导航">{tabs.map(name => <button key={name} type="button" aria-pressed={tab === name} onClick={() => setTab(name)}>{name}</button>)}</nav>
      <div className="utilities">
        <button type="button" aria-label="项目资料" onClick={() => setDialog('项目资料')}><UtilityIcon kind="folder" /></button>
        <button type="button" aria-label="通知" onClick={() => setDialog('通知')}><UtilityIcon kind="bell" /></button>
        {user && <span className="account-name" title={user.display_name ?? ''}>{user.display_name}</span>}
        <button type="button" aria-label="个人账号" onClick={onAccount}><UtilityIcon kind="account" /></button>
      </div>
    </header>

    <main id="main" className="workspace">
      <div className="workspace-grid">
        <aside className="shoot-panel" aria-labelledby="shoot-title">
          <span className="eyebrow">NEXT SHOOT</span><h2 id="shoot-title">下一次拍摄</h2>
          <p className="shoot-day">待安排</p><span className="shoot-day-caption">尚无已发布拍摄日</span>
          <div className="shoot-empty"><hr /><p>集合待定</p><span>接入剧组与排期后显示集合时间及地点</span></div>
          <span className="shoot-footer">拍摄信息随已发布安排更新</span>
        </aside>
        <div className="workspace-content">
          <div className="page-heading"><div><p className="eyebrow">CREW WORKSPACE / {String(tabs.indexOf(tab) + 1).padStart(2, '0')}</p><h1>{titles[tab]}</h1>{selected && <p className="caption">{selected.name} · {selected.project_name}</p>}</div>
            {preview ? <span className="preview-label">只读布局预览 · 不代表已登录</span> : <div className="member-actions"><Button onClick={() => setPicker(true)}>{selected ? '选择其他剧组' : '选择或创建剧组'}</Button>{selected && <Button onClick={() => setLeaving(true)}>退出当前剧组</Button>}</div>}
          </div>
          {!preview && detail.error && <><ErrorNotice error={detail.error} /><Button onClick={() => { void detail.refetch() }}>重新读取剧组</Button></>}
          {!preview && crewId && detail.isPending && <p role="status">正在确认剧组访问权限…</p>}
        <section className="module-panel" aria-labelledby="module-title">
          <div className="section-heading"><h2 id="module-title">{tab === '总览' ? '剧组工作台' : `${tab}工作区`}</h2><span className="caption">{selected ? '当前剧组' : preview ? '只读布局' : '请选择剧组'}</span></div>
          {selected && (tab === '成员' || tab === '排期') && <div className="member-actions">
            {selected.permissions.includes('availability.self') && <Button onClick={() => setCalendar('own')}>我的档期</Button>}
            {selected.permissions.includes('availability.read_all') && <Button onClick={() => setCalendar('crew')}>剧组档期</Button>}
          </div>}
          {tab === '成员' && selected ? selected.permissions.includes('member.read')
            ? <MemberDirectory key={selected.id} userId={user!.id} crewId={selected.id} selfMemberId={selected.membership.id} canAuthorize={selected.permissions.includes('member.authorize')} canInvite={selected.permissions.includes('member.invite')} canRemove={selected.permissions.includes('member.remove')} onOpenTask={openTask} onOpenResource={openResource} onAccessChanged={async () => { await detail.refetch() }} />
            : <p className="module-note">当前没有查看成员目录的权限。</p> : <>
          {tab === '资源' && selected ? selected.permissions.includes('resource.read')
            ? <ResourceBoard key={selected.id} crewId={selected.id} userId={user!.id} self={selected.membership} canCreate={selected.membership.role_codes.includes('producer') && selected.permissions.includes('resource.manage')} openResourceId={resourceToOpen} onResourceOpened={() => setResourceToOpen(null)} />
            : <p className="module-note">当前没有查看资源的权限。</p>
            : tab === '总览' && selected ? selected.permissions.some(code => ['task.read', 'task.manage'].includes(code))
            ? <TaskBoard key={selected.id} crewId={selected.id} userId={user!.id} canManage={selected.permissions.includes('task.manage')} openTaskId={taskToOpen} onTaskOpened={() => setTaskToOpen(null)} />
            : <p className="module-note">当前没有查看任务的权限。</p>
            : <div className="empty-state"><span className="empty-mark" aria-hidden="true">+</span><h3>{tab === '排期' && selected ? '拍摄计划尚未接入' : `${tab}模块尚未接入`}</h3><p>{descriptions[tab]}</p><p className="caption">{preview ? '这是只读布局预览，不代表已登录，不读取剧组或任务数据。' : selected ? '成员档期已提供；拍摄计划和资源管理仍在开发，此处不会展示演示业务数据。' : '请先选择或创建剧组，再进入剧组功能。'}</p></div>}
          {tab === '排期' && <div className="module-note">通告单属于排期工作流，不单独占用一级导航。</div>}
          {tab === '成员' && <div className="module-note">个人档期由本人维护，不提供代改其他成员档期的入口。</div>}
          </>}
        </section>
        </div>
      </div>
      <ServiceStatus />
      <footer className="page-footer"><span>SILENCIO SET / LOCAL DEVELOPMENT</span><span>Diane智能辅助将在基础业务完成后接入</span></footer>
    </main>
    {user && !preview && <CrewPicker open={picker} userId={user.id} onClose={() => setPicker(false)} onSelect={id => { setLeaving(false); setTaskToOpen(null); setResourceToOpen(null); setDialog(null); setCalendar(null); setCrewId(id) }} />}
    {calendar && selected && <AvailabilityPanel key={`${selected.id}-${calendar}`} crewId={selected.id} userId={user!.id} selfMemberId={selected.membership.id} mode={calendar} onClose={() => setCalendar(null)} />}
    {leaving && selected && <MemberExitDialog key={selected.id} crewId={selected.id} member={selected.membership} removing={false} onClose={() => setLeaving(false)} onOpenTask={openTask} onOpenResource={openResource}
      onReload={async () => {
        const fresh = await detail.refetch()
        if (fresh.isError) {
          await cache.cancelQueries({ queryKey: ['crews', user?.id, selected.id] })
          cache.removeQueries({ queryKey: ['crews', user?.id, selected.id, 'members'] })
          throw fresh.error
        }
        return fresh.data!.membership
      }} onSaved={async () => {
        const oldId = selected.id
        setCrewId(null)
        await cache.cancelQueries({ queryKey: ['crews', user?.id, oldId] })
        cache.removeQueries({ queryKey: ['crews', user?.id, oldId] })
        await cache.invalidateQueries({ queryKey: ['crews', user?.id, 'list'] })
      }} />}
    {dialog === '通知' && selected && <NotificationCenter key={selected.id} crewId={selected.id} userId={user!.id} onClose={() => setDialog(null)} onOpenTask={openTask} />}
    <Modal title={dialog} open={dialog === '项目资料' || dialog === '通知' && !selected} onCancel={() => setDialog(null)} footer={<Button onClick={() => setDialog(null)}>关闭</Button>}>
      <p>{dialog === '项目资料' ? '这里将提供剧本、场戏与文字分镜入口。当前未接入资料接口。'
        : '请先选择剧组，再查看属于你的剧组通知。'}</p>
    </Modal>
  </>
}
