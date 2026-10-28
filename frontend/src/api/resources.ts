import { AccountError } from './accounts'
import type { HttpClient } from './http'
import type { Page } from './crews'
import type { CalendarRange } from './availability'

export const resourceTypes = { equipment: '设备', prop: '道具', location: '场地' }
export const reservationStatuses = { pending: '待审批', confirmed: '已批准', rejected: '已拒绝', cancelled: '已取消' }
export type Resource = {
  id: string; crew_id: string; name: string; resource_type: keyof typeof resourceTypes; unit: string; total_quantity: number
  status: 'available' | 'disabled'; description: string | null; manager_member_id: string; manager_display_name: string; manager_lock_version: number
  review_ready: boolean; lock_version: number; created_at: string; updated_at: string; allowed_actions: ('edit' | 'authorize' | 'apply')[]
}
export type Reservation = {
  id: string; crew_id: string; resource_id: string; applicant_member_id: string; starts_at: string; ends_at: string; quantity: number
  status: keyof typeof reservationStatuses; reviewed_by: string | null; reviewed_at: string | null; decision_note: string | null
  cancelled_by: string | null; cancelled_at: string | null; cancel_reason: string | null; lock_version: number; created_at: string; updated_at: string
  allowed_actions: ('accept' | 'reject' | 'cancel')[]; dependency_check_status: 'not_implemented'
}
export type ResourceCapacity = CalendarRange & { resource_id: string; resource_lock_version: number; total_quantity: number;
  peak_confirmed_quantity: number; minimum_remaining_quantity: number; confirmed_reservation_count: number; physical_inventory_status: 'not_implemented' }
export type ResourceResponsibility = { kind: 'resource' | 'reservation'; id: string; resource_id: string; title: string;
  status: 'available' | 'disabled' | 'pending' | 'confirmed'; lock_version: number; ends_at: string | null }
export type ResourceCommand = { action: 'create' | 'edit' | 'authorize' | 'apply' | 'decide' | 'cancel'; resourceId?: string; reservationId?: string; body: Record<string, unknown>; key?: string }
const record = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object' && !Array.isArray(v)
const id = (v: unknown): v is string => typeof v === 'string' && /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i.test(v)
const integer = (v: unknown): v is number => typeof v === 'number' && Number.isSafeInteger(v) && v >= 0
const version = (v: unknown): v is number => integer(v) && v >= 1 && v <= 2_147_483_647
const date = (v: unknown): v is string => typeof v === 'string' && /(?:Z|[+-]\d\d:\d\d)$/.test(v) && Number.isFinite(Date.parse(v))
const text = (v: unknown, max: number): v is string => typeof v === 'string' && v.trim().length > 0 && Array.from(v).length <= max && !/\p{Cs}/u.test(v)
const invalid = () => new AccountError('INVALID_RESPONSE')
function checkedId(v: string) { if (!id(v)) throw invalid(); return v }
function actions(v: unknown, allowed: string[]) { return Array.isArray(v) && v.every(a => typeof a === 'string' && allowed.includes(a)) && new Set(v).size === v.length }
function pick<T>(v: Record<string, unknown>, fields: (keyof T)[]): T { return Object.fromEntries(fields.map(field => [field, v[String(field)]])) as T }
function parseResource(v: unknown): Resource {
  if (!record(v) || !id(v.id) || !id(v.crew_id) || !id(v.manager_member_id) || !text(v.name, 100) || !text(v.unit, 10)
    || !Object.hasOwn(resourceTypes, String(v.resource_type)) || !['available', 'disabled'].includes(String(v.status))
    || !integer(v.total_quantity) || v.total_quantity < 1 || v.total_quantity > 1_000_000 || !version(v.lock_version)
    || !integer(v.manager_lock_version) || v.manager_lock_version > 2_147_483_647 || !text(v.manager_display_name, 30)
    || typeof v.review_ready !== 'boolean' || !date(v.created_at) || !date(v.updated_at) || !(v.description === null || text(v.description, 2000))
    || !actions(v.allowed_actions, ['edit', 'authorize', 'apply'])) throw invalid()
  return pick<Resource>(v, ['id', 'crew_id', 'name', 'resource_type', 'unit', 'total_quantity', 'status', 'description', 'manager_member_id', 'manager_display_name', 'manager_lock_version', 'review_ready', 'lock_version', 'created_at', 'updated_at', 'allowed_actions'])
}
function parseReservation(v: unknown): Reservation {
  if (!record(v) || !id(v.id) || !id(v.crew_id) || !id(v.resource_id) || !id(v.applicant_member_id) || !date(v.starts_at) || !date(v.ends_at)
    || Date.parse(v.ends_at) <= Date.parse(v.starts_at) || !integer(v.quantity) || v.quantity < 1 || v.quantity > 1_000_000 || !version(v.lock_version)
    || !Object.hasOwn(reservationStatuses, String(v.status)) || !date(v.created_at) || !date(v.updated_at)
    || !(v.decision_note === null || text(v.decision_note, 500)) || v.dependency_check_status !== 'not_implemented'
    || !actions(v.allowed_actions, ['accept', 'reject', 'cancel'])) throw invalid()
  if (!(v.reviewed_by === null && v.reviewed_at === null || id(v.reviewed_by) && date(v.reviewed_at))) throw invalid()
  if (['confirmed', 'rejected'].includes(String(v.status)) && v.reviewed_by === null || v.status === 'rejected' && !text(v.decision_note, 500)) throw invalid()
  if (v.status === 'cancelled' ? !id(v.cancelled_by) || !date(v.cancelled_at) || !text(v.cancel_reason, 500) : v.cancelled_by !== null || v.cancelled_at !== null || v.cancel_reason !== null) throw invalid()
  return pick<Reservation>(v, ['id', 'crew_id', 'resource_id', 'applicant_member_id', 'starts_at', 'ends_at', 'quantity', 'status', 'reviewed_by', 'reviewed_at', 'decision_note', 'cancelled_by', 'cancelled_at', 'cancel_reason', 'lock_version', 'created_at', 'updated_at', 'allowed_actions', 'dependency_check_status'])
}
function parsePage<T>(v: unknown, page: number, parse: (v: unknown) => T): Page<T> {
  if (!record(v) || !Array.isArray(v.items) || v.items.length > 20 || v.page !== page || v.page_size !== 20 || !integer(v.total) || v.total < v.items.length) throw invalid()
  return { items: v.items.map(parse), total: v.total, page, page_size: 20 }
}
function pageQuery(page: number) { if (!Number.isInteger(page) || page < 1 || page > 100_000) throw new AccountError('VALIDATION_ERROR'); return `page=${page}&page_size=20` }

