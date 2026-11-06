import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, expect, test, vi } from 'vitest'
import { ScriptLibrary } from './ScriptLibrary'
import { AccountError } from '../api/accounts'

const api = vi.hoisted(() => ({ list: vi.fn(), get: vi.fn(), versions: vi.fn(), execute: vi.fn(), importFile: vi.fn() }))
vi.mock('../auth/AuthProvider', () => ({ useAuth: () => ({ scripts: api, refresh: vi.fn() }) }))
const script = { id: 'script-1', crew_id: 'crew', title: '雨停之前', current_version_id: null, lock_version: 2, created_at: '', updated_at: '' }
const version = { id: 'version-1', script_id: script.id, version_no: 1, status: 'pending_check', lock_version: 1, confirmed_by: null, confirmed_at: null, source_file: null, paragraphs: [{ id: 'p-1', paragraph_no: 1, text: '走廊。雨停了。', source_page: null }] }
beforeEach(() => {
  vi.resetAllMocks()
  api.list.mockResolvedValue({ items: [script], total: 1, page: 1, page_size: 20 })
  api.versions.mockResolvedValue({ items: [version], total: 1, page: 1, page_size: 20 })
  api.get.mockImplementation((_crew, _id, versionId) => Promise.resolve({ script, version: versionId ? version : null }))
  api.execute.mockResolvedValue({ script, version })
})
function start(permissions = ['script.read', 'script.edit', 'script.confirm']) {
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })}><ScriptLibrary crewId="crew" userId="user" permissions={permissions} /></QueryClientProvider>)
}
async function openVersion() {
  fireEvent.click(await screen.findByRole('button', { name: '雨停之前' }))
  fireEvent.click(await screen.findByRole('button', { name: '版本 1 · 待核对' }))
  await screen.findByText('走廊。雨停了。')
}

test('a linked script opens its working draft even if absent from the first library page', async () => {
  api.list.mockResolvedValue({ items: [], total: 0, page: 1, page_size: 20 })
  api.get.mockResolvedValue({ script: { ...script, id: 'hidden-script', title: '后页剧本' }, version: null })
  api.versions.mockResolvedValue({ items: [], total: 0, page: 1, page_size: 20 })
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })}><ScriptLibrary crewId="crew" userId="user" permissions={['script.read']} initialScriptId="hidden-script" /></QueryClientProvider>)
  await screen.findByRole('heading', { name: '后页剧本' })
  expect(api.get).toHaveBeenCalledWith('crew', 'hidden-script', undefined)
})
test('readers can read paragraphs without editing or confirmation controls', async () => {
  start(['script.read']); await openVersion()
  expect(screen.queryByRole('button', { name: '创建剧本' })).toBeNull()
  expect(screen.queryByRole('button', { name: '修正正文' })).toBeNull()
  expect(screen.queryByRole('button', { name: '确认正文' })).toBeNull()
  expect(screen.queryByRole('button', { name: '导入文件新版本' })).toBeNull()
})
test('confirming text is explicit and never sends a working-version change', async () => {
  start(); await openVersion(); fireEvent.click(screen.getByRole('button', { name: '确认正文' }))
  const editor = within(screen.getByRole('button', { name: '核对剧本操作' }).closest('[role="dialog"]') as HTMLElement)
  fireEvent.click(editor.getByRole('button', { name: '核对剧本操作' }))
  expect(api.execute).not.toHaveBeenCalled()
  expect(editor.getByText('确认后本版本正文不可覆盖；此操作不会自动切换工作版本。')).toBeTruthy()
  fireEvent.click(editor.getByRole('button', { name: '确认剧本操作' }))
  await waitFor(() => expect(api.execute).toHaveBeenCalledTimes(1))
  expect(api.execute.mock.calls[0][1]).toMatchObject({ action: 'confirm', versionId: 'version-1', body: { expected_lock_version: 1 } })
})
test('text correction preserves the exact input and key after an unknown result', async () => {
  api.execute.mockRejectedValueOnce(new AccountError('NETWORK_UNCONFIRMED'))
  start(); await openVersion(); fireEvent.click(screen.getByRole('button', { name: '修正正文' }))
  const editor = within(screen.getByLabelText('剧本正文').closest('[role="dialog"]') as HTMLElement)
  fireEvent.change(editor.getByLabelText('剧本正文'), { target: { value: '第一场\n新的台词。' } })
  fireEvent.click(editor.getByRole('button', { name: '核对剧本操作' })); fireEvent.click(editor.getByRole('button', { name: '确认剧本操作' }))
  const retry = await editor.findByRole('button', { name: '用原剧本请求重试' })
  await waitFor(() => expect(retry.classList.contains('ant-btn-loading')).toBe(false))
  fireEvent.click(retry)
  await waitFor(() => expect(api.execute).toHaveBeenCalledTimes(2))
  expect(api.execute.mock.calls[0][1]).toEqual(api.execute.mock.calls[1][1])
  expect(api.execute.mock.calls[0][1].body.text).toBe('第一场\n新的台词。')
})
test('confirmed versions expose a separate working-version action and cannot be corrected', async () => {
  api.get.mockResolvedValue({ script, version: { ...version, status: 'confirmed', lock_version: 2 } })
  start(); fireEvent.click(await screen.findByRole('button', { name: '雨停之前' }))
  fireEvent.click(await screen.findByRole('button', { name: '设为工作版本' }))
  expect(screen.queryByRole('button', { name: '修正正文' })).toBeNull()
  const editor = within(screen.getByRole('button', { name: '核对剧本操作' }).closest('[role="dialog"]') as HTMLElement)
  fireEvent.click(editor.getByRole('button', { name: '核对剧本操作' })); fireEvent.click(editor.getByRole('button', { name: '确认剧本操作' }))
  await waitFor(() => expect(api.execute).toHaveBeenCalledTimes(1))
  expect(api.execute.mock.calls[0][1]).toMatchObject({ action: 'set_working', body: { expected_lock_version: 2, version_id: 'version-1' } })
})

