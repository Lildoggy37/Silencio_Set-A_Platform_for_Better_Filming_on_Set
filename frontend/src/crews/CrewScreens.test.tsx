import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeAll, expect, test, vi } from 'vitest'
import App from '../App'
import { preloadScreens } from '../test-preload'

beforeAll(preloadScreens, 60_000)

const userId = '11111111-1111-4111-8111-111111111111'
const crewId = '22222222-2222-4222-8222-222222222222'
const memberId = '33333333-3333-4333-8333-333333333333'
const user = { id: userId, email: 'director@example.com', display_name: '导演', profile_completed: true, missing_fields: [], lock_version: 2 }
const member = { id: memberId, display_name: '导演', status: 'active', role_codes: ['admin'], lock_version: 1 }
const crew = { id: crewId, name: '学生电影剧组', project_name: '雨停之前', description: null, created_at: '2026-10-25T00:00:00Z', membership: member, permissions: ['crew.read', 'member.read', 'member.authorize'] }
const ok = (data: unknown, status = 200) => new Response(JSON.stringify({ data, request_id: 'a'.repeat(32) }), { status })
const fail = (code: string, status: number) => new Response(JSON.stringify({ error: { code, message: 'private raw detail' } }), { status })

function start(handler: (path: string, options?: RequestInit) => Response | Promise<Response>) {
  const fetcher = vi.fn((path: string, options?: RequestInit) => {
    if (path.endsWith('/users/me')) return Promise.resolve(ok(user))
    if (path.endsWith('/auth/csrf')) return Promise.resolve(ok({ csrf_token: 'c'.repeat(64) }))
    if (path.includes('/health/')) return Promise.resolve(ok({ status: 'alive' }))
    return Promise.resolve(handler(path, options))
  })
  vi.stubGlobal('fetch', fetcher)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  render(<QueryClientProvider client={client}><App /></QueryClientProvider>)
  return fetcher
}

test('selects a real crew and reads its member directory without showing private email', async () => {
  start(path => {
    if (path.includes('/crews?')) return ok({ items: [crew], page: 1, page_size: 20, total: 1 })
    if (path.includes('/members?')) return ok({ items: [member], page: 1, page_size: 20, total: 1 })
    if (path.endsWith(`/crews/${crewId}`)) return ok(crew)
    throw new Error('Unexpected request')
  })
  fireEvent.click(await screen.findByRole('button', { name: '选择或创建剧组' }))
  fireEvent.click(await screen.findByRole('button', { name: '学生电影剧组 · 雨停之前' }))
  await screen.findByText('学生电影剧组 · 雨停之前')
  fireEvent.click(screen.getByRole('button', { name: '成员' }))
  const table = await screen.findByRole('table')
  expect(await within(table).findByRole('cell', { name: '管理员' })).toBeTruthy()
  expect(within(table).getByRole('cell', { name: '导演' })).toBeTruthy()
  expect(table.textContent).not.toContain('director@example.com')
})

test('creation posts only business fields and waits for a confirmed server result', async () => {
  let created = false
  const fetcher = start((path, options) => {
    if (path.includes('/crews?')) return ok({ items: created ? [crew] : [], page: 1, page_size: 20, total: created ? 1 : 0 })
    if (path === '/api/v1/crews') {
      expect(JSON.parse(String(options?.body))).toEqual({ name: '学生电影剧组', project_name: '雨停之前', description: null })
      expect((options?.headers as Record<string, string>)['X-CSRF-Token']).toBe('c'.repeat(64))
      created = true; return ok(crew, 201)
    }
    if (path.endsWith(`/crews/${crewId}`)) return ok(crew)
    throw new Error('Unexpected request')
  })
  fireEvent.click(await screen.findByRole('button', { name: '选择或创建剧组' }))
  fireEvent.click(await screen.findByRole('button', { name: '创建新剧组' }))
  fireEvent.change(screen.getByLabelText('剧组名称'), { target: { value: ' 学生电影剧组 ' } })
  fireEvent.change(screen.getByLabelText('影片名称'), { target: { value: ' 雨停之前 ' } })
  fireEvent.click(screen.getByRole('button', { name: '确认创建剧组' }))
  await screen.findByText('学生电影剧组 · 雨停之前')
  expect(fetcher.mock.calls.filter(([path]) => path === '/api/v1/crews')).toHaveLength(1)
})

