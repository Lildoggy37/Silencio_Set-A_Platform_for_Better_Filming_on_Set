import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeAll, expect, test, vi } from 'vitest'
import App from '../App'
import { preloadScreens } from '../test-preload'

beforeAll(preloadScreens, 60_000)

const userId = '11111111-1111-4111-8111-111111111111'
const crewId = '22222222-2222-4222-8222-222222222222'
const memberId = '33333333-3333-4333-8333-333333333333'
const otherId = '44444444-4444-4444-8444-444444444444'
const user = { id: userId, email: 'director@example.com', display_name: '导演', profile_completed: true, missing_fields: [], lock_version: 2 }
const member = { id: memberId, display_name: '导演', status: 'active', role_codes: ['admin'], lock_version: 1 }
const other = { ...member, id: otherId, display_name: '摄影同学', role_codes: ['cinematographer'] }
const crew = { id: crewId, name: '学生剧组', project_name: '雨停之前', description: null, created_at: '2026-10-25T00:00:00Z', membership: member, permissions: ['crew.read', 'member.read', 'member.remove'] }
const ok = (data: unknown) => new Response(JSON.stringify({ data }), { status: 200 })
const fail = (code: string, status: number) => new Response(JSON.stringify({ error: { code, message: 'private detail' } }), { status })
const ended = (removing: boolean) => ok({ membership: { ...(removing ? other : member), status: removing ? 'removed' : 'left', role_codes: [], lock_version: 2 }, ended_at: '2026-10-28T01:00:00Z' })

function start(handler: (path: string, options?: RequestInit) => Response | Promise<Response>) {
  const fetcher = vi.fn((path: string, options?: RequestInit) => {
    if (path.endsWith('/users/me')) return Promise.resolve(ok(user))
    if (path.endsWith('/auth/csrf')) return Promise.resolve(ok({ csrf_token: 'c'.repeat(64) }))
    if (path.includes('/health/')) return Promise.resolve(ok({ status: 'alive' }))
    if (path.includes('/resource-responsibilities?')) {
      const target = path.match(/\/members\/([^/]+)\/resource-responsibilities/)![1]
      return Promise.resolve(ok({ member_id: target, items: [], total: 0, blocking_total: 0, restricted_total: 0, page: 1, page_size: 20 }))
    }
    if (path.includes('/exit-responsibilities?')) {
      const target = path.match(/\/members\/([^/]+)\/exit-responsibilities/)![1]
      return Promise.resolve(ok({ member_id: target, member_lock_version: 1, items: [], total: 0, blocking_total: 0, restricted_total: 0, page: 1, page_size: 20 }))
    }
    return Promise.resolve(handler(path, options))
  })
  vi.stubGlobal('fetch', fetcher)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  render(<QueryClientProvider client={client}><App /></QueryClientProvider>)
  return { fetcher, client }
}

async function selectCrew() {
  fireEvent.click(await screen.findByRole('button', { name: '选择或创建剧组' }))
  fireEvent.click(await screen.findByRole('button', { name: '学生剧组 · 雨停之前' }))
  await screen.findByText('学生剧组 · 雨停之前')
}

test('self exit requires explicit confirmation and clears current crew and its cache on success', async () => {
  let left = false
  const { fetcher, client } = start(path => {
    if (path.includes('/crews?')) return ok({ items: left ? [] : [crew], page: 1, page_size: 20, total: left ? 0 : 1 })
    if (path.endsWith('/me/leave')) { left = true; return ended(false) }
    if (path.endsWith(`/crews/${crewId}`)) return left ? fail('NOT_FOUND', 404) : ok(crew)
    throw new Error('Unexpected request')
  })
  await selectCrew()
  fireEvent.click(screen.getByRole('button', { name: '退出当前剧组' }))
  const confirm = await screen.findByRole('button', { name: '确认退出剧组' })
  await waitFor(() => expect(confirm.hasAttribute('disabled')).toBe(false))
  expect(fetcher.mock.calls.some(([path]) => path.endsWith('/me/leave'))).toBe(false)
  fireEvent.click(confirm); fireEvent.click(confirm)
  await screen.findByRole('button', { name: '选择或创建剧组' })
  expect(screen.queryByText('学生剧组 · 雨停之前')).toBeNull()
  expect(fetcher.mock.calls.filter(([path]) => path.endsWith('/me/leave'))).toHaveLength(1)
  await waitFor(() => expect(client.getQueriesData({ queryKey: ['crews', userId, crewId] })).toHaveLength(0))
})