export function createResourceClient(http: HttpClient) {
  const base = (crewId: string) => `/crews/${checkedId(crewId)}`
  return {
    async list(crewId: string, page = 1, signal?: AbortSignal) {
      const result = parsePage(await http.request(`${base(crewId)}/resources?${pageQuery(page)}`, { signal }), page, parseResource)
      if (result.items.some(row => row.crew_id !== crewId) || new Set(result.items.map(row => row.id)).size !== result.items.length) throw invalid()
      return result
    },
    async get(crewId: string, resourceId: string, signal?: AbortSignal) {
      const result = parseResource(await http.request(`${base(crewId)}/resources/${checkedId(resourceId)}`, { signal }))
      if (result.id !== resourceId || result.crew_id !== crewId) throw invalid()
      return result
    },
    async reservations(crewId: string, resourceId: string, page = 1, signal?: AbortSignal) {
      const result = parsePage(await http.request(`${base(crewId)}/resource-reservations?${pageQuery(page)}&resource_id=${checkedId(resourceId)}`, { signal }), page, parseReservation)
      if (result.items.some(row => row.crew_id !== crewId || row.resource_id !== resourceId) || new Set(result.items.map(row => row.id)).size !== result.items.length) throw invalid()
      return result
    },
    async reservation(crewId: string, reservationId: string, signal?: AbortSignal) {
      const result = parseReservation(await http.request(`${base(crewId)}/resource-reservations/${checkedId(reservationId)}`, { signal }))
      if (result.id !== reservationId || result.crew_id !== crewId) throw invalid()
      return result
    },
    async availability(crewId: string, resourceId: string, range: CalendarRange, signal?: AbortSignal): Promise<ResourceCapacity> {
      if (!date(range.range_start) || !date(range.range_end) || Date.parse(range.range_start) % 1000 || Date.parse(range.range_end) % 1000
        || Date.parse(range.range_end) <= Date.parse(range.range_start) || Date.parse(range.range_end) - Date.parse(range.range_start) > 31 * 86_400_000) throw new AccountError('VALIDATION_ERROR')
      const v = await http.request(`${base(crewId)}/resources/${checkedId(resourceId)}/availability?${new URLSearchParams(range)}`, { signal })
      if (!record(v) || v.resource_id !== resourceId || !version(v.resource_lock_version) || !integer(v.total_quantity) || v.total_quantity < 1
        || !integer(v.peak_confirmed_quantity) || v.peak_confirmed_quantity > v.total_quantity || !integer(v.minimum_remaining_quantity)
        || v.minimum_remaining_quantity !== v.total_quantity - v.peak_confirmed_quantity || !integer(v.confirmed_reservation_count) || v.confirmed_reservation_count > 1000
        || !date(v.range_start) || !date(v.range_end) || Date.parse(v.range_start) !== Date.parse(range.range_start) || Date.parse(v.range_end) !== Date.parse(range.range_end)
        || v.physical_inventory_status !== 'not_implemented') throw invalid()
      return pick<ResourceCapacity>(v, ['resource_id', 'resource_lock_version', 'range_start', 'range_end', 'total_quantity', 'peak_confirmed_quantity', 'minimum_remaining_quantity', 'confirmed_reservation_count', 'physical_inventory_status'])
    },
    async responsibilities(crewId: string, memberId: string, page = 1, signal?: AbortSignal) {
      const raw = await http.request(`${base(crewId)}/members/${checkedId(memberId)}/resource-responsibilities?${pageQuery(page)}`, { signal })
      if (!record(raw) || raw.member_id !== memberId || !integer(raw.blocking_total) || !integer(raw.restricted_total) || !integer(raw.total) || raw.blocking_total !== raw.total + raw.restricted_total) throw invalid()
      const result = parsePage(raw, page, (v): ResourceResponsibility => {
        if (!record(v) || !id(v.id) || !id(v.resource_id) || !['resource', 'reservation'].includes(String(v.kind)) || !text(v.title, 100) || !version(v.lock_version)
          || (v.kind === 'resource' ? !['available', 'disabled'].includes(String(v.status)) || v.ends_at !== null || v.id !== v.resource_id : !['pending', 'confirmed'].includes(String(v.status)) || !date(v.ends_at))) throw invalid()
        return pick<ResourceResponsibility>(v, ['kind', 'id', 'resource_id', 'title', 'status', 'lock_version', 'ends_at'])
      })
      return { ...result, member_id: memberId, blocking_total: raw.blocking_total, restricted_total: raw.restricted_total }
    },
    async execute(crewId: string, command: ResourceCommand): Promise<Resource | Reservation> {
      if (!['create', 'edit', 'authorize', 'apply', 'decide', 'cancel'].includes(command.action) || command.action !== 'edit' && !id(command.key)) throw new AccountError('VALIDATION_ERROR')
      const booking = ['apply', 'decide', 'cancel'].includes(command.action)
      const suffix = command.action === 'create' ? '/resources' : command.action === 'apply' ? '/resource-reservations'
        : command.action === 'decide' || command.action === 'cancel' ? `/resource-reservations/${checkedId(command.reservationId!)}/${command.action === 'decide' ? 'decisions' : 'cancel'}`
          : `/resources/${checkedId(command.resourceId!)}${command.action === 'authorize' ? '/authorization' : ''}`
      const raw = await http.mutate(`${base(crewId)}${suffix}`, command.body, command.action === 'edit' ? 'PATCH' : command.action === 'authorize' ? 'PUT' : 'POST', ['create', 'apply'].includes(command.action) ? [201] : [200], command.key)
      const result = booking ? parseReservation(raw) : parseResource(raw)
      if (result.crew_id !== crewId || command.reservationId && result.id !== command.reservationId || !booking && command.resourceId && result.id !== command.resourceId) throw invalid()
      if (['create', 'apply'].includes(command.action) ? result.lock_version !== 1 : result.lock_version !== Number(command.body.expected_lock_version) + 1) throw invalid()
      if (command.action === 'apply' && (result as Reservation).resource_id !== command.body.resource_id) throw invalid()
      const expected = command.action === 'apply' ? 'pending' : command.action === 'decide' ? command.body.decision === 'accept' ? 'confirmed' : 'rejected' : command.action === 'cancel' ? 'cancelled' : undefined
      if (expected && result.status !== expected) throw invalid()
      return result
    },
  }
}
export type ResourceClient = ReturnType<typeof createResourceClient>
