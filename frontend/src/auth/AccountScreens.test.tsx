import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeAll, expect, test, vi } from 'vitest'
import App from '../App'
import { preloadScreens } from '../test-preload'

beforeAll(preloadScreens, 60_000)

const id = '11111111-1111-4111-8111-111111111111'
const baseUser = { id, email: 'director@example.com', display_name: null as string | null, profile_completed: false, missing_fields: ['display_name'], lock_version: 1 }
const ok = (data: unknown, status = 200) => new Response(JSON.stringify({ data, request_id: 'a'.repeat(32) }), { status })
const fail = (code: string, status: number) => new Response(JSON.stringify({ error: { code, message: 'untrusted raw message' }, request_id: 'a'.repeat(32) }), { status })

function start(handler: (path: string, options?: RequestInit) => Response | Promise<Response>) {
  const fetcher = vi.fn((path: string, options?: RequestInit) => Promise.resolve(handler(path, options)))
  vi.stubGlobal('fetch', fetcher)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  render(<QueryClientProvider client={client}><App /></QueryClientProvider>)
  return { client, fetcher }
}

const fill = (label: string, value: string) => fireEvent.change(screen.getByLabelText(label, { exact: true }), { target: { value } })

test('requires nickname before showing any crew workspace and only enters after backend save', async () => {
  let user = { ...baseUser }
  const requests: string[] = []
  start((path, options) => {
    requests.push(path)
    if (path.endsWith('/users/me/profile')) {
      expect(JSON.parse(String(options?.body))).toEqual({ display_name: '导演', expected_lock_version: 1 })
      user = { ...user, display_name: '导演', profile_completed: true, missing_fields: [], lock_version: 2 }
      return ok(user)
    }
    if (path.endsWith('/users/me')) return ok(user)
    if (path.endsWith('/auth/csrf')) return ok({ csrf_token: 'c'.repeat(64) })
    return ok({ status: 'alive' })
  })
  expect(await screen.findByRole('heading', { name: '补充你的昵称' })).toBeTruthy()
  expect(screen.queryByRole('button', { name: '总览' })).toBeNull()
  fill('昵称', '  导演  ')
  fireEvent.click(screen.getByRole('button', { name: '保存昵称并继续' }))
  expect(await screen.findByRole('button', { name: '总览' })).toBeTruthy()
  expect(requests.some(path => path.includes('/crews'))).toBe(false)
})

test('login refreshes CSRF and reconciles current session before entering workspace', async () => {
  let logged = false
  let csrfCalls = 0
  const user = { ...baseUser, display_name: '导演', profile_completed: true, missing_fields: [] }
  start((path, options) => {
    if (path.endsWith('/users/me')) return logged ? ok(user) : fail('SESSION_EXPIRED', 401)
    if (path.endsWith('/auth/csrf')) { csrfCalls++; return ok({ csrf_token: (logged ? 'd' : 'c').repeat(64) }) }
    if (path.endsWith('/auth/login')) { expect(JSON.parse(String(options?.body)).password).toBe('sample password long'); logged = true; return ok(user) }
    return ok({ status: 'alive' })
  })
  await screen.findByRole('heading', { name: '登录片场' })
  fill('邮箱', user.email); fill('密码', 'sample password long')
  fireEvent.click(screen.getByRole('button', { name: '登录' }))
  expect(await screen.findByRole('button', { name: '总览' })).toBeTruthy()
  expect(csrfCalls).toBe(2)
  expect(window.localStorage.length).toBe(0)
  expect(window.sessionStorage.length).toBe(0)
})

test('wrong password remains a failure without exposing raw server text or enabling a workspace', async () => {
  start(path => path.endsWith('/users/me') ? fail('SESSION_EXPIRED', 401) : path.endsWith('/auth/csrf') ? ok({ csrf_token: 'c'.repeat(64) }) : fail('INVALID_CREDENTIALS', 401))
  await screen.findByRole('heading', { name: '登录片场' })
  fill('邮箱', 'director@example.com'); fill('密码', 'wrong password')
  fireEvent.click(screen.getByRole('button', { name: '登录' }))
  expect(await screen.findByText('邮箱或密码错误。')).toBeTruthy()
  expect(screen.queryByText('untrusted raw message')).toBeNull()
  expect(screen.queryByRole('button', { name: '总览' })).toBeNull()
})