test('unknown create result blocks another submission and offers a list check instead of auto replay', async () => {
  const fetcher = start(path => {
    if (path.includes('/crews?')) return ok({ items: [], page: 1, page_size: 20, total: 0 })
    if (path === '/api/v1/crews') return Promise.reject(new TypeError('response lost'))
    throw new Error('Unexpected request')
  })
  fireEvent.click(await screen.findByRole('button', { name: '选择或创建剧组' }))
  fireEvent.click(await screen.findByRole('button', { name: '创建新剧组' }))
  fireEvent.change(screen.getByLabelText('剧组名称'), { target: { value: '学生电影剧组' } })
  fireEvent.change(screen.getByLabelText('影片名称'), { target: { value: '雨停之前' } })
  const button = screen.getByRole('button', { name: '确认创建剧组' })
  fireEvent.click(button); fireEvent.click(button)
  await screen.findByText('创建结果未确认，请先查看剧组列表，避免重复创建。')
  expect(button.hasAttribute('disabled')).toBe(true)
  expect(fetcher.mock.calls.filter(([path]) => path === '/api/v1/crews')).toHaveLength(1)
  fireEvent.click(screen.getByRole('button', { name: '查看剧组列表' }))
  await screen.findByRole('button', { name: '创建新剧组' })
})

test('denied detail does not render old crew content or enable member reads', async () => {
  const fetcher = start(path => path.includes('/crews?') ? ok({ items: [crew], page: 1, page_size: 20, total: 1 }) : fail('NOT_FOUND', 404))
  fireEvent.click(await screen.findByRole('button', { name: '选择或创建剧组' }))
  fireEvent.click(await screen.findByRole('button', { name: '学生电影剧组 · 雨停之前' }))
  await screen.findByText('剧组不存在或无权访问，请刷新剧组列表。')
  fireEvent.click(screen.getByRole('button', { name: '成员' }))
  await waitFor(() => expect(screen.queryByRole('table')).toBeNull())
  expect(fetcher.mock.calls.some(([path]) => path.includes('/members'))).toBe(false)
  expect(screen.queryByText('学生电影剧组 · 雨停之前')).toBeNull()
})

async function openRoles() {
  fireEvent.click(await screen.findByRole('button', { name: '选择或创建剧组' }))
  fireEvent.click(await screen.findByRole('button', { name: '学生电影剧组 · 雨停之前' }))
  await screen.findByText('学生电影剧组 · 雨停之前')
  fireEvent.click(screen.getByRole('button', { name: '成员' }))
  fireEvent.click(await screen.findByRole('button', { name: '管理导演的岗位' }))
  // rc-component replaces every generated aria ID with the same "test-id" in
  // NODE_ENV=test. Scope to this rendered form instead of testing that mock ID.
  const legend = await screen.findByText('选择岗位')
  const dialog = legend.closest<HTMLElement>('[role="dialog"]')
  if (!dialog) throw new Error('Role editor is not inside a dialog')
  return dialog
}

function roleScenario(write: (body: { role_codes: string[]; expected_lock_version: number }) => Response | Promise<Response>, readMember = () => member, permission = true) {
  return start((path, options) => {
    if (path.endsWith('/roles')) return write(JSON.parse(String(options?.body)))
    if (path.includes('/crews?')) return ok({ items: [crew], page: 1, page_size: 20, total: 1 })
    if (path.includes('/members?')) return ok({ items: [readMember()], page: 1, page_size: 20, total: 1 })
    if (path.endsWith(`/crews/${crewId}`)) return ok({ ...crew, permissions: permission && readMember().role_codes.includes('admin') ? crew.permissions : ['crew.read', 'member.read'] })
    throw new Error('Unexpected request')
  })
}

test('role changes show a diff before any PUT and require explicit confirmation', async () => {
  let current = { ...member }
  const fetcher = roleScenario(body => {
    expect(body).toEqual({ role_codes: ['admin', 'producer'], expected_lock_version: 1 })
    current = { ...member, role_codes: body.role_codes, lock_version: 2 }; return ok(current)
  }, () => current)
  const dialog = await openRoles()
  fireEvent.click(within(dialog).getByRole('checkbox', { name: '制片' }))
  fireEvent.click(within(dialog).getByRole('button', { name: '核对岗位变更' }))
  expect(within(dialog).getByText('拟调整为：管理员 / 制片')).toBeTruthy()
  expect(fetcher.mock.calls.some(([path]) => path.endsWith('/roles'))).toBe(false)
  fireEvent.click(within(dialog).getByRole('button', { name: '确认修改岗位' }))
  const table = await screen.findByRole('table')
  await within(table).findByRole('cell', { name: '管理员 / 制片' })
  expect(fetcher.mock.calls.filter(([path]) => path.endsWith('/roles'))).toHaveLength(1)
})

