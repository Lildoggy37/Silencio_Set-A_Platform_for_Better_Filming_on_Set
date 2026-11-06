import { expect, test, vi } from 'vitest'
import { createHttpClient } from './http'
import { createLocationClient, locationPhotoUrl } from './locations'

test('photo upload sends browser multipart with CSRF key resource version and description', async () => {
  const fetcher = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({ data: { csrf_token: 'c'.repeat(64) } })))
    .mockResolvedValueOnce(new Response(JSON.stringify({ data: {} }), { status: 201 }))
  vi.stubGlobal('fetch', fetcher)
  const file = new File(['image'], 'scout.png', { type: 'image/png' })
  await createLocationClient(createHttpClient()).uploadPhoto('crew', 'resource', file, '入口', 3, 'photo-key')
  const [path, options] = fetcher.mock.calls[1]
  expect(path).toBe('/api/v1/crews/crew/locations/resource/photos')
  expect(options.body.get('file')).toBe(file)
  expect(options.body.get('description')).toBe('入口')
  expect(options.body.get('expected_lock_version')).toBe('3')
  expect(options.headers['Content-Type']).toBeUndefined()
  expect(options.headers['X-CSRF-Token']).toBe('c'.repeat(64))
  expect(options.headers['Idempotency-Key']).toBe('photo-key')
})

test('photo removal is a scoped keyed JSON mutation and image URLs contain no credentials', async () => {
  const fetcher = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({ data: { csrf_token: 'c'.repeat(64) } })))
    .mockResolvedValueOnce(new Response(JSON.stringify({ data: {} })))
  vi.stubGlobal('fetch', fetcher)
  await createLocationClient(createHttpClient()).locationWrite('crew', 'resource', { action: 'remove_photo', photoId: 'photo', body: { expected_lock_version: 4 }, key: 'remove-key' })
  expect(fetcher.mock.calls[1][0]).toBe('/api/v1/crews/crew/locations/resource/photos/photo/remove')
  expect(fetcher.mock.calls[1][1].method).toBe('POST')
  expect(JSON.parse(fetcher.mock.calls[1][1].body)).toEqual({ expected_lock_version: 4 })
  expect(locationPhotoUrl('crew', 'resource', 'photo')).toBe('/api/v1/crews/crew/locations/resource/photos/photo/content?variant=preview')
})
