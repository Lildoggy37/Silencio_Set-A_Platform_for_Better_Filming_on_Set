import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeAll, expect, test, vi } from 'vitest'
import App from '../App'
import { preloadScreens } from '../test-preload'

beforeAll(preloadScreens, 60_000)
const userId = '11111111-1111-4111-8111-111111111111'
const crewId = '22222222-2222-4222-8222-222222222222'
const memberId = '33333333-3333-4333-8333-333333333333'
const resourceId = '44444444-4444-4444-8444-444444444444'
const reservationId = '55555555-5555-4555-8555-555555555555'
const user = { id: userId, email: 'producer@example.com', display_name: '制片', profile_completed: true, missing_fields: [], lock_version: 2 }
const member = { id: memberId, display_name: '制片', status: 'active', role_codes: ['admin', 'producer'], lock_version: 1 }
const crew = { id: crewId, name: '学生剧组', project_name: '雨停之前', description: null, created_at: '2026-10-25T00:00:00Z', membership: member,
  permissions: ['crew.read', 'member.read', 'resource.read', 'resource.manage', 'member.authorize', 'reservation.apply'] }
const resource = { id: resourceId, crew_id: crewId, name: '摄影机', resource_type: 'equipment', unit: '台', total_quantity: 2, status: 'available', description: null,
  manager_member_id: memberId, manager_display_name: '制片', manager_lock_version: 1, review_ready: false, lock_version: 1, created_at: '2026-10-28T00:00:00Z', updated_at: '2026-10-28T00:00:00Z', allowed_actions: ['edit', 'authorize', 'apply'] }
const reservation = { id: reservationId, crew_id: crewId, resource_id: resourceId, applicant_member_id: memberId, starts_at: '2099-01-01T09:00:00Z', ends_at: '2099-01-01T10:00:00Z', quantity: 1,
  status: 'pending', reviewed_by: null, reviewed_at: null, decision_note: null, cancelled_by: null, cancelled_at: null, cancel_reason: null, lock_version: 1,
  created_at: '2026-10-28T00:00:00Z', updated_at: '2026-10-28T00:00:00Z', allowed_actions: ['accept', 'reject', 'cancel'], dependency_check_status: 'not_implemented' }
const ok = (data: unknown, status = 200) => new Response(JSON.stringify({ data }), { status })
const page = (items: unknown[]) => ({ items, total: items.length, page: 1, page_size: 20 })

function start(handler: (path: string, options?: RequestInit) => Response | Promise<Response> | undefined = () => undefined) {
  const fetcher = vi.fn((path: string, options?: RequestInit) => {
    if (path.endsWith('/users/me')) return Promise.resolve(ok(user))
    if (path.endsWith('/auth/csrf')) return Promise.resolve(ok({ csrf_token: 'c'.repeat(64) }))
    if (path.includes('/health/')) return Promise.resolve(ok({ status: 'alive' }))
    const result = handler(path, options)
    if (result !== undefined) return Promise.resolve(result)
    if (path.includes('/crews?')) return Promise.resolve(ok(page([crew])))
    if (path.endsWith(`/crews/${crewId}`)) return Promise.resolve(ok(crew))
    if (path.includes('/members?')) return Promise.resolve(ok(page([member])))
    if (path.includes('/resource-reservations?')) return Promise.resolve(ok(page([reservation])))
    if (path.includes('/resources?')) return Promise.resolve(ok(page([resource])))
    if (path.endsWith(`/resources/${resourceId}`)) return Promise.resolve(ok(resource))
    if (path.includes('/exit-responsibilities?')) return Promise.resolve(ok({ ...page([]), member_id: memberId, member_lock_version: 1, blocking_total: 0, restricted_total: 0 }))
    if (path.includes('/resource-responsibilities?')) return Promise.resolve(ok({ ...page([]), member_id: memberId, blocking_total: 0, restricted_total: 0 }))
    throw new Error(`Unexpected request: ${path}`)
  })
  vi.stubGlobal('fetch', fetcher)
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })}><App /></QueryClientProvider>)
  return fetcher
}
async function open() {
  fireEvent.click(await screen.findByRole('button', { name: '选择或创建剧组' }))
  fireEvent.click(await screen.findByRole('button', { name: '学生剧组 · 雨停之前' }))
  await screen.findByText('学生剧组 · 雨停之前')
  fireEvent.click(screen.getByRole('button', { name: '资源' }))
  await screen.findByRole('button', { name: '查看资源摄影机' })
}
async function openDetail() { await open(); fireEvent.click(screen.getByRole('button', { name: '查看资源摄影机' })); await screen.findByRole('button', { name: '查询余量' }) }
async function createDraft() {
  await open(); fireEvent.click(screen.getByRole('button', { name: '登记资源' }))
  fireEvent.change(await screen.findByLabelText('资源名称'), { target: { value: '收音杆' } })
  await waitFor(() => expect(screen.getByRole('button', { name: '核对本次操作' }).hasAttribute('disabled')).toBe(false))
  fireEvent.click(screen.getByRole('button', { name: '核对本次操作' }))
  await screen.findByText('确认登记资源？')
}

