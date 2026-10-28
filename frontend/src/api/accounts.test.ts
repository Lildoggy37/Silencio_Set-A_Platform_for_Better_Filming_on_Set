import { expect, test, vi } from 'vitest'
import { AccountError, createAccountClient, trimName, validName, validNewPassword } from './accounts'

const id = '11111111-1111-4111-8111-111111111111'
const user = { id, email: 'director@example.com', display_name: '导演', profile_completed: true, missing_fields: [], lock_version: 2 }
const ok = (data: unknown, status = 200) => new Response(JSON.stringify({ data, request_id: 'a'.repeat(32) }), { status })
const failure = (code: string, status: number, headers?: HeadersInit) => new Response(JSON.stringify({ error: { code, message: 'private-server-detail' }, request_id: 'a'.repeat(32) }), { status, headers })

test('coalesces CSRF requests and sends secrets only in same-origin JSON bodies', async () => {
  const fetcher = vi.fn().mockResolvedValueOnce(ok({ csrf_token: 'c'.repeat(64) })).mockResolvedValue(ok(user))
  vi.stubGlobal('fetch', fetcher)
  const api = createAccountClient()
  await Promise.all([api.prepareCsrf(), api.prepareCsrf()])
  await api.login('director@example.com', 'private-password-example')
  expect(fetcher).toHaveBeenCalledTimes(2)
  const [url, options] = fetcher.mock.calls[1]
  expect(url).toBe('/api/v1/auth/login')
  expect(url).not.toContain('private-password')
  expect(options.credentials).toBe('same-origin')
  expect(options.headers['X-CSRF-Token']).toBe('c'.repeat(64))
  expect(options.body).toContain('private-password-example')
})

test('login invalidates old CSRF and the next read fetches the rotated token', async () => {
  const fetcher = vi.fn().mockResolvedValueOnce(ok({ csrf_token: 'c'.repeat(64) })).mockResolvedValueOnce(ok(user)).mockResolvedValueOnce(ok({ csrf_token: 'd'.repeat(64) }))
  vi.stubGlobal('fetch', fetcher)
  const api = createAccountClient()
  await api.login(user.email, 'sample password long')
  expect(await api.prepareCsrf()).toBe('d'.repeat(64))
  expect(fetcher).toHaveBeenCalledTimes(3)
})

test('CSRF failure is not transparently retried and old token is discarded', async () => {
  const fetcher = vi.fn().mockResolvedValueOnce(ok({ csrf_token: 'c'.repeat(64) })).mockResolvedValueOnce(failure('CSRF_FAILED', 403)).mockResolvedValueOnce(ok({ csrf_token: 'd'.repeat(64) }))
  vi.stubGlobal('fetch', fetcher)
  const api = createAccountClient()
  await expect(api.saveProfile('导演', 1)).rejects.toMatchObject({ code: 'CSRF_FAILED' })
  expect(fetcher).toHaveBeenCalledTimes(2)
  expect(await api.prepareCsrf()).toBe('d'.repeat(64))
})

test('network loss on registration does not replay a potentially committed mutation', async () => {
  const fetcher = vi.fn().mockResolvedValueOnce(ok({ csrf_token: 'c'.repeat(64) })).mockRejectedValueOnce(new TypeError('private-network-detail'))
  vi.stubGlobal('fetch', fetcher)
  const api = createAccountClient()
  await expect(api.register('t'.repeat(43), 'sample password long')).rejects.toMatchObject({ code: 'NETWORK_UNCONFIRMED' })
  expect(fetcher).toHaveBeenCalledTimes(2)
})

test('unknown backend messages and arbitrary request IDs never become UI errors', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ error: { code: 'private-code', message: 'private-secret' }, request_id: 'private-secret' }), { status: 503 })))
  try { await createAccountClient().me(); throw new Error('expected failure') }
  catch (error) { expect(error).toBeInstanceOf(AccountError); expect(String(error)).not.toContain('private'); expect((error as AccountError).requestId).toBeUndefined() }
})

test('a session-expired response means anonymous, not a fake successful profile', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(failure('SESSION_EXPIRED', 401)))
  expect(await createAccountClient().me()).toBeNull()
})

test('user parser only keeps safe fields and validates completion consistency', async () => {
  const fetcher = vi.fn().mockResolvedValueOnce(ok({ ...user, password_hash: 'private-hash' })).mockResolvedValueOnce(ok({ ...user, display_name: null }))
  vi.stubGlobal('fetch', fetcher)
  const api = createAccountClient()
  expect(await api.me()).toEqual(user)
  await expect(api.me()).rejects.toMatchObject({ code: 'INVALID_RESPONSE' })
})

test('202 is only accepted as verification intake, not as a saved profile', async () => {
  const fetcher = vi.fn().mockResolvedValueOnce(ok({ csrf_token: 'c'.repeat(64) })).mockResolvedValueOnce(ok(user, 202))
  vi.stubGlobal('fetch', fetcher)
  await expect(createAccountClient().saveProfile('导演', 1)).rejects.toMatchObject({ code: 'INVALID_RESPONSE' })
})

test('verification intake validates the backend resend window and never claims delivery', async () => {
  const value = { verification_id: id, expires_at: new Date(Date.now() + 300_000).toISOString(), delivery_status: 'pending_dispatch', resend_after_seconds: 45 }
  vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(ok({ csrf_token: 'c'.repeat(64) })).mockResolvedValueOnce(ok(value, 202)))
  expect(await createAccountClient().requestVerification(user.email)).toEqual({ verification_id: id, expires_at: value.expires_at, resend_after_seconds: 45 })
})

test('rate limit exposes a bounded retry-after value without echoing response text', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(ok({ csrf_token: 'c'.repeat(64) })).mockResolvedValueOnce(failure('RATE_LIMITED', 429, { 'Retry-After': '60' })))
  await expect(createAccountClient().requestVerification(user.email)).rejects.toMatchObject({ retryAfter: 60, code: 'RATE_LIMITED' })
})

test('passwords and profile names count Unicode code points and preserve password spaces', () => {
  expect(validNewPassword('🎬'.repeat(15))).toBe(true)
  expect(validNewPassword('🎬'.repeat(128))).toBe(true)
  expect(validNewPassword('a'.repeat(14))).toBe(false)
  expect(validNewPassword('a'.repeat(129))).toBe(false)
  expect(validName('🎬'.repeat(30))).toBe(true)
  expect(validName('🎬'.repeat(31))).toBe(false)
  expect(trimName('\u0085 导演\u3000')).toBe('导演')
  expect(trimName('\ufeff导演')).toBe('\ufeff导演')
  expect(validName('导\n演')).toBe(false)
})
