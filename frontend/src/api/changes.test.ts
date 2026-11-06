import { expect,test,vi } from 'vitest'
import { createHttpClient } from './http'
import { createChangeClient,changeCategories } from './changes'

test('change issue reads preserve category and pagination without CSRF or writes',async () => {
  const fetcher=vi.fn().mockResolvedValue(new Response(JSON.stringify({data:{items:[],total:0,page:2,page_size:20}})))
  vi.stubGlobal('fetch',fetcher)
  await createChangeClient(createHttpClient()).list('crew','plans',2)
  expect(fetcher).toHaveBeenCalledTimes(1)
  const [url,options]=fetcher.mock.calls[0]
  expect(url).toBe('/api/v1/crews/crew/change-issues/plans?page=2&page_size=20')
  expect(options.method??'GET').toBe('GET')
  expect(changeCategories(['plan.read_related','member.authorize'])).toEqual([])
})

test('explicit checks send an empty body and preserve the original CSRF idempotent request',async () => {
  const fetcher=vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({data:{csrf_token:'c'.repeat(64)}})))
    .mockResolvedValueOnce(new Response(JSON.stringify({data:{id:'check',open_count:1}}),{status:201}))
  vi.stubGlobal('fetch',fetcher)
  await createChangeClient(createHttpClient()).check('crew','plans','same-key')
  const [url,options]=fetcher.mock.calls[1]
  expect(url).toBe('/api/v1/crews/crew/change-issues/plans/checks')
  expect(options.method).toBe('POST'); expect(JSON.parse(options.body)).toEqual({})
  expect(options.headers['Idempotency-Key']).toBe('same-key')
})