test('resource registration requires review and explicit confirmation without granting review authority', async () => {
  let writes = 0
  const fetcher = start((path, options) => {
    if (path.endsWith('/resources') && options?.method === 'POST') {
      writes += 1; const body = JSON.parse(String(options.body))
      expect(body).toEqual({ name: '收音杆', resource_type: 'equipment', unit: '件', total_quantity: 1, description: null, manager_member_id: memberId })
      return ok({ ...resource, ...body, id: '66666666-6666-4666-8666-666666666666', allowed_actions: ['edit', 'apply'], review_ready: false }, 201)
    }
  })
  await createDraft(); expect(writes).toBe(0)
  const confirm = screen.getByRole('button', { name: '确认提交' })
  fireEvent.click(confirm); fireEvent.click(confirm)
  await waitFor(() => expect(screen.queryByLabelText('资源名称')).toBeNull())
  expect(writes).toBe(1)
  expect(fetcher.mock.calls.filter(([, options]) => options?.method === 'PUT')).toHaveLength(0)
})

test('an unknown create result freezes its payload and retries only the original idempotency key', async () => {
  let writes = 0
  const fetcher = start((path, options) => {
    if (path.endsWith('/resources') && options?.method === 'POST') {
      writes += 1
      if (writes === 1) return Promise.reject(new TypeError('lost response'))
      return ok({ ...resource, ...JSON.parse(String(options.body)), allowed_actions: [] }, 201)
    }
  })
  await createDraft(); fireEvent.click(screen.getByRole('button', { name: '确认提交' }))
  const retry = await screen.findByRole('button', { name: '用原请求重试' })
  expect(screen.queryByLabelText('资源名称')).toBeNull()
  expect(screen.getByRole('button', { name: '返回修改' }).hasAttribute('disabled')).toBe(true)
  expect(writes).toBe(1)
  fireEvent.click(retry)
  await waitFor(() => expect(screen.queryByRole('button', { name: '用原请求重试' })).toBeNull())
  const requests = fetcher.mock.calls.filter(([, options]) => options?.method === 'POST')
  expect(requests).toHaveLength(2); expect(requests[0][1]?.body).toBe(requests[1][1]?.body)
  expect(new Headers(requests[0][1]?.headers).get('Idempotency-Key')).toBe(new Headers(requests[1][1]?.headers).get('Idempotency-Key'))
})

test('admin authorization starts unchecked and explicitly confirms the global capability impact', async () => {
  let writes = 0
  start((path, options) => {
    if (path.endsWith('/authorization') && options?.method === 'PUT') {
      writes += 1
      expect(JSON.parse(String(options.body))).toEqual({ expected_lock_version: 1, manager_member_id: memberId, expected_manager_lock_version: 1, grant_review_permission: true })
      return ok({ ...resource, lock_version: 2, manager_lock_version: 2, review_ready: true })
    }
  })
  await openDetail(); fireEvent.click(screen.getByRole('button', { name: '负责人及审批授权' }))
  const grant = await screen.findByRole('checkbox', { name: '明确授予该成员预约审批能力' })
  expect((grant as HTMLInputElement).checked).toBe(false)
  fireEvent.click(grant)
  await waitFor(() => expect(screen.getByRole('button', { name: '核对本次操作' }).hasAttribute('disabled')).toBe(false))
  fireEvent.click(screen.getByRole('button', { name: '核对本次操作' }))
  await screen.findByText('将明确授予审批能力，并指定本资源审批范围。')
  expect(writes).toBe(0); fireEvent.click(screen.getByRole('button', { name: '确认提交' }))
  await waitFor(() => expect(writes).toBe(1))
})

test('approval uses the current booking version and does not run on opening the form', async () => {
  let writes = 0
  start((path, options) => {
    if (path.endsWith('/decisions')) {
      writes += 1; expect(JSON.parse(String(options?.body))).toEqual({ decision: 'accept', decision_note: null, expected_lock_version: 1 })
      return ok({ ...reservation, status: 'confirmed', reviewed_by: memberId, reviewed_at: '2026-10-28T01:00:00Z', lock_version: 2, allowed_actions: ['cancel'] })
    }
  })
  await openDetail(); fireEvent.click(await screen.findByRole('button', { name: '批准预约' }))
  expect(writes).toBe(0); fireEvent.click(screen.getByRole('button', { name: '核对本次操作' }))
  fireEvent.click(await screen.findByRole('button', { name: '确认提交' }))
  await waitFor(() => expect(writes).toBe(1))
})

