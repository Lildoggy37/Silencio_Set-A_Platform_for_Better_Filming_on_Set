import { expect, test, vi } from 'vitest'
import { createHttpClient } from './http'
import { createCastingClient } from './casting'

test('casting writes use PATCH with original CSRF and idempotency key', async () => {
  const fetcher = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({ data: { csrf_token: 'c'.repeat(64) } })))
    .mockResolvedValueOnce(new Response(JSON.stringify({ data: { script_id: 'script', lock_version: 2 } })))
  vi.stubGlobal('fetch', fetcher)
  const body = { expected_lock_version: 1, character_name: '林雨', member_id: 'actor', membership_joined_at: '2026-11-03T00:00:00Z' }
  await createCastingClient(createHttpClient()).saveCasting('crew', 'script', body, 'same-key')
  const [url, options] = fetcher.mock.calls[1]
  expect(url).toBe('/api/v1/crews/crew/scripts/script/casting')
  expect(options.method).toBe('PATCH')
  expect(JSON.parse(options.body)).toEqual(body)
  expect(options.headers['X-CSRF-Token']).toBe('c'.repeat(64))
  expect(options.headers['Idempotency-Key']).toBe('same-key')
})