test('last admin rejection leaves the editor open and never claims the change succeeded', async () => {
  const fetcher = roleScenario(() => fail('LAST_ADMIN_REQUIRED', 409))
  const dialog = await openRoles()
  fireEvent.click(within(dialog).getByRole('checkbox', { name: '管理员' }))
  fireEvent.click(within(dialog).getByRole('button', { name: '核对岗位变更' }))
  expect(within(dialog).getByText(/将取消管理权限/)).toBeTruthy()
  fireEvent.click(within(dialog).getByRole('button', { name: '确认修改岗位' }))
  await within(dialog).findByText('剧组至少需要一名有效管理员，请先完成管理员交接。')
  expect(within(dialog).getByRole('button', { name: '确认修改岗位' })).toBeTruthy()
  expect(fetcher.mock.calls.filter(([path]) => path.endsWith('/roles'))).toHaveLength(1)
})

test('role version conflict keeps selected roles and requires reload and a second confirmation', async () => {
  let current = { ...member }
  const versions: number[] = []
  roleScenario(body => {
    versions.push(body.expected_lock_version)
    if (versions.length === 1) { current = { ...member, role_codes: ['admin', 'director'], lock_version: 2 }; return fail('VERSION_CONFLICT', 409) }
    current = { ...member, role_codes: body.role_codes, lock_version: 3 }; return ok(current)
  }, () => current)
  const dialog = await openRoles()
  fireEvent.click(within(dialog).getByRole('checkbox', { name: '制片' }))
  fireEvent.click(within(dialog).getByRole('button', { name: '核对岗位变更' }))
  fireEvent.click(within(dialog).getByRole('button', { name: '确认修改岗位' }))
  fireEvent.click(await within(dialog).findByRole('button', { name: '读取当前岗位' }))
  await within(dialog).findByText(/已读取当前岗位/)
  expect((within(dialog).getByRole('checkbox', { name: '制片' }) as HTMLInputElement).checked).toBe(true)
  expect((within(dialog).getByRole('checkbox', { name: '导演' }) as HTMLInputElement).checked).toBe(false)
  expect(versions).toEqual([1])
  await waitFor(() => expect(within(dialog).getByRole('button', { name: '核对岗位变更' }).hasAttribute('disabled')).toBe(false))
  fireEvent.click(within(dialog).getByRole('button', { name: '核对岗位变更' }))
  fireEvent.click(within(dialog).getByRole('button', { name: '确认修改岗位' }))
  await waitFor(() => expect(versions).toEqual([1, 2]))
})

test('a lost role response blocks another write until the current state has been read', async () => {
  const fetcher = roleScenario(() => Promise.reject(new TypeError('lost response')))
  const dialog = await openRoles()
  fireEvent.click(within(dialog).getByRole('checkbox', { name: '制片' }))
  fireEvent.click(within(dialog).getByRole('button', { name: '核对岗位变更' }))
  fireEvent.click(within(dialog).getByRole('button', { name: '确认修改岗位' }))
  await within(dialog).findByRole('button', { name: '读取当前岗位' })
  expect(within(dialog).getByRole('button', { name: '核对岗位变更' }).hasAttribute('disabled')).toBe(true)
  expect(fetcher.mock.calls.filter(([path]) => path.endsWith('/roles'))).toHaveLength(1)
})

test('a confirmed self-demotion reloads current permissions and removes management controls', async () => {
  let current = { ...member }
  roleScenario(body => { current = { ...member, role_codes: body.role_codes, lock_version: 2 }; return ok(current) }, () => current)
  const dialog = await openRoles()
  fireEvent.click(within(dialog).getByRole('checkbox', { name: '管理员' }))
  fireEvent.click(within(dialog).getByRole('checkbox', { name: '制片' }))
  fireEvent.click(within(dialog).getByRole('button', { name: '核对岗位变更' }))
  fireEvent.click(within(dialog).getByRole('button', { name: '确认修改岗位' }))
  await waitFor(() => expect(screen.queryByRole('button', { name: '管理导演的岗位' })).toBeNull())
  const table = await screen.findByRole('table')
  await within(table).findByRole('cell', { name: '制片' })
})

test('a reader without organization authority sees members but no role editor', async () => {
  roleScenario(() => { throw new Error('Must not write') }, () => member, false)
  fireEvent.click(await screen.findByRole('button', { name: '选择或创建剧组' }))
  fireEvent.click(await screen.findByRole('button', { name: '学生电影剧组 · 雨停之前' }))
  await screen.findByText('学生电影剧组 · 雨停之前')
  fireEvent.click(screen.getByRole('button', { name: '成员' }))
  const table = await screen.findByRole('table')
  await within(table).findByRole('cell', { name: '管理员' })
  expect(screen.queryByRole('button', { name: '管理导演的岗位' })).toBeNull()
})
