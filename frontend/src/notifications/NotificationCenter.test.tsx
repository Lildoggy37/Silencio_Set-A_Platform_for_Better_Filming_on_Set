import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeAll, expect, test, vi } from 'vitest'
import App from '../App'
import { preloadScreens } from '../test-preload'

beforeAll(preloadScreens, 60_000)
const userId = '11111111-1111-4111-8111-111111111111'
const crewId = '22222222-2222-4222-8222-222222222222'
const memberId = '33333333-3333-4333-8333-333333333333'
const taskId = '44444444-4444-4444-8444-444444444444'
const noticeId = '55555555-5555-4555-8555-555555555555'
const user = { id: userId, email: 'director@example.com', display_name: '导演', profile_completed: true, missing_fields: [], lock_version: 2 }
const member = { id: memberId, display_name: '导演', status: 'active', role_codes: ['admin', 'director'], lock_version: 1 }
const crew = { id: crewId, name: '学生剧组', project_name: '雨停之前', description: null, created_at: '2026-10-25T00:00:00Z', membership: member, permissions: ['crew.read', 'member.read', 'task.read', 'task.manage'] }
const notice = { id: noticeId, event_type: 'task.create', title: '收到新任务', summary: '请核对任务安排', task_id: taskId, task_title: '走廊分镜', created_at: '2026-10-28T00:00:00Z',
  object_version: 1, current_object_version: 1, is_current: true, requires_confirmation: true, read_at: null as string | null, confirmed_at: null as string | null }
const ok = (data: unknown) => new Response(JSON.stringify({ data }), { status: 200 })
const fail = (code: string, status: number) => new Response(JSON.stringify({ error: { code } }), { status })

function start(handler: (path: string, options?: RequestInit) => Response | Promise<Response>) {
  const fetcher = vi.fn((path: string, options?: RequestInit) => {
    if (path.endsWith('/users/me')) return Promise.resolve(ok(user))
    if (path.endsWith('/auth/csrf')) return Promise.resolve(ok({ csrf_token: 'c'.repeat(64) }))
    if (path.includes('/health/')) return Promise.resolve(ok({ status: 'alive' }))
    if (path.includes('/resource-responsibilities?')) return Promise.resolve(ok({ member_id: memberId, items: [], total: 0, blocking_total: 0, restricted_total: 0, page: 1, page_size: 20 }))
    if (path.includes('/crews?')) return Promise.resolve(ok({ items: [crew], page: 1, page_size: 20, total: 1 }))
    if (path.endsWith(`/crews/${crewId}`)) return Promise.resolve(ok(crew))
    if (path.includes('/tasks?')) return Promise.resolve(ok({ items: [], total: 0, page: 1, page_size: 20 }))
    return Promise.resolve(handler(path, options))
  })
  vi.stubGlobal('fetch', fetcher)
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })}><App /></QueryClientProvider>)
  return fetcher
}
async function selectCrew() {
  fireEvent.click(await screen.findByRole('button', { name: '选择或创建剧组' }))
  fireEvent.click(await screen.findByRole('button', { name: '学生剧组 · 雨停之前' }))
  await screen.findByText('学生剧组 · 雨停之前')
}
async function openNotice() {
  await selectCrew(); fireEvent.click(screen.getByRole('button', { name: '通知' }))
  fireEvent.click(await screen.findByRole('button', { name: /收到新任务 · 走廊分镜/ }))
  await screen.findByRole('button', { name: '标记已读' })
}

test('opening a notification does not confirm it; read and explicit acknowledgement stay separate', async () => {
  let item = { ...notice }
  const fetcher = start((path, options) => {
    if (path.includes('/notifications?')) return ok({ items: [item], total: 1, page: 1, page_size: 20 })
    if (path.endsWith('/read')) { item = { ...item, read_at: '2026-10-28T01:00:00Z' }; return ok(item) }
    if (path.endsWith('/acknowledge')) {
      expect(JSON.parse(String(options?.body))).toEqual({ object_version: 1 })
      item = { ...item, confirmed_at: '2026-10-28T01:00:00Z' }; return ok(item)
    }
    return ok(item)
  })
  await openNotice()
  expect(fetcher.mock.calls.some(([path]) => path.endsWith('/read') || path.endsWith('/acknowledge'))).toBe(false)
  fireEvent.click(screen.getByRole('button', { name: '标记已读' }))
  await waitFor(() => expect(screen.queryByRole('button', { name: '标记已读' })).toBeNull())
  fireEvent.click(screen.getByRole('button', { name: '确认知悉' }))
  expect(fetcher.mock.calls.some(([path]) => path.endsWith('/acknowledge'))).toBe(false)
  fireEvent.click(screen.getByRole('button', { name: '确认我已知悉' }))
  await waitFor(() => expect(screen.queryByRole('button', { name: '确认知悉' })).toBeNull())
  expect(fetcher.mock.calls.filter(([path]) => path.endsWith('/acknowledge'))).toHaveLength(1)
})

