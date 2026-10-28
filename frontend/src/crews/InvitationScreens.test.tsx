import { StrictMode } from 'react'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeAll, expect, test, vi } from 'vitest'
import App from '../App'
import { preloadScreens } from '../test-preload'

beforeAll(preloadScreens, 60_000)

const TOKEN = 't'.repeat(43)
const userId = '11111111-1111-4111-8111-111111111111'
const crewId = '22222222-2222-4222-8222-222222222222'
const memberId = '33333333-3333-4333-8333-333333333333'
const inviteId = '44444444-4444-4444-8444-444444444444'
const user = { id: userId, email: 'director@example.com', display_name: '导演', profile_completed: true, missing_fields: [], lock_version: 1 }
const member = { id: memberId, display_name: '导演', status: 'active', role_codes: [], lock_version: 1 }
const crew = { id: crewId, name: '电影剧组', project_name: '雨停之前', description: null, created_at: '2026-10-27T00:00:00Z', membership: member, permissions: ['crew.read', 'member.read', 'member.invite'] }
const invite = { id: inviteId, email: 'invitee@example.com', status: 'pending', lock_version: 1, created_at: '2026-10-27T00:00:00Z', expires_at: '2026-11-03T00:00:00Z' }
const ok = (data: unknown, status = 200) => new Response(JSON.stringify({ data }), { status })
const fail = (code: string, status: number) => new Response(JSON.stringify({ error: { code, message: 'private raw message' } }), { status })

afterEach(() => { window.history.replaceState(null, '', '/') })

function start(handler: (path: string, options?: RequestInit) => Response | Promise<Response>, current = () => user) {
  const fetcher = vi.fn((path: string, options?: RequestInit) => {
    if (path.endsWith('/users/me')) return Promise.resolve(ok(current()))
    if (path.endsWith('/auth/csrf')) return Promise.resolve(ok({ csrf_token: 'c'.repeat(64) }))
    if (path.includes('/health/')) return Promise.resolve(ok({ status: 'alive' }))
    return Promise.resolve(handler(path, options))
  })
  vi.stubGlobal('fetch', fetcher)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  render(<StrictMode><QueryClientProvider client={client}><App /></QueryClientProvider></StrictMode>)
  return fetcher
}

test('captures and clears a fragment credential under StrictMode without accepting before a click', async () => {
  window.history.replaceState(null, '', `/#invitation=${TOKEN}`)
  const fetcher = start((path, options) => {
    if (path.endsWith('/invitations/accept')) { expect(JSON.parse(String(options?.body))).toEqual({ token: TOKEN }); return ok({ crew_id: crewId, membership: member, accepted: true }) }
    if (path.endsWith(`/crews/${crewId}`)) return ok(crew)
    throw new Error('Unexpected request')
  })
  await screen.findByRole('heading', { name: '接受剧组邀请' })
  expect(window.location.hash).toBe('')
  expect(document.body.innerHTML).not.toContain(TOKEN)
  expect(fetcher.mock.calls.some(([path]) => path.endsWith('/invitations/accept'))).toBe(false)
  fireEvent.click(screen.getByRole('button', { name: '确认加入剧组' }))
  await screen.findByText('电影剧组 · 雨停之前')
  expect(window.localStorage.length + window.sessionStorage.length).toBe(0)
  expect(fetcher.mock.calls.filter(([path]) => path.endsWith('/invitations/accept'))).toHaveLength(1)
})

test('requires nickname completion before offering invitation acceptance', async () => {
  window.history.replaceState(null, '', `/#invitation=${TOKEN}`)
  let current = { ...user, display_name: null as string | null, profile_completed: false, missing_fields: ['display_name'] }
  const fetcher = start(path => {
    if (path.endsWith('/users/me/profile')) { current = { ...user, lock_version: 2 }; return ok(current) }
    throw new Error('Unexpected write')
  }, () => current as typeof user)
  await screen.findByRole('heading', { name: '补充你的昵称' })
  expect(screen.queryByRole('button', { name: '确认加入剧组' })).toBeNull()
  fireEvent.change(screen.getByLabelText('昵称', { exact: true }), { target: { value: '导演' } })
  fireEvent.click(screen.getByRole('button', { name: '保存昵称并继续' }))
  await screen.findByRole('heading', { name: '接受剧组邀请' })
  expect(fetcher.mock.calls.some(([path]) => path.endsWith('/invitations/accept'))).toBe(false)
})