test('registration separates intake, verification, password and explicit login, keeping grant out of DOM', async () => {
  let meCalls = 0
  const fetches: { path: string; body?: unknown }[] = []
  start((path, options) => {
    fetches.push({ path, body: options?.body ? JSON.parse(String(options.body)) : undefined })
    if (path.endsWith('/users/me')) { meCalls++; return fail('SESSION_EXPIRED', 401) }
    if (path.endsWith('/auth/csrf')) return ok({ csrf_token: 'c'.repeat(64) })
    if (path.endsWith('/auth/email-verifications')) return ok({ verification_id: id, expires_at: new Date(Date.now() + 300_000).toISOString(), delivery_status: 'pending_dispatch', resend_after_seconds: 60 }, 202)
    if (path.endsWith('/verify')) return ok({ registration_token: 't'.repeat(43), expires_at: new Date(Date.now() + 300_000).toISOString() })
    if (path.endsWith('/auth/register')) return ok({ user_id: id, registered: true }, 201)
    throw new Error('Unexpected request')
  })
  await screen.findByRole('heading', { name: '登录片场' })
  fireEvent.click(screen.getByRole('button', { name: '邮箱验证注册' }))
  fill('注册邮箱', 'director@example.com')
  fireEvent.click(screen.getByRole('button', { name: '申请验证码' }))
  expect(await screen.findByText(/验证邮件已受理，尚不能确认送达/)).toBeTruthy()
  fill('邮箱验证码', '012345')
  fireEvent.click(screen.getByRole('button', { name: '验证并设置密码' }))
  await screen.findByLabelText('设置密码', { exact: true })
  expect(document.body.innerHTML).not.toContain('t'.repeat(43))
  fill('设置密码', 'sample password long'); fill('再次输入密码', 'sample password long')
  fireEvent.click(screen.getByRole('button', { name: '完成注册' }))
  expect(await screen.findByRole('heading', { name: '注册完成' })).toBeTruthy()
  expect(fetches.filter(item => item.path.endsWith('/auth/login'))).toHaveLength(0)
  expect(meCalls).toBe(1)
  expect(window.location.href).not.toContain('registration_token')
  expect(window.localStorage.length + window.sessionStorage.length).toBe(0)
})

test('version conflict preserves draft and requires explicit reload followed by a separate save', async () => {
  let currentReads = 0
  const versions: number[] = []
  start((path, options) => {
    if (path.endsWith('/users/me')) { currentReads++; return ok({ ...baseUser, lock_version: currentReads > 1 ? 2 : 1 }) }
    if (path.endsWith('/auth/csrf')) return ok({ csrf_token: 'c'.repeat(64) })
    if (path.endsWith('/users/me/profile')) {
      const input = JSON.parse(String(options?.body)); versions.push(input.expected_lock_version)
      return input.expected_lock_version === 1 ? fail('VERSION_CONFLICT', 409) : ok({ display_name: input.display_name, profile_completed: true, missing_fields: [], lock_version: 3 })
    }
    return ok({ status: 'alive' })
  })
  await screen.findByRole('heading', { name: '补充你的昵称' })
  fill('昵称', '保留草稿')
  fireEvent.click(screen.getByRole('button', { name: '保存昵称并继续' }))
  fireEvent.click(await screen.findByRole('button', { name: '读取当前资料版本' }))
  await screen.findByText(/已读取当前版本/)
  expect((screen.getByLabelText('昵称', { exact: true }) as HTMLInputElement).value).toBe('保留草稿')
  expect(versions).toEqual([1])
  await waitFor(() => expect(screen.getByRole('button', { name: '保存昵称并继续' }).getAttribute('aria-busy')).toBe('false'))
  fireEvent.click(screen.getByRole('button', { name: '保存昵称并继续' }))
  await screen.findByRole('button', { name: '总览' })
  expect(versions).toEqual([1, 2])
})

test('logout failure hides private workspace without falsely claiming the server session was cleared', async () => {
  start(path => {
    if (path.endsWith('/users/me')) return ok({ ...baseUser, display_name: '导演', profile_completed: true, missing_fields: [] })
    if (path.endsWith('/auth/csrf')) return ok({ csrf_token: 'c'.repeat(64) })
    if (path.endsWith('/auth/logout')) return fail('AUTH_SERVICE_UNAVAILABLE', 503)
    return ok({ status: 'alive' })
  })
  fireEvent.click(await screen.findByRole('button', { name: '个人账号' }))
  fireEvent.click(await screen.findByRole('button', { name: '退出账号' }))
  await screen.findByRole('heading', { name: '登录片场' })
  expect(screen.queryByRole('button', { name: '总览' })).toBeNull()
  expect(screen.queryByText('已退出')).toBeNull()
})

