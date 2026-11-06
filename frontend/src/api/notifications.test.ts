import { expect, test, vi } from 'vitest'
import { createHttpClient } from './http'
import { createNotificationClient } from './notifications'
import { createCrewClient } from './crews'

const crew = '22222222-2222-4222-8222-222222222222'
const id = '33333333-3333-4333-8333-333333333333'
const task = '44444444-4444-4444-8444-444444444444'
const notice = { id, event_type: 'task.create', title: '新任务', summary: '请核对', source_type: 'task', source_id: task, object_title: '分镜', created_at: '2026-10-28T00:00:00Z',
  object_version: 1, current_object_version: 1, is_current: true, source_status: 'current', can_open_source: true, requires_confirmation: true, read_at: null, confirmed_at: null }
const ok = (data: unknown) => new Response(JSON.stringify({ data }), { status: 200 })

test('withdrawal notices parse as historical information without acknowledgement', async () => {
  const withdrawn = { ...notice, event_type: 'plan.withdrawn', source_type: 'plan', source_status: 'withdrawn', is_current: false, current_object_version: null, can_open_source: false, requires_confirmation: false }
  vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(ok(withdrawn)))
  expect(await createNotificationClient(createHttpClient()).get(crew, id)).toEqual(withdrawn)
})

test('notice parser drops receiver data and rejects inconsistent confirmation records', async () => {
  const fetcher = vi.fn().mockResolvedValueOnce(ok({ ...notice, receiver_email: 'private@example.com' }))
    .mockResolvedValueOnce(ok({ ...notice, confirmed_at: '2026-10-28T01:00:00Z' }))
  vi.stubGlobal('fetch', fetcher)
  const api = createNotificationClient(createHttpClient())
  expect(await api.get(crew, id)).toEqual(notice)
  await expect(api.get(crew, id)).rejects.toMatchObject({ code: 'INVALID_RESPONSE' })
})

test('acknowledgement binds only the notification version and uses the shared CSRF client', async () => {
  const fetcher = vi.fn().mockResolvedValueOnce(ok({ csrf_token: 'c'.repeat(64) })).mockResolvedValueOnce(ok({ ...notice, read_at: '2026-10-28T01:00:00Z', confirmed_at: '2026-10-28T01:00:00Z' }))
  vi.stubGlobal('fetch', fetcher)
  await createNotificationClient(createHttpClient()).acknowledge(crew, id, 1)
  const [url, options] = fetcher.mock.calls[1]
  expect(url).toBe(`/api/v1/crews/${crew}/notifications/${id}/acknowledge`)
  expect(JSON.parse(options.body)).toEqual({ object_version: 1 })
  expect(options.headers['X-CSRF-Token']).toBe('c'.repeat(64))
})

test('responsibility parser rejects inconsistent counts without exposing hidden item fields', async () => {
  const fetcher = vi.fn().mockResolvedValueOnce(ok({ member_id: id, member_lock_version: 1, items: [], total: 0, blocking_total: 2, restricted_total: 0, page: 1, page_size: 20 }))
  vi.stubGlobal('fetch', fetcher)
  await expect(createCrewClient(createHttpClient()).responsibilities(crew, id)).rejects.toMatchObject({ code: 'INVALID_RESPONSE' })
})

test('read mutation never retries automatically on a lost response', async () => {
  const fetcher = vi.fn().mockResolvedValueOnce(ok({ csrf_token: 'c'.repeat(64) })).mockRejectedValueOnce(new TypeError('lost response'))
  vi.stubGlobal('fetch', fetcher)
  await expect(createNotificationClient(createHttpClient()).read(crew, id)).rejects.toMatchObject({ code: 'NETWORK_UNCONFIRMED' })
  expect(fetcher).toHaveBeenCalledTimes(2)
})

test('publication status can invalidate a notice without changing its version number', async () => {
  const item = { ...notice, source_type: 'call_sheet', event_type: 'call_sheet.published', source_status: 'withdrawn', is_current: false }
  vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(ok(item)))
  expect(await createNotificationClient(createHttpClient()).get(crew, id)).toEqual(item)
})
