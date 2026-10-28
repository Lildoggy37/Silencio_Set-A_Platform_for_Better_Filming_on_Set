import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeAll, expect, test, vi } from 'vitest'
import App from '../App'
import { preloadScreens } from '../test-preload'
import type { CalendarInterval, CalendarRange } from '../api/availability'

beforeAll(preloadScreens, 60_000)
const userId = '11111111-1111-4111-8111-111111111111'
const crewId = '22222222-2222-4222-8222-222222222222'
const memberId = '33333333-3333-4333-8333-333333333333'
const epoch = '2026-10-01T00:00:00.123456Z'
const user = { id: userId, email: 'director@example.com', display_name: '摄影', profile_completed: true, missing_fields: [], lock_version: 2 }
const member = { id: memberId, display_name: '摄影', status: 'active', role_codes: [], lock_version: 1 }
const crew = { id: crewId, name: '学生剧组', project_name: '雨停之前', description: null, created_at: '2026-10-25T00:00:00Z', membership: member, permissions: ['crew.read', 'availability.self', 'availability.read_all'] }
const ok = (data: unknown) => new Response(JSON.stringify({ data }), { status: 200 })
const fail = (code: string, status: number) => new Response(JSON.stringify({ error: { code } }), { status })
const local = (value: string) => new Date(Date.parse(value) + 8 * 3_600_000).toISOString().slice(0, 19)
const shifted = (range: CalendarRange, hours: number) => new Date(Date.parse(range.range_start) + hours * 3_600_000).toISOString()
function queryRange(path: string): CalendarRange {
  const query = new URL(path, 'http://localhost').searchParams
  return { range_start: query.get('range_start')!, range_end: query.get('range_end')! }
}
function response(range: CalendarRange, intervals: CalendarInterval[] = [], version = 1) {
  const unregistered = []; let cursor = range.range_start
  for (const item of intervals) { if (Date.parse(item.starts_at) > Date.parse(cursor)) unregistered.push({ starts_at: cursor, ends_at: item.starts_at }); cursor = item.ends_at }
  if (Date.parse(cursor) < Date.parse(range.range_end)) unregistered.push({ starts_at: cursor, ends_at: range.range_end })
  return { ...range, member_id: memberId, display_name: '摄影', membership_joined_at: epoch, lock_version: version, updated_at: null,
    intervals, unregistered, plan_review_status: 'not_implemented' }
}
function start(handler: (path: string, options?: RequestInit) => Response | Promise<Response>) {
  const fetcher = vi.fn((path: string, options?: RequestInit) => {
    if (path.endsWith('/users/me')) return Promise.resolve(ok(user))
    if (path.endsWith('/auth/csrf')) return Promise.resolve(ok({ csrf_token: 'c'.repeat(64) }))
    if (path.includes('/health/')) return Promise.resolve(ok({ status: 'alive' }))
    if (path.includes('/crews?')) return Promise.resolve(ok({ items: [crew], page: 1, page_size: 20, total: 1 }))
    if (path.endsWith(`/crews/${crewId}`)) return Promise.resolve(ok(crew))
    return Promise.resolve(handler(path, options))
  })
  vi.stubGlobal('fetch', fetcher)
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })}><App /></QueryClientProvider>)
  return fetcher
}
async function open(mode: 'own' | 'crew' = 'own') {
  fireEvent.click(await screen.findByRole('button', { name: '选择或创建剧组' }))
  fireEvent.click(await screen.findByRole('button', { name: '学生剧组 · 雨停之前' }))
  await screen.findByText('学生剧组 · 雨停之前')
  fireEvent.click(screen.getByRole('button', { name: '排期' }))
  fireEvent.click(await screen.findByRole('button', { name: mode === 'own' ? '我的档期' : '剧组档期' }))
}

test('unknown time is not free time and a new interval requires explicit review then save', async () => {
  let range: CalendarRange; let calls = 0
  const fetcher = start((path, options) => {
    if (options?.method === 'PUT') {
      calls += 1; const body = JSON.parse(String(options.body))
      expect(body.expected_membership_joined_at).toBe(epoch)
      expect(body).not.toHaveProperty('member_id')
      return ok(response(body, body.intervals, 2))
    }
    range = queryRange(path); return ok(response(range))
  })
  await open(); await screen.findByRole('cell', { name: '未登记（未知）' })
  expect(screen.queryByRole('cell', { name: '可用' })).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: '添加时间段' }))
  fireEvent.change(screen.getByLabelText('开始时间 1'), { target: { value: local(shifted(range!, 2)) } })
  fireEvent.change(screen.getByLabelText('结束时间 1'), { target: { value: local(shifted(range!, 3)) } })
  fireEvent.change(screen.getByLabelText('可用状态 1'), { target: { value: 'unavailable' } })
  fireEvent.click(screen.getByRole('button', { name: '核对档期修改' }))
  await screen.findByRole('heading', { name: '核对档期替换' })
  expect(calls).toBe(0)
  fireEvent.click(screen.getByRole('button', { name: '确认替换本人档期' }))
  await screen.findByText('本人档期已保存。')
  expect(calls).toBe(1)
  expect(fetcher.mock.calls.filter(([, options]) => options?.method === 'PUT')).toHaveLength(1)
  expect(screen.getByRole('cell', { name: '不可用' })).toBeTruthy()
})

