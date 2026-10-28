import { expect, test, vi } from 'vitest'
import { createHttpClient } from './http'
import { createCrewClient } from './crews'

const id = '22222222-2222-4222-8222-222222222222'
const ok = (data: unknown, status = 200) => new Response(JSON.stringify({ data }), { status })
const member = { id, display_name: '导演', status: 'active', role_codes: ['admin'], lock_version: 1 }

test('directory parsing discards account-private fields and rejects unsupported roles', async () => {
  const fetcher = vi.fn().mockResolvedValueOnce(ok({ items: [{ ...member, email: 'private@example.com' }], page: 1, page_size: 20, total: 1 }))
    .mockResolvedValueOnce(ok({ items: [{ ...member, role_codes: ['root'] }], page: 1, page_size: 20, total: 1 }))
  vi.stubGlobal('fetch', fetcher)
  const api = createCrewClient(createHttpClient())
  expect((await api.members(id)).items[0]).toEqual(member)
  await expect(api.members(id)).rejects.toMatchObject({ code: 'INVALID_RESPONSE' })
})

test('invalid crew identifiers cannot escape the fixed API namespace', async () => {
  const fetcher = vi.fn(); vi.stubGlobal('fetch', fetcher)
  const api = createCrewClient(createHttpClient())
  await expect(api.get('../auth/logout')).rejects.toMatchObject({ code: 'INVALID_RESPONSE' })
  await expect(api.list(0)).rejects.toMatchObject({ code: 'VALIDATION_ERROR' })
  expect(fetcher).not.toHaveBeenCalled()
})

test('creation never retries an uncertain write', async () => {
  const fetcher = vi.fn().mockResolvedValueOnce(ok({ csrf_token: 'c'.repeat(64) })).mockRejectedValueOnce(new TypeError('offline'))
  vi.stubGlobal('fetch', fetcher)
  await expect(createCrewClient(createHttpClient()).create({ name: '组', project_name: '片', description: null })).rejects.toMatchObject({ code: 'NETWORK_UNCONFIRMED' })
  expect(fetcher).toHaveBeenCalledTimes(2)
})

test('role replacement uses PUT, the shared CSRF and expected version without replay', async () => {
  const fetcher = vi.fn().mockResolvedValueOnce(ok({ csrf_token: 'c'.repeat(64) })).mockResolvedValueOnce(ok({ ...member, role_codes: ['admin', 'producer'], lock_version: 2 }))
  vi.stubGlobal('fetch', fetcher)
  await createCrewClient(createHttpClient()).replaceRoles(id, id, ['producer', 'admin'], 1)
  const [url, options] = fetcher.mock.calls[1]
  expect(url).toBe(`/api/v1/crews/${id}/members/${id}/roles`)
  expect(options.method).toBe('PUT')
  expect(options.headers['X-CSRF-Token']).toBe('c'.repeat(64))
  expect(JSON.parse(options.body)).toEqual({ role_codes: ['admin', 'producer'], expected_lock_version: 1 })
})

test('role response must confirm the requested member, new version and exact role set', async () => {
  const fetcher = vi.fn().mockResolvedValueOnce(ok({ csrf_token: 'c'.repeat(64) })).mockResolvedValueOnce(ok({ ...member, role_codes: ['admin'], lock_version: 2 }))
  vi.stubGlobal('fetch', fetcher)
  await expect(createCrewClient(createHttpClient()).replaceRoles(id, id, ['producer', 'admin'], 1)).rejects.toMatchObject({ code: 'INVALID_RESPONSE' })
  expect(fetcher).toHaveBeenCalledTimes(2)
})

test('self exit submits only the expected version and validates the ended membership', async () => {
  const fetcher = vi.fn().mockResolvedValueOnce(ok({ csrf_token: 'c'.repeat(64) })).mockResolvedValueOnce(ok({
    membership: { ...member, status: 'left', role_codes: [], lock_version: 2 }, ended_at: '2026-10-28T01:00:00Z',
  }))
  vi.stubGlobal('fetch', fetcher)
  expect(await createCrewClient(createHttpClient()).leave(id, id, 1)).toMatchObject({ memberId: id, status: 'left', lockVersion: 2 })
  expect(fetcher.mock.calls[1][0]).toBe(`/api/v1/crews/${id}/members/me/leave`)
  expect(JSON.parse(fetcher.mock.calls[1][1].body)).toEqual({ expected_lock_version: 1 })
})

test('removal cannot report success if old authority or an unexpected version is returned', async () => {
  const fetcher = vi.fn().mockResolvedValueOnce(ok({ csrf_token: 'c'.repeat(64) })).mockResolvedValueOnce(ok({
    membership: { ...member, status: 'removed', lock_version: 2 }, ended_at: '2026-10-28T01:00:00Z',
  }))
  vi.stubGlobal('fetch', fetcher)
  await expect(createCrewClient(createHttpClient()).remove(id, id, 1, ' 工作调整 ')).rejects.toMatchObject({ code: 'INVALID_RESPONSE' })
  expect(JSON.parse(fetcher.mock.calls[1][1].body)).toEqual({ expected_lock_version: 1, reason: '工作调整' })
  expect(fetcher).toHaveBeenCalledTimes(2)
})

test('invalid removal reason is rejected before sending a credentialed request', async () => {
  const fetcher = vi.fn(); vi.stubGlobal('fetch', fetcher)
  const api = createCrewClient(createHttpClient())
  await expect(api.remove(id, id, 1, '   ')).rejects.toMatchObject({ code: 'VALIDATION_ERROR' })
  await expect(api.remove(id, id, 1, 'bad\nreason')).rejects.toMatchObject({ code: 'VALIDATION_ERROR' })
  expect(fetcher).not.toHaveBeenCalled()
})
