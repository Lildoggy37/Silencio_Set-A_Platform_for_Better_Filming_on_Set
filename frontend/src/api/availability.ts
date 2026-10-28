import { AccountError } from './accounts'
import type { HttpClient } from './http'
import type { Page } from './crews'

export type TimeWindow = { starts_at: string; ends_at: string }
export type CalendarInterval = TimeWindow & { availability: 'available' | 'unavailable' }
export type CalendarRange = { range_start: string; range_end: string }
export type Calendar = CalendarRange & { member_id: string; display_name: string; membership_joined_at: string; lock_version: number; updated_at: string | null;
  intervals: CalendarInterval[]; unregistered: TimeWindow[]; plan_review_status: 'not_implemented' }
export type CalendarReplacement = CalendarRange & { expected_lock_version: number; expected_membership_joined_at: string; intervals: CalendarInterval[] }
const record = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object' && !Array.isArray(v)
const uuid = /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i
const id = (v: unknown): v is string => typeof v === 'string' && uuid.test(v)
const date = (v: unknown): v is string => typeof v === 'string' && Number.isFinite(Date.parse(v)) && /(?:Z|[+-]\d\d:\d\d)$/.test(v)
const seconds = (v: unknown): v is string => date(v) && Date.parse(v) % 1000 === 0
const integer = (v: unknown): v is number => typeof v === 'number' && Number.isSafeInteger(v) && v >= 0
const version = (v: unknown): v is number => integer(v) && v >= 1 && v <= 2_147_483_647
const invalid = () => new AccountError('INVALID_RESPONSE')
function checkedId(v: string) { if (!id(v)) throw invalid(); return v }
function validRange(v: CalendarRange) { return seconds(v.range_start) && seconds(v.range_end) && Date.parse(v.range_end) > Date.parse(v.range_start) && Date.parse(v.range_end) - Date.parse(v.range_start) <= 31 * 86_400_000 }
function intervals(raw: unknown, range: CalendarRange, maximum: number): CalendarInterval[] {
  if (!Array.isArray(raw) || raw.length > maximum) throw invalid()
  let cursor = Date.parse(range.range_start)
  return raw.map(value => {
    if (!record(value) || !seconds(value.starts_at) || !seconds(value.ends_at) || !['available', 'unavailable'].includes(String(value.availability))
      || Date.parse(value.starts_at) < cursor || Date.parse(value.ends_at) <= Date.parse(value.starts_at) || Date.parse(value.ends_at) > Date.parse(range.range_end)) throw invalid()
    cursor = Date.parse(value.ends_at)
    return { starts_at: value.starts_at, ends_at: value.ends_at, availability: value.availability as CalendarInterval['availability'] }
  })
}
function parse(v: unknown, range: CalendarRange): Calendar {
  if (!record(v) || !id(v.member_id) || typeof v.display_name !== 'string' || !v.display_name.trim() || Array.from(v.display_name).length > 30
    || !date(v.membership_joined_at) || !version(v.lock_version) || !(v.updated_at === null || date(v.updated_at)) || !seconds(v.range_start) || !seconds(v.range_end)
    || Date.parse(v.range_start) !== Date.parse(range.range_start) || Date.parse(v.range_end) !== Date.parse(range.range_end) || v.plan_review_status !== 'not_implemented') throw invalid()
  const values = intervals(v.intervals, range, 500)
  let cursor = Date.parse(range.range_start); const gaps: number[][] = []
  for (const item of values) { const start = Date.parse(item.starts_at); if (start > cursor) gaps.push([cursor, start]); cursor = Date.parse(item.ends_at) }
  if (cursor < Date.parse(range.range_end)) gaps.push([cursor, Date.parse(range.range_end)])
  if (!Array.isArray(v.unregistered) || v.unregistered.length !== gaps.length) throw invalid()
  const unregistered = v.unregistered.map((value, index) => {
    if (!record(value) || !seconds(value.starts_at) || !seconds(value.ends_at) || Date.parse(value.starts_at) !== gaps[index][0] || Date.parse(value.ends_at) !== gaps[index][1]) throw invalid()
    return { starts_at: value.starts_at, ends_at: value.ends_at }
  })
  return { member_id: v.member_id, display_name: v.display_name, membership_joined_at: v.membership_joined_at, lock_version: v.lock_version, updated_at: v.updated_at,
    range_start: v.range_start, range_end: v.range_end, intervals: values, unregistered, plan_review_status: 'not_implemented' }
}

export function createAvailabilityClient(http: HttpClient) {
  const base = (crewId: string) => `/crews/${checkedId(crewId)}`
  const query = (range: CalendarRange) => { if (!validRange(range)) throw new AccountError('VALIDATION_ERROR'); return new URLSearchParams(range).toString() }
  return {
    async own(crewId: string, selfMemberId: string, range: CalendarRange, signal?: AbortSignal) {
      checkedId(selfMemberId)
      const result = parse(await http.request(`${base(crewId)}/members/me/availability?${query(range)}`, { signal }), range)
      if (result.member_id !== selfMemberId) throw invalid()
      return result
    },
    async list(crewId: string, range: CalendarRange, page = 1, memberId?: string, signal?: AbortSignal): Promise<Page<Calendar>> {
      if (!Number.isInteger(page) || page < 1 || page > 100_000) throw new AccountError('VALIDATION_ERROR')
      const raw = await http.request(`${base(crewId)}/availability?${query(range)}&page=${page}&page_size=20${memberId ? `&member_id=${checkedId(memberId)}` : ''}`, { signal })
      if (!record(raw) || !Array.isArray(raw.items) || raw.items.length > 20 || raw.page !== page || raw.page_size !== 20 || !integer(raw.total) || raw.total < raw.items.length) throw invalid()
      const items = raw.items.map(item => parse(item, range))
      if (new Set(items.map(item => item.member_id)).size !== items.length || memberId && items.some(item => item.member_id !== memberId)) throw invalid()
      return { items, total: raw.total, page, page_size: 20 }
    },
    async replace(crewId: string, selfMemberId: string, body: CalendarReplacement) {
      checkedId(selfMemberId)
      if (!validRange(body) || !version(body.expected_lock_version) || !date(body.expected_membership_joined_at)) throw new AccountError('VALIDATION_ERROR')
      let values: CalendarInterval[]
      try { values = intervals([...body.intervals].sort((a, b) => Date.parse(a.starts_at) - Date.parse(b.starts_at)), body, 100) } catch { throw new AccountError('VALIDATION_ERROR') }
      const data = await http.mutate(`${base(crewId)}/members/me/availability`, { range_start: body.range_start, range_end: body.range_end,
        expected_lock_version: body.expected_lock_version, expected_membership_joined_at: body.expected_membership_joined_at, intervals: values }, 'PUT')
      const result = parse(data, body)
      if (result.member_id !== selfMemberId || result.lock_version !== body.expected_lock_version + 1 || result.membership_joined_at !== body.expected_membership_joined_at) throw invalid()
      return result
    },
  }
}
export type AvailabilityClient = ReturnType<typeof createAvailabilityClient>