test('clearing a window is explicit and submits an empty collection only after confirmation', async () => {
  let writes = 0
  start((path, options) => {
    if (options?.method === 'PUT') { writes += 1; const body = JSON.parse(String(options.body)); expect(body.intervals).toEqual([]); return ok(response(body, [], 2)) }
    const range = queryRange(path)
    return ok(response(range, [{ starts_at: shifted(range, 2), ends_at: shifted(range, 3), availability: 'available' }]))
  })
  await open(); fireEvent.click(await screen.findByRole('button', { name: '移除时间段 1' }))
  fireEvent.click(screen.getByRole('button', { name: '核对档期修改' }))
  await screen.findByText('本次将清空所选范围内的登记。')
  expect(writes).toBe(0)
  fireEvent.click(screen.getByRole('button', { name: '确认替换本人档期' }))
  await screen.findByText('本人档期已保存。')
})

test('a version conflict preserves the draft and requires a read before a separate save', async () => {
  let version = 1; let puts = 0
  start((path, options) => {
    if (options?.method === 'PUT') {
      const body = JSON.parse(String(options.body)); puts += 1
      if (puts === 1) { version = 2; return fail('VERSION_CONFLICT', 409) }
      expect(body.expected_lock_version).toBe(2)
      expect(body.intervals[0].availability).toBe('unavailable')
      return ok(response(body, body.intervals, 3))
    }
    const range = queryRange(path)
    return ok(response(range, [{ starts_at: shifted(range, 2), ends_at: shifted(range, version === 1 ? 3 : 4), availability: 'available' }], version))
  })
  await open(); await screen.findByLabelText('可用状态 1')
  fireEvent.change(screen.getByLabelText('可用状态 1'), { target: { value: 'unavailable' } })
  fireEvent.click(screen.getByRole('button', { name: '核对档期修改' })); fireEvent.click(await screen.findByRole('button', { name: '确认替换本人档期' }))
  fireEvent.click(await screen.findByRole('button', { name: '读取当前档期并保留草稿' }))
  await screen.findByText(/已读取当前登记，草稿仍保留/)
  expect((screen.getByLabelText('可用状态 1') as HTMLSelectElement).value).toBe('unavailable')
  expect(puts).toBe(1)
  fireEvent.click(screen.getByRole('button', { name: '核对档期修改' })); fireEvent.click(await screen.findByRole('button', { name: '确认替换本人档期' }))
  await screen.findByText('本人档期已保存。')
})

test('lost save response cannot produce an automatic second write', async () => {
  let saved: ReturnType<typeof response> | null = null; let puts = 0
  start((path, options) => {
    if (options?.method === 'PUT') { puts += 1; const body = JSON.parse(String(options.body)); saved = response(body, body.intervals, 2); return Promise.reject(new TypeError('reply lost')) }
    const range = queryRange(path)
    return ok(saved ?? response(range, [{ starts_at: shifted(range, 2), ends_at: shifted(range, 3), availability: 'available' }]))
  })
  await open(); await screen.findByLabelText('可用状态 1')
  fireEvent.change(screen.getByLabelText('可用状态 1'), { target: { value: 'unavailable' } })
  fireEvent.click(screen.getByRole('button', { name: '核对档期修改' })); fireEvent.click(await screen.findByRole('button', { name: '确认替换本人档期' }))
  fireEvent.click(await screen.findByRole('button', { name: '读取当前档期并保留草稿' }))
  await screen.findByText(/已读取当前登记，草稿仍保留/)
  await waitFor(() => expect(screen.getByRole('button', { name: '核对档期修改' }).hasAttribute('disabled')).toBe(true))
  expect(puts).toBe(1)
})

test('authorized group view remains read-only and never creates a member edit request', async () => {
  const fetcher = start(path => { const range = queryRange(path); return ok({ items: [response(range)], total: 1, page: 1, page_size: 20 }) })
  await open('crew'); await screen.findByRole('cell', { name: '未登记（未知）' })
  expect(screen.queryByRole('button', { name: '添加时间段' })).toBeNull()
  expect(screen.queryByRole('button', { name: '确认替换本人档期' })).toBeNull()
  expect(screen.getByText('这里仅查看登记，不能代替其他成员修改。')).toBeTruthy()
  expect(fetcher.mock.calls.some(([, options]) => options?.method === 'PUT')).toBe(false)
})