test('outdated notification stays readable but cannot confirm a superseded task version', async () => {
  const old = { ...notice, is_current: false, current_object_version: 2 }
  const fetcher = start(path => path.includes('/notifications?') ? ok({ items: [old], total: 1, page: 1, page_size: 20 }) : ok(old))
  await openNotice()
  expect(screen.queryByRole('button', { name: '确认知悉' })).toBeNull()
  expect(screen.getByText(/历史知悉记录不会转移到新版本/)).toBeTruthy()
  expect(fetcher.mock.calls.some(([path]) => path.endsWith('/acknowledge'))).toBe(false)
})

test('notification refresh denial removes private notice content', async () => {
  let denied = false
  start(path => path.includes('/notifications?') ? ok({ items: [notice], total: 1, page: 1, page_size: 20 }) : denied ? fail('NOT_FOUND', 404) : ok(notice))
  await openNotice()
  denied = true
  fireEvent.click(screen.getByRole('button', { name: '刷新通知详情' }))
  await screen.findByText('剧组不存在或无权访问，请刷新剧组列表。')
  expect(screen.queryByText('请核对任务安排')).toBeNull()
  expect(screen.queryByRole('button', { name: '查看当前任务' })).toBeNull()
})

test('exit preview blocks submission and links only visible responsibilities to the actual task', async () => {
  const fetcher = start(path => {
    if (path.includes('/exit-responsibilities?')) return ok({ member_id: memberId, member_lock_version: 1, items: [{ id: taskId, title: '走廊分镜', status: 'in_progress', due_at: '2099-01-01T00:00:00Z', lock_version: 2, is_owner: true, is_reviewer: false }],
      total: 1, blocking_total: 2, restricted_total: 1, page: 1, page_size: 20 })
    if (path.endsWith(`/tasks/${taskId}`)) return ok({ task: { id: taskId, crew_id: crewId, title: '走廊分镜', description: null,
      owner_member_id: memberId, reviewer_member_id: memberId, created_by_member_id: memberId, collaborator_member_ids: [], due_at: '2099-01-01T00:00:00Z', status: 'in_progress', cancel_reason: null, lock_version: 2,
      created_at: '2026-10-28T00:00:00Z', updated_at: '2026-10-28T00:00:00Z', allowed_actions: ['submit'] }, member_names: { [memberId]: '导演' }, current_submission: null, submission_count: 0 })
    throw new Error('Unexpected request')
  })
  await selectCrew(); fireEvent.click(screen.getByRole('button', { name: '退出当前剧组' }))
  const open = await screen.findByRole('button', { name: '处理任务：走廊分镜' })
  expect(screen.getByRole('button', { name: '确认退出剧组' }).hasAttribute('disabled')).toBe(true)
  expect(screen.getByText(/1 项任务超出你的查看范围/)).toBeTruthy()
  expect(fetcher.mock.calls.some(([path]) => path.endsWith('/me/leave'))).toBe(false)
  fireEvent.click(open)
  await screen.findByRole('button', { name: '提交成果' })
})

test('failed responsibility check cannot be treated as no remaining work', async () => {
  start(path => path.includes('/exit-responsibilities?') ? fail('SERVICE_UNAVAILABLE', 503) : ok({}))
  await selectCrew(); fireEvent.click(screen.getByRole('button', { name: '退出当前剧组' }))
  await screen.findByText('API暂不可用，请检查本地服务是否已启动。')
  expect(screen.getByRole('button', { name: '确认退出剧组' }).hasAttribute('disabled')).toBe(true)
})
