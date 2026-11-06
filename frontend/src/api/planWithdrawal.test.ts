import { expect, test, vi } from 'vitest'
import { createPlanClient } from './plans'
import { createHttpClient } from './http'

test('withdrawal binds the source lock and exact reason to the CSRF idempotent POST', async () => {
  const fetcher = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({ data: { csrf_token: 'c'.repeat(64) } })))
    .mockResolvedValueOnce(new Response(JSON.stringify({ data: { withdrawn_version_id: 'version', lock_version: 4 } })))
  vi.stubGlobal('fetch', fetcher)
  await createPlanClient(createHttpClient()).withdraw('crew', 'version', { expected_lock_version: 3, reason: '场地关闭' }, 'same-key')
  const [url, options] = fetcher.mock.calls[1]
  expect(url).toBe('/api/v1/crews/crew/plan-versions/version/withdraw')
  expect(JSON.parse(options.body)).toEqual({ expected_lock_version: 3, reason: '场地关闭' })
  expect(options.method).toBe('POST'); expect(options.headers['Idempotency-Key']).toBe('same-key')
})
