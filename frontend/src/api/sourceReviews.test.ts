import { expect, test, vi } from 'vitest'
import { createSourceReviewClient } from './sourceReviews'
import { createHttpClient } from './http'

test('source reviews send the exact audited basis with CSRF and original idempotency key', async () => {
  const fetcher = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({ data: { csrf_token: 'c'.repeat(64) } })))
    .mockResolvedValueOnce(new Response(JSON.stringify({ data: { id: 'target', status: 'still_applicable' } }), { status: 201 }))
  vi.stubGlobal('fetch', fetcher)
  const body = { expected_lock_version: 4, expected_working_version_id: 'working', expected_working_revision: 3, expected_review_id: null, conclusion: 'still_applicable' as const, review_note: '已核对第2段' }
  await createSourceReviewClient(createHttpClient()).reviewSource('crew', 'breakdowns', 'target', body, 'original-key')
  const [url, options] = fetcher.mock.calls[1]
  expect(url).toBe('/api/v1/crews/crew/breakdowns/target/source-reviews')
  expect(options.method).toBe('POST'); expect(JSON.parse(options.body)).toEqual(body)
  expect(options.headers['Idempotency-Key']).toBe('original-key')
})
