import { AccountError, createHttpClient } from './http'
import type { HttpClient } from './http'
export { AccountError } from './http'

export type User = {
  id: string
  email: string
  display_name: string | null
  profile_completed: boolean
  missing_fields: string[]
  lock_version: number
}
export type Profile = Pick<User, 'display_name' | 'profile_completed' | 'missing_fields' | 'lock_version'>
export type Verification = { verification_id: string; expires_at: string; resend_after_seconds: number }

function record(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
const validVersion = (value: unknown): value is number => Number.isInteger(value) && Number(value) >= 1 && Number(value) <= 2_147_483_647
const validDate = (value: unknown): value is string => typeof value === 'string' && Number.isFinite(Date.parse(value)) && /(?:Z|[+-]\d{2}:\d{2})$/.test(value)

// Match Python str.strip() for profile names, rather than UTF-16 length or JS's
// different treatment of U+0085/FEFF. Count Unicode code points, not glyphs.
export function trimName(value: string): string {
  return value.replace(/^[\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+|[\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+$/g, '')
}
export function validName(value: string): boolean {
  const name = trimName(value)
  return Array.from(name).length >= 1 && Array.from(name).length <= 30 && !/[\p{Cc}\p{Cs}]/u.test(name)
}
export function validNewPassword(value: string): boolean {
  const length = Array.from(value).length
  return length >= 15 && length <= 128 && !/\p{Cs}/u.test(value)
}

function parseProfile(value: unknown): Profile {
  if (!record(value) || !validVersion(value.lock_version) || typeof value.profile_completed !== 'boolean'
      || !(value.display_name === null || typeof value.display_name === 'string') || !Array.isArray(value.missing_fields)) {
    throw new AccountError('INVALID_RESPONSE')
  }
  const completed = value.profile_completed
  if (completed ? !(typeof value.display_name === 'string' && validName(value.display_name) && trimName(value.display_name) === value.display_name && value.missing_fields.length === 0)
    : !(value.missing_fields.length === 1 && value.missing_fields[0] === 'display_name')) {
    throw new AccountError('INVALID_RESPONSE')
  }
  return { display_name: value.display_name, profile_completed: completed, missing_fields: completed ? [] : ['display_name'], lock_version: value.lock_version }
}

function parseUser(value: unknown): User {
  if (!record(value) || typeof value.id !== 'string' || !uuid.test(value.id) || typeof value.email !== 'string') {
    throw new AccountError('INVALID_RESPONSE')
  }
  return { id: value.id, email: value.email, ...parseProfile(value) }
}

export function createAccountClient(http: HttpClient = createHttpClient()) {
  const { request, mutate, prepareCsrf, invalidateCsrf } = http
  return {
    invalidateCsrf, prepareCsrf,
    async me(signal?: AbortSignal): Promise<User | null> {
      try { return parseUser(await request('/users/me', { signal })) }
      catch (error) { if (error instanceof AccountError && error.code === 'SESSION_EXPIRED') return null; throw error }
    },
    async login(email: string, password: string): Promise<User> {
      const user = parseUser(await mutate('/auth/login', { email, password }))
      invalidateCsrf()
      return user
    },
    async logout(): Promise<void> {
      await mutate('/auth/logout', {}, 'POST', [204])
      invalidateCsrf()
    },
    async requestVerification(email: string): Promise<Verification> {
      const data = await mutate('/auth/email-verifications', { email, purpose: 'register' }, 'POST', [202])
      if (!record(data) || typeof data.verification_id !== 'string' || !uuid.test(data.verification_id) || !validDate(data.expires_at)
          || !Number.isInteger(data.resend_after_seconds) || Number(data.resend_after_seconds) < 1 || Number(data.resend_after_seconds) > 300
          || data.delivery_status !== 'pending_dispatch') throw new AccountError('INVALID_RESPONSE')
      return { verification_id: data.verification_id, expires_at: data.expires_at, resend_after_seconds: Number(data.resend_after_seconds) }
    },
    async verify(email: string, verificationId: string, code: string): Promise<{ token: string; expiresAt: string }> {
      const data = await mutate('/auth/email-verifications/verify', { email, verification_id: verificationId, code })
      if (!record(data) || typeof data.registration_token !== 'string' || !/^[A-Za-z0-9_-]{43}$/.test(data.registration_token) || !validDate(data.expires_at)) throw new AccountError('INVALID_RESPONSE')
      return { token: data.registration_token, expiresAt: data.expires_at }
    },
    async register(token: string, password: string): Promise<void> {
      const data = await mutate('/auth/register', { registration_token: token, password }, 'POST', [200, 201])
      if (!record(data) || data.registered !== true || typeof data.user_id !== 'string' || !uuid.test(data.user_id)) throw new AccountError('INVALID_RESPONSE')
    },
    async saveProfile(displayName: string, version: number): Promise<Profile> {
      return parseProfile(await mutate('/users/me/profile', { display_name: trimName(displayName), expected_lock_version: version }, 'PATCH'))
    },
  }
}

export type AccountClient = ReturnType<typeof createAccountClient>