test('last admin rejection leaves confirmation open and preserves crew access', async () => {
  start(path => {
    if (path.includes('/crews?')) return ok({ items: [crew], page: 1, page_size: 20, total: 1 })
    if (path.endsWith('/me/leave')) return fail('LAST_ADMIN_REQUIRED', 409)
    return ok(crew)
  })
  await selectCrew()
  fireEvent.click(screen.getByRole('button', { name: '退出当前剧组' }))
  await waitFor(() => expect(screen.getByRole('button', { name: '确认退出剧组' }).hasAttribute('disabled')).toBe(false))
  fireEvent.click(screen.getByRole('button', { name: '确认退出剧组' }))
  await screen.findByText('剧组至少需要一名有效管理员，请先完成管理员交接。')
  expect(screen.getByRole('button', { name: '确认退出剧组' })).toBeTruthy()
  expect(screen.getByText('学生剧组 · 雨停之前')).toBeTruthy()
})

test('unknown exit result blocks replay until an explicit read and uses the new version', async () => {
  let version = 1
  const { fetcher } = start((path, options) => {
    if (path.includes('/crews?')) return ok({ items: [crew], page: 1, page_size: 20, total: 1 })
    if (path.endsWith('/me/leave')) {
      const submitted = JSON.parse(String(options?.body))
      if (submitted.expected_lock_version === 1) { version = 2; return Promise.reject(new TypeError('lost response')) }
      expect(submitted.expected_lock_version).toBe(2)
      return ok({ membership: { ...member, status: 'left', role_codes: [], lock_version: 3 }, ended_at: '2026-10-28T01:00:00Z' })
    }
    return ok({ ...crew, membership: { ...member, lock_version: version } })
  })
  await selectCrew()
  fireEvent.click(screen.getByRole('button', { name: '退出当前剧组' }))
  await waitFor(() => expect(screen.getByRole('button', { name: '确认退出剧组' }).hasAttribute('disabled')).toBe(false))
  fireEvent.click(screen.getByRole('button', { name: '确认退出剧组' }))
  const reload = await screen.findByRole('button', { name: '读取当前成员状态' })
  expect(screen.getByRole('button', { name: '确认退出剧组' }).hasAttribute('disabled')).toBe(true)
  expect(fetcher.mock.calls.filter(([path]) => path.endsWith('/me/leave'))).toHaveLength(1)
  fireEvent.click(reload)
  await waitFor(() => expect(screen.getByRole('button', { name: '确认退出剧组' }).hasAttribute('disabled')).toBe(false))
  expect(fetcher.mock.calls.filter(([path]) => path.endsWith('/me/leave'))).toHaveLength(1)
  fireEvent.click(screen.getByRole('button', { name: '确认退出剧组' }))
  await screen.findByRole('button', { name: '选择或创建剧组' })
})

test('only another member can be removed and a reason plus confirmation is required', async () => {
  let removed = false
  const { fetcher } = start((path, options) => {
    if (path.includes('/crews?')) return ok({ items: [crew], page: 1, page_size: 20, total: 1 })
    if (path.includes('/members?')) return ok({ items: removed ? [member] : [member, other], page: 1, page_size: 20, total: removed ? 1 : 2 })
    if (path.endsWith(`/${otherId}/remove`)) {
      expect(JSON.parse(String(options?.body))).toEqual({ reason: '工作调整', expected_lock_version: 1 })
      removed = true; return ended(true)
    }
    return ok(crew)
  })
  await selectCrew()
  fireEvent.click(screen.getByRole('button', { name: '成员' }))
  fireEvent.click(await screen.findByRole('button', { name: '移除摄影同学' }))
  expect(screen.queryByRole('button', { name: '移除导演' })).toBeNull()
  const confirm = await screen.findByRole('button', { name: '确认移除成员' })
  expect(confirm.hasAttribute('disabled')).toBe(true)
  expect(fetcher.mock.calls.some(([path]) => path.endsWith('/remove'))).toBe(false)
  fireEvent.change(screen.getByLabelText('移除原因'), { target: { value: ' 工作调整 ' } })
  await waitFor(() => expect(confirm.hasAttribute('disabled')).toBe(false))
  fireEvent.click(confirm)
  const table = screen.getByRole('table')
  await waitFor(() => expect(within(table).queryByRole('cell', { name: '摄影同学' })).toBeNull())
  expect(fetcher.mock.calls.filter(([path]) => path.endsWith('/remove'))).toHaveLength(1)
})