async function prepareImport() {
  fireEvent.click(screen.getByRole('button', { name: '导入文件新版本' }))
  const file = new File(['剧本正文'], '雨停之前.docx', { type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' })
  fireEvent.change(screen.getByLabelText('剧本文件'), { target: { files: [file] } })
  fireEvent.click(screen.getByRole('button', { name: '核对导入文件' }))
  return file
}

test('file import requires explicit confirmation and only creates a pending version', async () => {
  api.importFile.mockResolvedValue({ script, version })
  start(); await openVersion()
  const file = await prepareImport()
  expect(api.importFile).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: '确认导入剧本' }))
  await waitFor(() => expect(api.importFile).toHaveBeenCalledTimes(1))
  expect(api.importFile.mock.calls[0].slice(0, 4)).toEqual(['crew', 'script-1', file, 2])
  expect(api.execute).not.toHaveBeenCalled()
})

test('unknown import outcome retains the original file version and request key for manual retry', async () => {
  api.importFile.mockRejectedValueOnce(new AccountError('NETWORK_UNCONFIRMED')).mockResolvedValueOnce({ script, version })
  start(); await openVersion(); await prepareImport()
  fireEvent.click(screen.getByRole('button', { name: '确认导入剧本' }))
  const retry = await screen.findByRole('button', { name: '用原文件导入请求重试' })
  expect(api.importFile).toHaveBeenCalledTimes(1)
  await waitFor(() => expect(retry.classList.contains('ant-btn-loading')).toBe(false))
  fireEvent.click(retry)
  await waitFor(() => expect(api.importFile).toHaveBeenCalledTimes(2))
  expect(api.importFile.mock.calls[1]).toEqual(api.importFile.mock.calls[0])
})

test('version conflict reload keeps the selected file and updates import premise', async () => {
  api.importFile.mockRejectedValueOnce(new AccountError('VERSION_CONFLICT', 409)).mockResolvedValueOnce({ script, version })
  start(); await openVersion()
  const file = await prepareImport()
  fireEvent.click(screen.getByRole('button', { name: '确认导入剧本' }))
  api.get.mockResolvedValue({ script: { ...script, lock_version: 5 }, version })
  fireEvent.click(await screen.findByRole('button', { name: '读取最新剧本并保留文件' }))
  await waitFor(() => expect(screen.queryByRole('button', { name: '读取最新剧本并保留文件' })).toBeNull())
  expect(screen.getByText('已选：雨停之前.docx')).toBeTruthy()
  expect((screen.getByLabelText('剧本文件') as HTMLInputElement).required).toBe(false)
  fireEvent.click(screen.getByRole('button', { name: '核对导入文件' }))
  fireEvent.click(screen.getByRole('button', { name: '确认导入剧本' }))
  await waitFor(() => expect(api.importFile).toHaveBeenCalledTimes(2))
  expect(api.importFile.mock.calls[1].slice(0, 4)).toEqual(['crew', 'script-1', file, 5])
})

test('imported version shows source pages warnings and private original download', async () => {
  api.get.mockResolvedValue({ script, version: { ...version,
    source_file: { id: 'file', original_name: '原剧本.pdf', file_format: 'pdf', byte_size: 100, page_count: 2, extraction_notes: ['第2页未提取到文字，请对照原文核对。'] },
    paragraphs: [{ ...version.paragraphs[0], source_page: 1 }] } })
  start(); fireEvent.click(await screen.findByRole('button', { name: '雨停之前' }))
  expect(await screen.findByText('· 原文件第 1 页')).toBeTruthy()
  expect(screen.getByText('第2页未提取到文字，请对照原文核对。')).toBeTruthy()
  expect(screen.getByRole('link', { name: '下载原始文件' }).getAttribute('href')).toBe('/api/v1/crews/crew/scripts/script-1/versions/version-1/source-file')
  fireEvent.click(screen.getByRole('button', { name: '修正正文' }))
  expect(screen.getByText('修正后保留原始文件供对照，正文段落的原页码标注会取消。')).toBeTruthy()
})