test('an expired session during conflict reload removes profile and private query data', async () => {
  let reads = 0
  const { client } = start(path => {
    if (path.endsWith('/users/me')) return ++reads === 1 ? ok(baseUser) : fail('SESSION_EXPIRED', 401)
    if (path.endsWith('/auth/csrf')) return ok({ csrf_token: 'c'.repeat(64) })
    if (path.endsWith('/users/me/profile')) return fail('VERSION_CONFLICT', 409)
    throw new Error('Unexpected request')
  })
  await screen.findByRole('heading', { name: '补充你的昵称' })
  client.setQueryData(['private-crew'], { secret: 'private-draft' })
  fill('昵称', '待保存草稿')
  fireEvent.click(screen.getByRole('button', { name: '保存昵称并继续' }))
  fireEvent.click(await screen.findByRole('button', { name: '读取当前资料版本' }))
  await screen.findByRole('heading', { name: '登录片场' })
  expect(screen.queryByLabelText('昵称', { exact: true })).toBeNull()
  expect(client.getQueryData(['private-crew'])).toBeUndefined()
})

test('a different account during profile reload hides old data and requires a fresh session check', async () => {
  let reads = 0
  const { client } = start(path => {
    if (path.endsWith('/users/me')) return ok(reads++ === 0 ? baseUser : { ...baseUser, id: '22222222-2222-4222-8222-222222222222' })
    if (path.endsWith('/auth/csrf')) return ok({ csrf_token: 'c'.repeat(64) })
    if (path.endsWith('/users/me/profile')) return fail('VERSION_CONFLICT', 409)
    throw new Error('Unexpected request')
  })
  await screen.findByRole('heading', { name: '补充你的昵称' })
  client.setQueryData(['private-crew'], { secret: 'private-draft' })
  fill('昵称', '旧账号草稿')
  fireEvent.click(screen.getByRole('button', { name: '保存昵称并继续' }))
  fireEvent.click(await screen.findByRole('button', { name: '读取当前资料版本' }))
  await screen.findByRole('heading', { name: '登录片场' })
  expect(await screen.findByText('登录状态已变化，请重新检查会话。')).toBeTruthy()
  expect(client.getQueryData(['private-crew'])).toBeUndefined()
})

test('uncertain registration is never replayed automatically; explicit retry uses the same grant', async () => {
  const tokens: string[] = []
  let loseResponse: (() => void) | undefined
  start((path, options) => {
    if (path.endsWith('/users/me')) return fail('SESSION_EXPIRED', 401)
    if (path.endsWith('/auth/csrf')) return ok({ csrf_token: 'c'.repeat(64) })
    if (path.endsWith('/auth/email-verifications')) return ok({ verification_id: id, expires_at: new Date(Date.now() + 300_000).toISOString(), delivery_status: 'pending_dispatch', resend_after_seconds: 60 }, 202)
    if (path.endsWith('/verify')) return ok({ registration_token: 't'.repeat(43), expires_at: new Date(Date.now() + 300_000).toISOString() })
    if (path.endsWith('/auth/register')) {
      tokens.push(JSON.parse(String(options?.body)).registration_token)
      if (tokens.length === 1) return new Promise((_resolve, reject) => { loseResponse = () => reject(new TypeError('response lost after commit')) })
      return ok({ user_id: id, registered: true })
    }
    throw new Error('Unexpected request')
  })
  await screen.findByRole('heading', { name: '登录片场' })
  fireEvent.click(screen.getByRole('button', { name: '邮箱验证注册' }))
  fill('注册邮箱', 'director@example.com')
  fireEvent.click(screen.getByRole('button', { name: '申请验证码' }))
  await screen.findByLabelText('邮箱验证码', { exact: true })
  fill('邮箱验证码', '012345')
  fireEvent.click(screen.getByRole('button', { name: '验证并设置密码' }))
  await screen.findByLabelText('设置密码', { exact: true })
  fill('设置密码', 'sample password long'); fill('再次输入密码', 'sample password long')
  const button = screen.getByRole('button', { name: '完成注册' })
  fireEvent.click(button); fireEvent.click(button)
  await waitFor(() => expect(tokens).toHaveLength(1))
  loseResponse?.()
  await screen.findByText('连接中断或请求超时，操作结果未确认；未自动重试。')
  expect(tokens).toHaveLength(1)
  expect(screen.queryByRole('heading', { name: '注册完成' })).toBeNull()
  expect(document.body.innerHTML).not.toContain('t'.repeat(43))
  await waitFor(() => expect(button.getAttribute('aria-busy')).toBe('false'))
  fireEvent.click(button)
  await screen.findByRole('heading', { name: '注册完成' })
  expect(tokens).toEqual(['t'.repeat(43), 't'.repeat(43)])
})
