import { AccountError, validName } from './accounts'
import type { HttpClient } from './http'

export const roleNames: Record<string, string> = { admin: '管理员', director: '导演', producer: '制片', writer: '编剧', cinematographer: '摄影', art: '美术', staff: '工作人员', actor: '演员' }
export type Member = { id: string; display_name: string; status: 'active'; role_codes: string[]; lock_version: number }
export type Crew = { id: string; name: string; project_name: string; description: string | null; created_at: string; membership: Member; permissions: string[] }
export type Page<T> = { items: T[]; page: number; page_size: number; total: number }
export type CrewInput = { name: string; project_name: string; description: string | null }
export type Invitation = { id: string; email: string; status: 'pending' | 'accepted' | 'revoked' | 'expired'; expires_at: string; created_at: string; lock_version: number }
export type ExitTask = { id: string; title: string; status: 'pending' | 'in_progress' | 'pending_review'; due_at: string; lock_version: number; is_owner: boolean; is_reviewer: boolean }
export type ExitResponsibilities = Page<ExitTask> & { member_id: string; member_lock_version: number; blocking_total: number; restricted_total: number }
const uuid = /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i
const isRecord = (value: unknown): value is Record<string, unknown> => !!value && typeof value === 'object' && !Array.isArray(value)
const validId = (value: unknown): value is string => typeof value === 'string' && uuid.test(value)
const integer = (value: unknown): value is number => typeof value === 'number' && Number.isSafeInteger(value) && value >= 0
const text = (value: unknown, max: number): value is string => typeof value === 'string' && Array.from(value).length >= 1 && Array.from(value).length <= max && !/[\p{Cc}\p{Cs}]/u.test(value)
const awareDate = (value: unknown): value is string => typeof value === 'string' && Number.isFinite(Date.parse(value)) && /(?:Z|[+-]\d{2}:\d{2})$/.test(value)

function member(value: unknown): Member {
  if (!isRecord(value) || !validId(value.id) || typeof value.display_name !== 'string' || !validName(value.display_name)
      || value.status !== 'active' || !integer(value.lock_version) || value.lock_version < 1
      || !Array.isArray(value.role_codes) || !value.role_codes.every(code => typeof code === 'string' && Object.hasOwn(roleNames, code))) throw new AccountError('INVALID_RESPONSE')
  return { id: value.id, display_name: value.display_name, status: 'active', lock_version: value.lock_version, role_codes: [...new Set(value.role_codes as string[])] }
}

function crew(value: unknown): Crew {
  if (!isRecord(value) || !validId(value.id) || !text(value.name, 50) || !text(value.project_name, 100)
      || !(value.description === null || typeof value.description === 'string' && Array.from(value.description).length <= 1000)
      || typeof value.created_at !== 'string' || !Number.isFinite(Date.parse(value.created_at)) || !/(Z|[+-]\d\d:\d\d)$/.test(value.created_at)
      || !Array.isArray(value.permissions) || value.permissions.length > 100
      || !value.permissions.every(code => typeof code === 'string' && /^[a-z_]+\.[a-z_]+$/.test(code))) throw new AccountError('INVALID_RESPONSE')
  return { id: value.id, name: value.name, project_name: value.project_name, description: value.description,
    created_at: value.created_at, membership: member(value.membership), permissions: [...new Set(value.permissions as string[])] }
}

function page<T>(value: unknown, parse: (value: unknown) => T, expected: number): Page<T> {
  if (!isRecord(value) || !Array.isArray(value.items) || value.items.length > 20 || value.page !== expected
      || value.page_size !== 20 || !integer(value.total) || value.total < value.items.length) throw new AccountError('INVALID_RESPONSE')
  return { items: value.items.map(parse), page: expected, page_size: 20, total: value.total }
}

function checkedId(id: string): string { if (!validId(id)) throw new AccountError('INVALID_RESPONSE'); return id }
function checkedPage(number: number): number { if (!Number.isSafeInteger(number) || number < 1 || number > 100_000) throw new AccountError('VALIDATION_ERROR'); return number }

function invitation(value: unknown): Invitation {
  if (!isRecord(value) || !validId(value.id) || typeof value.email !== 'string' || value.email.length > 254 || !value.email.includes('@')
    || !['pending', 'accepted', 'revoked', 'expired'].includes(String(value.status)) || !integer(value.lock_version) || value.lock_version < 1
    || !awareDate(value.expires_at) || !awareDate(value.created_at)) throw new AccountError('INVALID_RESPONSE')
  return { id: value.id, email: value.email, status: value.status as Invitation['status'], expires_at: value.expires_at, created_at: value.created_at, lock_version: value.lock_version }
}

