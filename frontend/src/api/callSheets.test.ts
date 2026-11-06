import { expect, test, vi } from 'vitest'
import { createCallSheetClient } from './callSheets'
import { createHttpClient } from './http'

test('PDF export is a private version-bound GET returning binary PDF without JSON envelope', async () => {
  const fetcher = vi.fn().mockResolvedValueOnce(new Response('%PDF-example', { headers: { 'Content-Type': 'application/pdf' } }))
  vi.stubGlobal('fetch', fetcher)
  const blob = await createCallSheetClient(createHttpClient()).exportPdf('crew', 'version', 4)
  expect(blob.type).toBe('application/pdf')
  expect(await blob.text()).toBe('%PDF-example')
  expect(fetcher.mock.calls[0][0]).toBe('/api/v1/crews/crew/call-sheet-versions/version/export.pdf?expected_lock_version=4')
  expect(fetcher.mock.calls[0][1]).toMatchObject({ method: 'GET', credentials: 'same-origin', cache: 'no-store' })
  expect(fetcher).toHaveBeenCalledTimes(1)
})

test('PDF export handles permission and version errors as JSON without treating them as files', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({ error: { code: 'FORBIDDEN' } }), { status: 403 }))
    .mockResolvedValueOnce(new Response(JSON.stringify({ error: { code: 'VERSION_CONFLICT' } }), { status: 409 })))
  const api = createCallSheetClient(createHttpClient())
  await expect(api.exportPdf('crew', 'version', 4)).rejects.toMatchObject({ code: 'FORBIDDEN' })
  await expect(api.exportPdf('crew', 'version', 4)).rejects.toMatchObject({ code: 'VERSION_CONFLICT' })
})

test('successful JSON from a broken export endpoint is not downloaded as PDF', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({ data: {} }), { headers: { 'Content-Type': 'application/json' } })))
  await expect(createCallSheetClient(createHttpClient()).exportPdf('crew', 'version', 4)).rejects.toMatchObject({ code: 'INVALID_RESPONSE' })
})