test('capacity shows only approved aggregate use and labels physical inventory as unconnected', async () => {
  const fetcher = start(path => {
    if (path.includes('/availability?')) {
      const values = new URL(path, 'http://local').searchParams
      return ok({ resource_id: resourceId, resource_lock_version: 1, range_start: values.get('range_start'), range_end: values.get('range_end'),
        total_quantity: 2, peak_confirmed_quantity: 1, minimum_remaining_quantity: 1, confirmed_reservation_count: 2, physical_inventory_status: 'not_implemented' })
    }
  })
  await openDetail(); fireEvent.click(screen.getByRole('button', { name: '查询余量' }))
  await screen.findByText(/同时占用峰值 1 台，最少余量 1 台。尚未包含实物借还情况/)
  expect(fetcher.mock.calls.filter(([, options]) => options?.method && options.method !== 'GET')).toHaveLength(0)
})

test('read-only viewers see no create, edit, authorize or approval controls', async () => {
  start(path => {
    if (path.endsWith(`/crews/${crewId}`)) return ok({ ...crew, permissions: ['crew.read', 'resource.read'] })
    if (path.endsWith(`/resources/${resourceId}`)) return ok({ ...resource, allowed_actions: [] })
    if (path.includes('/resource-reservations?')) return ok(page([{ ...reservation, allowed_actions: [] }]))
  })
  await openDetail(); await screen.findByText('待审批 · 1 台')
  for (const name of ['登记资源', '编辑资源', '负责人及审批授权', '申请预约', '批准预约', '拒绝预约', '取消预约']) expect(screen.queryByRole('button', { name })).toBeNull()
})

test('resource responsibilities block membership exit and link to the resource detail', async () => {
  const fetcher = start(path => {
    if (path.includes('/resource-responsibilities?')) return ok({ ...page([{ kind: 'resource', id: resourceId, resource_id: resourceId, title: '摄影机', status: 'available', lock_version: 1, ends_at: null }]), member_id: memberId, blocking_total: 1, restricted_total: 0 })
  })
  await open(); fireEvent.click(screen.getByRole('button', { name: '退出当前剧组' }))
  const section = await screen.findByRole('region', { name: '资源交接检查' })
  const link = await within(section).findByRole('button', { name: '处理资源：摄影机' })
  expect(screen.getByRole('button', { name: '确认退出剧组' }).hasAttribute('disabled')).toBe(true)
  fireEvent.click(link)
  await screen.findByRole('button', { name: '查询余量' })
  expect(fetcher.mock.calls.filter(([path]) => path.endsWith('/me/leave'))).toHaveLength(0)
})

test('a failed resource responsibility check cannot be treated as zero blockers', async () => {
  const fetcher = start(path => {
    if (path.includes('/resource-responsibilities?')) return new Response(JSON.stringify({ error: { code: 'SERVICE_UNAVAILABLE' } }), { status: 503 })
  })
  await open(); fireEvent.click(screen.getByRole('button', { name: '退出当前剧组' }))
  await screen.findByText('API暂不可用，请检查本地服务是否已启动。')
  expect(screen.getByRole('button', { name: '确认退出剧组' }).hasAttribute('disabled')).toBe(true)
  expect(fetcher.mock.calls.filter(([path]) => path.endsWith('/me/leave'))).toHaveLength(0)
})

test('an unconfirmed edit preserves input and requires an explicit fresh version before resubmission', async () => {
  let writes = 0, currentVersion = 1
  const fetcher = start((path, options) => {
    if (path.endsWith(`/resources/${resourceId}`)) {
      if (options?.method === 'PATCH') {
        writes += 1; const body = JSON.parse(String(options.body))
        if (writes === 1) { expect(body.expected_lock_version).toBe(1); currentVersion = 2; return Promise.reject(new TypeError('lost response')) }
        expect(body.expected_lock_version).toBe(2)
        return ok({ ...resource, ...body, lock_version: 3 })
      }
      return ok({ ...resource, lock_version: currentVersion })
    }
  })
  await openDetail(); fireEvent.click(screen.getByRole('button', { name: '编辑资源' }))
  fireEvent.change(await screen.findByLabelText('资源名称'), { target: { value: '备用摄影机' } })
  fireEvent.click(screen.getByRole('button', { name: '核对本次操作' })); fireEvent.click(screen.getByRole('button', { name: '确认提交' }))
  const reload = await screen.findByRole('button', { name: '重新读取并保留草稿' })
  expect((screen.getByLabelText('资源名称') as HTMLInputElement).value).toBe('备用摄影机')
  expect(screen.getByRole('button', { name: '核对本次操作' }).hasAttribute('disabled')).toBe(true)
  expect(writes).toBe(1); fireEvent.click(reload)
  await waitFor(() => expect(screen.getByRole('button', { name: '核对本次操作' }).hasAttribute('disabled')).toBe(false))
  expect(writes).toBe(1); fireEvent.click(screen.getByRole('button', { name: '核对本次操作' })); fireEvent.click(screen.getByRole('button', { name: '确认提交' }))
  await waitFor(() => expect(writes).toBe(2))
  expect(fetcher.mock.calls.filter(([, options]) => options?.method === 'PATCH')).toHaveLength(2)
})
