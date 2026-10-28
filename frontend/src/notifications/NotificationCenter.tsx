import { useRef, useState } from 'react'
import { Button, Modal } from 'antd'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useAuth } from '../auth/AuthProvider'
import { ErrorNotice } from '../auth/AccountScreens'
import { useBusinessSession } from '../crews/useBusinessSession'
import type { NoticeFilter } from '../api/notifications'

const time = (value: string) => new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value))

export function NotificationCenter({ crewId, userId, onClose, onOpenTask }: { crewId: string; userId: string; onClose: () => void; onOpenTask: (id: string) => void }) {
  const { notifications } = useAuth()
  const cache = useQueryClient()
  const [page, setPage] = useState(1)
  const [filter, setFilter] = useState<NoticeFilter>('')
  const [selected, setSelected] = useState<string | null>(null)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [reviewing, setReviewing] = useState(false)
  const lock = useRef(false)
  const prefix = ['crews', userId, crewId, 'notifications']
  const list = useQuery({ queryKey: [...prefix, 'list', page, filter], queryFn: ({ signal }) => notifications.list(crewId, page, filter, signal), enabled: selected === null, retry: false })
  const detail = useQuery({ queryKey: [...prefix, 'detail', selected], queryFn: ({ signal }) => notifications.get(crewId, selected!, signal), enabled: selected !== null, retry: false })
  useBusinessSession(error ?? (selected ? detail.error : list.error))
  const current = detail.isError || selected === null ? undefined : detail.data

  async function mark(action: 'read' | 'acknowledge') {
    if (lock.current || !current || action === 'acknowledge' && !reviewing) return
    lock.current = true; setPending(true); setError(null)
    try {
      const result = action === 'read' ? await notifications.read(crewId, current.id) : await notifications.acknowledge(crewId, current.id, current.object_version)
      cache.setQueryData([...prefix, 'detail', current.id], result)
      setReviewing(false)
      await cache.invalidateQueries({ queryKey: [...prefix, 'list'] })
    } catch (failure) { setError(failure) }
    finally { lock.current = false; setPending(false) }
  }

  return <Modal title="剧组通知" open footer={null} onCancel={onClose} width={700} closable={!pending} keyboard={!pending} mask={{ closable: !pending }}>
    {selected === null ? <>
      <div className="task-filters"><label htmlFor="notification-filter">通知筛选</label><select id="notification-filter" value={filter} onChange={event => { setFilter(event.target.value as NoticeFilter); setPage(1) }}><option value="">全部通知</option><option value="unread">未读</option><option value="needs_acknowledgement">待知悉</option></select><Button loading={list.isFetching} onClick={() => { void list.refetch() }}>刷新通知</Button></div>
      <ErrorNotice error={list.error} />{list.isPending && <p role="status">正在读取通知…</p>}
      {!list.isError && list.data && <>{list.data.items.length === 0 ? <p>当前范围内没有通知。</p> : <ul className="task-list">{list.data.items.map(item => <li key={item.id}><button type="button" onClick={() => { setSelected(item.id); setError(null); setReviewing(false) }}><strong>{item.title} · {item.task_title}</strong><span>{item.read_at ? '已读' : '未读'} · {item.is_current ? '当前版本' : '任务已有更新'} · {time(item.created_at)}</span></button></li>)}</ul>}
        <div className="list-pagination"><Button disabled={page === 1 || list.isFetching} onClick={() => setPage(page - 1)}>上一页通知</Button><span>第 {page} 页 · 共 {list.data.total} 条</span><Button disabled={page * 20 >= list.data.total || list.isFetching} onClick={() => setPage(page + 1)}>下一页通知</Button></div></>}
    </> : <>
      <div className="member-actions"><Button disabled={pending} onClick={() => { setSelected(null); setError(null); setReviewing(false) }}>返回通知列表</Button><Button aria-label="刷新通知详情" loading={detail.isFetching} onClick={() => { setReviewing(false); setError(null); void detail.refetch() }}>刷新通知详情</Button></div>
      <ErrorNotice error={detail.error} />{detail.isPending && <p role="status">正在读取通知详情…</p>}
      {current && <article className="task-detail"><h3>{current.title}</h3><p>{current.task_title}</p><p className="task-text">{current.summary}</p><p className="caption">{time(current.created_at)} · 通知对应任务版本 {current.object_version}</p>
        {!current.is_current && <p className="account-note">任务已有更新。这条通知保留作历史记录，请核对当前任务；历史知悉记录不会转移到新版本。</p>}
        <p>{current.read_at ? `已读于 ${time(current.read_at)}` : '尚未标记已读'}{current.confirmed_at ? ` · 已知悉于 ${time(current.confirmed_at)}` : ''}</p>
        <ErrorNotice error={error} />
        {error != null && <p className="caption">操作结果未确认或请求被拒绝。可先刷新核对；重复标记会保留已有时间，不会代替其他成员操作。</p>}
        <div className="member-actions"><Button disabled={pending} onClick={() => onOpenTask(current.task_id)}>查看当前任务</Button>{!current.read_at && <Button aria-label="标记已读" loading={pending} onClick={() => { void mark('read') }}>标记已读</Button>}
          {current.requires_confirmation && !current.confirmed_at && current.is_current && !reviewing && <Button disabled={pending} onClick={() => setReviewing(true)}>确认知悉</Button>}</div>
        {reviewing && current.is_current && !current.confirmed_at && <div className="account-note"><p>确认你已知悉该版本的任务安排？这不会提交成果或代替任务验收。</p><Button disabled={pending} onClick={() => setReviewing(false)}>返回核对</Button><Button type="primary" aria-label="确认我已知悉" loading={pending} onClick={() => { void mark('acknowledge') }}>确认我已知悉</Button></div>}
      </article>}
    </>}
  </Modal>
}