test('an unknown accept result is only retried explicitly using the same credential', async () => {
  window.history.replaceState(null, '', `/#invitation=${TOKEN}`)
  const tokens: string[] = []
  start((path, options) => {
    if (path.endsWith('/invitations/accept')) {
      tokens.push(JSON.parse(String(options?.body)).token)
      if (tokens.length === 1) return Promise.reject(new TypeError('response lost'))
      return ok({ crew_id: crewId, membership: member, accepted: true })
    }
    if (path.endsWith(`/crews/${crewId}`)) return ok(crew)
    throw new Error('Unexpected request')
  })
  fireEvent.click(await screen.findByRole('button', { name: '确认加入剧组' }))
  const retry = await screen.findByRole('button', { name: '重新确认加入结果' })
  expect(tokens).toEqual([TOKEN])
  await waitFor(() => expect(retry.getAttribute('aria-busy')).toBe('false'))
  fireEvent.click(retry)
  await screen.findByText('电影剧组 · 雨停之前')
  expect(tokens).toEqual([TOKEN, TOKEN])
})

test('invalid invitation does not join or display raw server text', async () => {
  window.history.replaceState(null, '', `/#other=1&invitation=${TOKEN}`)
  start(() => fail('INVITATION_INVALID', 400))
  fireEvent.click(await screen.findByRole('button', { name: '确认加入剧组' }))
  await screen.findByText(/邀请无效、已过期、已撤销或与当前账号不匹配/)
  expect(window.location.hash).toBe('')
  expect(screen.queryByText('private raw message')).toBeNull()
  expect(screen.queryByRole('button', { name: '总览' })).toBeNull()
})

async function openManager() {
  fireEvent.click(await screen.findByRole('button', { name: '选择或创建剧组' }))
  fireEvent.click(await screen.findByRole('button', { name: '电影剧组 · 雨停之前' }))
  await screen.findByText('电影剧组 · 雨停之前')
  fireEvent.click(screen.getByRole('button', { name: '成员' }))
  fireEvent.click(await screen.findByRole('button', { name: '邀请成员' }))
  await screen.findByLabelText('受邀邮箱')
}

test('admin invitation shows accepted-not-delivered and revokes only after confirmation', async () => {
  let record = { ...invite }
  const writes: string[] = []
  start((path, options) => {
    if (path.includes('/crews?')) return ok({ items: [crew], page: 1, page_size: 20, total: 1 })
    if (path.endsWith(`/crews/${crewId}`)) return ok(crew)
    if (path.includes('/members?')) return ok({ items: [member], page: 1, page_size: 20, total: 1 })
    if (path.includes('/invitations?')) return ok({ items: writes.length ? [record] : [], page: 1, page_size: 20, total: writes.length ? 1 : 0 })
    if (path.endsWith('/revoke')) { writes.push('revoke'); expect(JSON.parse(String(options?.body))).toEqual({ expected_lock_version: 1 }); record = { ...invite, status: 'revoked', lock_version: 2 }; return ok(record) }
    if (path.endsWith('/invitations')) { writes.push('invite'); expect(JSON.parse(String(options?.body))).toEqual({ email: invite.email }); return ok({ invitation: record, delivery_status: 'pending_dispatch' }, 202) }
    throw new Error('Unexpected request')
  })
  await openManager()
  fireEvent.change(screen.getByLabelText('受邀邮箱'), { target: { value: invite.email } })
  fireEvent.click(screen.getByRole('button', { name: '发送邀请' }))
  await screen.findByText(/邀请邮件已受理，尚不能确认送达/)
  fireEvent.click(await screen.findByRole('button', { name: `撤销对${invite.email}的邀请` }))
  expect(writes).toEqual(['invite'])
  fireEvent.click(screen.getByRole('button', { name: '确认撤销邀请' }))
  await screen.findByText('邀请已撤销。')
  expect(writes).toEqual(['invite', 'revoke'])
})

test('unknown invitation creation blocks another send until the list is checked', async () => {
  const fetcher = start(path => {
    if (path.includes('/crews?')) return ok({ items: [crew], page: 1, page_size: 20, total: 1 })
    if (path.endsWith(`/crews/${crewId}`)) return ok(crew)
    if (path.includes('/members?')) return ok({ items: [member], page: 1, page_size: 20, total: 1 })
    if (path.includes('/invitations?')) return ok({ items: [], page: 1, page_size: 20, total: 0 })
    return Promise.reject(new TypeError('lost response'))
  })
  await openManager()
  fireEvent.change(screen.getByLabelText('受邀邮箱'), { target: { value: invite.email } })
  fireEvent.click(screen.getByRole('button', { name: '发送邀请' }))
  await screen.findByText(/操作结果未确认或邀请已变化/)
  expect(screen.getByRole('button', { name: '发送邀请' }).hasAttribute('disabled')).toBe(true)
  expect(fetcher.mock.calls.filter(([path]) => path.endsWith('/invitations'))).toHaveLength(1)
})
