import { vi, expect, test } from 'vitest'
import { fetchHealth } from './health'

const dependencies = { database: 'ok', redis: 'not_configured', rabbitmq: 'unavailable' }

test('parses dependency failure without disguising 503 as success', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
    error: { code: 'DEPENDENCIES_NOT_READY', details: { dependencies } }, request_id: 'req-test',
  }), { status: 503 })))
  expect(await fetchHealth('ready')).toEqual({ ready: false, dependencies, requestId: 'req-test' })
})

test('rejects malformed dependency state', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
    data: { status: 'ready', dependencies: { database: 'fake' } }, request_id: 'req-test',
  }))))
  await expect(fetchHealth('ready')).rejects.toThrow('响应格式')
})

test('rejects unexpected response without displaying arbitrary server messages', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
    error: { message: 'private-server-detail' }, request_id: 'req-test',
  }), { status: 500 })))
  await expect(fetchHealth('live')).rejects.toThrow('服务暂不可用')
})

test('propagates network failure rather than using fake fallback data', async () => {
  vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Network error')))
  await expect(fetchHealth('live')).rejects.toThrow('Network error')
})
