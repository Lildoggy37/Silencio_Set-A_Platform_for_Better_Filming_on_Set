import { expect, test, vi } from 'vitest'
import { createHttpClient } from './http'
import { createTaskClient } from './tasks'

const crewId = '22222222-2222-4222-8222-222222222222'
const taskId = '33333333-3333-4333-8333-333333333333'
const memberId = '44444444-4444-4444-8444-444444444444'
const key = '55555555-5555-4555-8555-555555555555'
const task = { id: taskId, crew_id: crewId, title: '分镜整理', description: null, owner_member_id: memberId, reviewer_member_id: memberId, created_by_member_id: memberId,
  collaborator_member_ids: [], due_at: '2099-01-01T00:00:00Z', status: 'pending', cancel_reason: null, lock_version: 1, created_at: '2026-10-28T00:00:00Z', updated_at: '2026-10-28T00:00:00Z', allowed_actions: ['start'] }
const ok = (data: unknown, status = 200) => new Response(JSON.stringify({ data }), { status })

test('task parsing rejects cross-crew data and drops unapproved response fields', async () => {
  const fetcher = vi.fn().mockResolvedValueOnce(ok({ items: [{ ...task, email: 'private@example.com' }], total: 1, page: 1, page_size: 20 }))
    .mockResolvedValueOnce(ok({ items: [{ ...task, crew_id: memberId }], total: 1, page: 1, page_size: 20 }))
  vi.stubGlobal('fetch', fetcher)
  const api = createTaskClient(createHttpClient())
  expect((await api.list(crewId)).items[0]).toEqual(task)
  await expect(api.list(crewId)).rejects.toMatchObject({ code: 'INVALID_RESPONSE' })
})

test('a keyed write carries CSRF and the same explicit key without automatic replay', async () => {
  const fetcher = vi.fn().mockResolvedValueOnce(ok({ csrf_token: 'c'.repeat(64) })).mockRejectedValueOnce(new TypeError('lost'))
  vi.stubGlobal('fetch', fetcher)
  await expect(createTaskClient(createHttpClient()).execute(crewId, { action: 'submit', taskId, key, body: { completion_note: '完成', expected_lock_version: 1 } })).rejects.toMatchObject({ code: 'NETWORK_UNCONFIRMED' })
  expect(fetcher).toHaveBeenCalledTimes(2)
  expect(fetcher.mock.calls[1][1].headers).toMatchObject({ 'Idempotency-Key': key, 'X-CSRF-Token': 'c'.repeat(64) })
})

test('a write must return the expected status and version before success is accepted', async () => {
  const fetcher = vi.fn().mockResolvedValueOnce(ok({ csrf_token: 'c'.repeat(64) })).mockResolvedValueOnce(ok({ ...task, lock_version: 2 }))
  vi.stubGlobal('fetch', fetcher)
  await expect(createTaskClient(createHttpClient()).execute(crewId, { action: 'start', taskId, body: { expected_lock_version: 1 } })).rejects.toMatchObject({ code: 'INVALID_RESPONSE' })
})

test('unsafe identifiers and absent keys fail before network access', async () => {
  const fetcher = vi.fn(); vi.stubGlobal('fetch', fetcher)
  const api = createTaskClient(createHttpClient())
  await expect(api.get('../auth/logout', taskId)).rejects.toMatchObject({ code: 'INVALID_RESPONSE' })
  await expect(api.execute(crewId, { action: 'create', body: {} })).rejects.toMatchObject({ code: 'VALIDATION_ERROR' })
  expect(fetcher).not.toHaveBeenCalled()
})
