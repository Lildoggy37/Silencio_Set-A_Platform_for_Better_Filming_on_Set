import { AccountError } from './accounts'
import type { Page } from './crews'
import type { HttpClient } from './http'

export type Notice = { id: string; event_type: string; title: string; summary: string; created_at: string; task_id: string; task_title: string;
  object_version: number; current_object_version: number; is_current: boolean; requires_confirmation: boolean; read_at: string | null; confirmed_at: string | null }
export type NoticeFilter = '' | 'unread' | 'needs_acknowledgement'
const record = (value: unknown): value is Record<string, unknown> => !!value && typeof value === 'object' && !Array.isArray(value)
const uuid = /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i
const id = (value: unknown): value is string => typeof value === 'string' && uuid.test(value)
const date = (value: unknown): value is string => typeof value === 'string' && Number.isFinite(Date.parse(value)) && /(?:Z|[+-]\d\d:\d\d)$/.test(value)
const text = (value: unknown, max: number): value is string => typeof value === 'string' && Array.from(value).length >= 1 && Array.from(value).length <= max && !/[\p{Cs}]/u.test(value)
const number = (value: unknown): value is number => typeof value === 'number' && Number.isSafeInteger(value) && value >= 0
const version = (value: unknown): value is number => number(value) && value > 0 && value <= 2_147_483_647
const invalid = () => new AccountError('INVALID_RESPONSE')
function checkedId(value: string) { if (!id(value)) throw invalid(); return value }
function parse(value: unknown): Notice {
  if (!record(value) || !id(value.id) || !id(value.task_id) || !text(value.title, 100) || !text(value.summary, 2000) || !text(value.task_title, 100)
    || !date(value.created_at) || !version(value.object_version) || !version(value.current_object_version) || typeof value.requires_confirmation !== 'boolean'
    || value.is_current !== (value.object_version === value.current_object_version) || !(value.read_at === null || date(value.read_at))
    || !(value.confirmed_at === null || date(value.confirmed_at) && value.requires_confirmation && date(value.read_at) && Date.parse(value.confirmed_at) >= Date.parse(value.read_at))
    || typeof value.event_type !== 'string' || !/^task\.(create|edit|start|submit|review|cancel)$/.test(value.event_type)) throw invalid()
  return { id: value.id, event_type: value.event_type, title: value.title, summary: value.summary, created_at: value.created_at, task_id: value.task_id,
    task_title: value.task_title, object_version: value.object_version, current_object_version: value.current_object_version, is_current: value.is_current as boolean,
    requires_confirmation: value.requires_confirmation, read_at: value.read_at, confirmed_at: value.confirmed_at }
}

export function createNotificationClient(http: HttpClient) {
  const path = (crewId: string) => `/crews/${checkedId(crewId)}/notifications`
  return {
    async list(crewId: string, page = 1, filter: NoticeFilter = '', signal?: AbortSignal): Promise<Page<Notice>> {
      if (!Number.isInteger(page) || page < 1 || page > 100_000 || !['', 'unread', 'needs_acknowledgement'].includes(filter)) throw new AccountError('VALIDATION_ERROR')
      const data = await http.request(`${path(crewId)}?page=${page}&page_size=20${filter ? `&${filter}=true` : ''}`, { signal })
      if (!record(data) || !Array.isArray(data.items) || data.items.length > 20 || data.page !== page || data.page_size !== 20 || !number(data.total) || data.total < data.items.length) throw invalid()
      return { items: data.items.map(parse), page, page_size: 20, total: data.total }
    },
    async get(crewId: string, noticeId: string, signal?: AbortSignal) {
      const result = parse(await http.request(`${path(crewId)}/${checkedId(noticeId)}`, { signal }))
      if (result.id !== noticeId) throw invalid()
      return result
    },
    async read(crewId: string, noticeId: string) {
      const result = parse(await http.mutate(`${path(crewId)}/${checkedId(noticeId)}/read`, {}))
      if (result.id !== noticeId || result.read_at === null) throw invalid()
      return result
    },
    async acknowledge(crewId: string, noticeId: string, objectVersion: number) {
      if (!version(objectVersion)) throw new AccountError('VALIDATION_ERROR')
      const result = parse(await http.mutate(`${path(crewId)}/${checkedId(noticeId)}/acknowledge`, { object_version: objectVersion }))
      if (result.id !== noticeId || result.object_version !== objectVersion || result.confirmed_at === null) throw invalid()
      return result
    },
  }
}
export type NotificationClient = ReturnType<typeof createNotificationClient>