export function createCrewClient(http: HttpClient) {
  async function endMembership(crewId: string, memberId: string, version: number, reason?: string) {
    if (!integer(version) || version < 1 || version >= 2_147_483_647) throw new AccountError('VALIDATION_ERROR')
    const removing = reason !== undefined
    if (removing && !text(reason.trim(), 500)) throw new AccountError('VALIDATION_ERROR')
    const path = `/crews/${checkedId(crewId)}/members/${removing ? `${checkedId(memberId)}/remove` : 'me/leave'}`
    checkedId(memberId)
    const data = await http.mutate(path, { expected_lock_version: version, ...(removing ? { reason: reason.trim() } : {}) })
    if (!isRecord(data) || !awareDate(data.ended_at) || !isRecord(data.membership)) throw new AccountError('INVALID_RESPONSE')
    const result = data.membership
    if (result.id !== memberId || result.status !== (removing ? 'removed' : 'left') || result.lock_version !== version + 1
      || !Array.isArray(result.role_codes) || result.role_codes.length !== 0 || typeof result.display_name !== 'string' || !validName(result.display_name)) throw new AccountError('INVALID_RESPONSE')
    return { memberId, status: removing ? 'removed' as const : 'left' as const, endedAt: data.ended_at, lockVersion: version + 1 }
  }
  return {
    async responsibilities(crewId: string, memberId: string, number = 1, signal?: AbortSignal): Promise<ExitResponsibilities> {
      const data = await http.request(`/crews/${checkedId(crewId)}/members/${checkedId(memberId)}/exit-responsibilities?page=${checkedPage(number)}&page_size=20`, { signal })
      if (!isRecord(data) || data.member_id !== memberId || !integer(data.member_lock_version) || data.member_lock_version < 1 || !integer(data.blocking_total)
        || !integer(data.restricted_total) || !integer(data.total) || data.blocking_total !== data.total + data.restricted_total) throw new AccountError('INVALID_RESPONSE')
      const result = page(data, value => {
        if (!isRecord(value) || !validId(value.id) || !text(value.title, 100) || !['pending', 'in_progress', 'pending_review'].includes(String(value.status))
          || !awareDate(value.due_at) || !integer(value.lock_version) || value.lock_version < 1 || typeof value.is_owner !== 'boolean'
          || typeof value.is_reviewer !== 'boolean' || !value.is_owner && !value.is_reviewer) throw new AccountError('INVALID_RESPONSE')
        return { id: value.id, title: value.title, status: value.status as ExitTask['status'], due_at: value.due_at, lock_version: value.lock_version, is_owner: value.is_owner, is_reviewer: value.is_reviewer }
      }, number)
      return { ...result, member_id: memberId, member_lock_version: data.member_lock_version, blocking_total: data.blocking_total, restricted_total: data.restricted_total }
    },
    async leave(crewId: string, memberId: string, version: number) { return endMembership(crewId, memberId, version) },
    async remove(crewId: string, memberId: string, version: number, reason: string) { return endMembership(crewId, memberId, version, reason) },
    async list(number = 1, signal?: AbortSignal) { return page(await http.request(`/crews?page=${checkedPage(number)}&page_size=20`, { signal }), crew, number) },
    async get(id: string, signal?: AbortSignal) { return crew(await http.request(`/crews/${checkedId(id)}`, { signal })) },
    async members(id: string, number = 1, signal?: AbortSignal) { return page(await http.request(`/crews/${checkedId(id)}/members?page=${checkedPage(number)}&page_size=20`, { signal }), member, number) },
    async replaceRoles(crewId: string, memberId: string, roleCodes: string[], version: number) {
      if (!roleCodes.every(code => Object.hasOwn(roleNames, code)) || new Set(roleCodes).size !== roleCodes.length || !integer(version) || version < 1 || version > 2_147_483_647) throw new AccountError('VALIDATION_ERROR')
      const result = member(await http.mutate(`/crews/${checkedId(crewId)}/members/${checkedId(memberId)}/roles`, { role_codes: [...roleCodes].sort(), expected_lock_version: version }, 'PUT'))
      if (result.id !== memberId || result.lock_version !== version + 1 || [...result.role_codes].sort().join(',') !== [...roleCodes].sort().join(',')) throw new AccountError('INVALID_RESPONSE')
      return result
    },
    async create(input: CrewInput) { return crew(await http.mutate('/crews', input, 'POST', [201])) },
    async invitations(id: string, number = 1, signal?: AbortSignal) { return page(await http.request(`/crews/${checkedId(id)}/invitations?page=${checkedPage(number)}&page_size=20`, { signal }), invitation, number) },
    async invite(id: string, email: string) {
      const data = await http.mutate(`/crews/${checkedId(id)}/invitations`, { email }, 'POST', [202])
      if (!isRecord(data) || data.delivery_status !== 'pending_dispatch') throw new AccountError('INVALID_RESPONSE')
      const result = invitation(data.invitation)
      if (result.status !== 'pending') throw new AccountError('INVALID_RESPONSE')
      return result
    },
    async revokeInvitation(id: string, invitationId: string, version: number) {
      const result = invitation(await http.mutate(`/crews/${checkedId(id)}/invitations/${checkedId(invitationId)}/revoke`, { expected_lock_version: version }))
      if (result.id !== invitationId || result.status !== 'revoked' || result.lock_version !== version + 1) throw new AccountError('INVALID_RESPONSE')
      return result
    },
    async acceptInvitation(token: string) {
      if (!/^[A-Za-z0-9_-]{43}$/.test(token)) throw new AccountError('VALIDATION_ERROR')
      const data = await http.mutate('/invitations/accept', { token })
      if (!isRecord(data) || data.accepted !== true || !validId(data.crew_id)) throw new AccountError('INVALID_RESPONSE')
      return { crewId: data.crew_id, membership: member(data.membership) }
    },
  }
}
export type CrewClient = ReturnType<typeof createCrewClient>
