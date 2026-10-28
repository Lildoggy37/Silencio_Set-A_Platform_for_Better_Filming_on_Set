import { expect, test, vi } from 'vitest'
import { createHttpClient } from './http'
import { createAvailabilityClient } from './availability'

const crew = '22222222-2222-4222-8222-222222222222'
const member = '33333333-3333-4333-8333-333333333333'
const epoch = '2026-10-01T00:00:00.123456Z'
const range = { range_start: '2026-11-01T00:00:00Z', range_end: '2026-11-02T00:00:00Z' }
const calendar = { ...range, member_id: member, display_name: '摄影', membership_joined_at: epoch, lock_version: 1, updated_at: null,
  intervals: [], unregistered: [{ starts_at: range.range_start, ends_at: range.range_end }], plan_review_status: 'not_implemented' }
const ok = (data: unknown) => new Response(JSON.stringify({ data }), { status: 200 })

test('empty calendar remains unknown and a missing unknown gap is rejected', async () => {
  const fetcher = vi.fn().mockResolvedValueOnce(ok({ ...calendar, email: 'private@example.com' })).mockResolvedValueOnce(ok({ ...calendar, unregistered: [] }))
  vi.stubGlobal('fetch', fetcher)
  const client = createAvailabilityClient(createHttpClient())
  expect(await client.own(crew, member, range)).toEqual(calendar)
  await expect(client.own(crew, member, range)).rejects.toMatchObject({ code: 'INVALID_RESPONSE' })
})

test('self replacement preserves the full membership timestamp and never submits a target member', async () => {
  const fetcher = vi.fn().mockResolvedValueOnce(ok({ csrf_token: 'c'.repeat(64) })).mockResolvedValueOnce(ok({ ...calendar, lock_version: 2 }))
  vi.stubGlobal('fetch', fetcher)
  await createAvailabilityClient(createHttpClient()).replace(crew, member, { ...range, expected_lock_version: 1, expected_membership_joined_at: epoch, intervals: [] })
  const [path, options] = fetcher.mock.calls[1]
  expect(path).toBe(`/api/v1/crews/${crew}/members/me/availability`)
  expect(options.method).toBe('PUT')
  expect(options.headers['X-CSRF-Token']).toBe('c'.repeat(64))
  expect(JSON.parse(options.body)).toEqual({ ...range, expected_lock_version: 1, expected_membership_joined_at: epoch, intervals: [] })
})

test('overlapping draft intervals fail before a credentialed request', async () => {
  const fetcher = vi.fn(); vi.stubGlobal('fetch', fetcher)
  await expect(createAvailabilityClient(createHttpClient()).replace(crew, member, { ...range, expected_lock_version: 1, expected_membership_joined_at: epoch,
    intervals: [{ starts_at: '2026-11-01T10:00:00Z', ends_at: '2026-11-01T12:00:00Z', availability: 'available' },
      { starts_at: '2026-11-01T11:00:00Z', ends_at: '2026-11-01T13:00:00Z', availability: 'unavailable' }] })).rejects.toMatchObject({ code: 'VALIDATION_ERROR' })
  expect(fetcher).not.toHaveBeenCalled()
})

test('unknown calendar save is not automatically retried', async () => {
  const fetcher = vi.fn().mockResolvedValueOnce(ok({ csrf_token: 'c'.repeat(64) })).mockRejectedValueOnce(new TypeError('lost reply'))
  vi.stubGlobal('fetch', fetcher)
  await expect(createAvailabilityClient(createHttpClient()).replace(crew, member, { ...range, expected_lock_version: 1, expected_membership_joined_at: epoch, intervals: [] })).rejects.toMatchObject({ code: 'NETWORK_UNCONFIRMED' })
  expect(fetcher).toHaveBeenCalledTimes(2)
})
