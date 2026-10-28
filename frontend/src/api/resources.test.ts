import { expect, test, vi } from 'vitest'
import { createHttpClient } from './http'
import { createResourceClient } from './resources'

const crewId = '22222222-2222-4222-8222-222222222222'
const resourceId = '33333333-3333-4333-8333-333333333333'
const memberId = '44444444-4444-4444-8444-444444444444'
const bookingId = '55555555-5555-4555-8555-555555555555'
const key = '66666666-6666-4666-8666-666666666666'
const resource = { id: resourceId, crew_id: crewId, name: '摄影机', resource_type: 'equipment', unit: '台', total_quantity: 2, status: 'available', description: null,
  manager_member_id: memberId, manager_display_name: '制片', manager_lock_version: 1, review_ready: false, lock_version: 1, created_at: '2026-10-28T00:00:00Z', updated_at: '2026-10-28T00:00:00Z', allowed_actions: ['apply'] }
const reservation = { id: bookingId, crew_id: crewId, resource_id: resourceId, applicant_member_id: memberId, starts_at: '2099-01-01T09:00:00Z', ends_at: '2099-01-01T10:00:00Z', quantity: 1,
  status: 'pending', reviewed_by: null, reviewed_at: null, decision_note: null, cancelled_by: null, cancelled_at: null, cancel_reason: null, lock_version: 1,
  created_at: '2026-10-28T00:00:00Z', updated_at: '2026-10-28T00:00:00Z', allowed_actions: ['cancel'], dependency_check_status: 'not_implemented' }
const ok = (data: unknown, status = 200) => new Response(JSON.stringify({ data }), { status })
const page = (row: unknown) => ({ items: [row], total: 1, page: 1, page_size: 20 })

test('resource responses drop unrelated private fields and reject cross-crew and unsafe actions', async () => {
  const fetcher = vi.fn().mockResolvedValueOnce(ok(page({ ...resource, email: 'private@example.com' })))
    .mockResolvedValueOnce(ok(page({ ...resource, crew_id: memberId }))).mockResolvedValueOnce(ok(page({ ...resource, allowed_actions: ['root'] })))
  vi.stubGlobal('fetch', fetcher)
  const api = createResourceClient(createHttpClient())
  expect((await api.list(crewId)).items[0]).toEqual(resource)
  await expect(api.list(crewId)).rejects.toMatchObject({ code: 'INVALID_RESPONSE' })
  await expect(api.list(crewId)).rejects.toMatchObject({ code: 'INVALID_RESPONSE' })
})

test('private booking data must match the selected resource and valid review evidence', async () => {
  const fetcher = vi.fn().mockResolvedValueOnce(ok(page(reservation)))
    .mockResolvedValueOnce(ok(page({ ...reservation, resource_id: memberId })))
    .mockResolvedValueOnce(ok(page({ ...reservation, status: 'confirmed' })))
  vi.stubGlobal('fetch', fetcher); const api = createResourceClient(createHttpClient())
  expect((await api.reservations(crewId, resourceId)).items[0]).toEqual(reservation)
  await expect(api.reservations(crewId, resourceId)).rejects.toMatchObject({ code: 'INVALID_RESPONSE' })
  await expect(api.reservations(crewId, resourceId)).rejects.toMatchObject({ code: 'INVALID_RESPONSE' })
})

test('aggregate availability cannot invent physical inventory or contradictory remaining capacity', async () => {
  const range = { range_start: reservation.starts_at, range_end: reservation.ends_at }
  const capacity = { ...range, resource_id: resourceId, resource_lock_version: 1, total_quantity: 2, peak_confirmed_quantity: 1, minimum_remaining_quantity: 1, confirmed_reservation_count: 2, physical_inventory_status: 'not_implemented' }
  const fetcher = vi.fn().mockResolvedValueOnce(ok(capacity)).mockResolvedValueOnce(ok({ ...capacity, minimum_remaining_quantity: 2 })).mockResolvedValueOnce(ok({ ...capacity, physical_inventory_status: 'checked' }))
  vi.stubGlobal('fetch', fetcher); const api = createResourceClient(createHttpClient())
  expect(await api.availability(crewId, resourceId, range)).toEqual(capacity)
  await expect(api.availability(crewId, resourceId, range)).rejects.toMatchObject({ code: 'INVALID_RESPONSE' })
  await expect(api.availability(crewId, resourceId, range)).rejects.toMatchObject({ code: 'INVALID_RESPONSE' })
})

test('explicit write carries CSRF and a stable idempotency key without automatic replay', async () => {
  const fetcher = vi.fn().mockResolvedValueOnce(ok({ csrf_token: 'c'.repeat(64) })).mockRejectedValueOnce(new TypeError('lost'))
  vi.stubGlobal('fetch', fetcher)
  await expect(createResourceClient(createHttpClient()).execute(crewId, { action: 'apply', key, body: { resource_id: resourceId } })).rejects.toMatchObject({ code: 'NETWORK_UNCONFIRMED' })
  expect(fetcher).toHaveBeenCalledTimes(2)
  expect(fetcher.mock.calls[1][1].headers).toMatchObject({ 'Idempotency-Key': key, 'X-CSRF-Token': 'c'.repeat(64) })
})

test('an approval response must match expected state and next version', async () => {
  const fetcher = vi.fn().mockResolvedValueOnce(ok({ csrf_token: 'c'.repeat(64) })).mockResolvedValueOnce(ok(reservation))
  vi.stubGlobal('fetch', fetcher)
  await expect(createResourceClient(createHttpClient()).execute(crewId, { action: 'decide', reservationId: bookingId, key, body: { decision: 'accept', expected_lock_version: 1 } })).rejects.toMatchObject({ code: 'INVALID_RESPONSE' })
})

test('identifiers, keys and range are checked before requesting the network', async () => {
  const fetcher = vi.fn(); vi.stubGlobal('fetch', fetcher); const api = createResourceClient(createHttpClient())
  await expect(api.get('../auth/logout', resourceId)).rejects.toMatchObject({ code: 'INVALID_RESPONSE' })
  await expect(api.execute(crewId, { action: 'authorize', resourceId, body: {} })).rejects.toMatchObject({ code: 'VALIDATION_ERROR' })
  await expect(api.availability(crewId, resourceId, { range_start: '2026-01-01T00:00:00', range_end: '2026-01-02T00:00:00Z' })).rejects.toMatchObject({ code: 'VALIDATION_ERROR' })
  expect(fetcher).not.toHaveBeenCalled()
})

test('responsibility counts include restricted bookings without leaking their details', async () => {
  const fetcher = vi.fn().mockResolvedValueOnce(ok({ member_id: memberId, items: [], total: 0, restricted_total: 2, blocking_total: 2, page: 1, page_size: 20 }))
    .mockResolvedValueOnce(ok({ member_id: memberId, items: [], total: 0, restricted_total: 0, blocking_total: 2, page: 1, page_size: 20 }))
  vi.stubGlobal('fetch', fetcher); const api = createResourceClient(createHttpClient())
  expect((await api.responsibilities(crewId, memberId)).blocking_total).toBe(2)
  await expect(api.responsibilities(crewId, memberId)).rejects.toMatchObject({ code: 'INVALID_RESPONSE' })
})
