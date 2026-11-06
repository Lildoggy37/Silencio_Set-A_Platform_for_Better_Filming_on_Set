import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, expect, test, vi } from 'vitest'
import { CreativePanel } from './CreativePanel'
import { AccountError } from '../api/accounts'
const api = vi.hoisted(() => ({ list: vi.fn(), get: vi.fn(), reviews: vi.fn(), write: vi.fn() }))
const resources = vi.hoisted(() => ({ list: vi.fn(), stocks: vi.fn() }))
vi.mock('../auth/AuthProvider', () => ({ useAuth: () => ({ creative: api, resources, refresh: vi.fn() }) }))
const row = { id: 'breakdown-1', kind: 'breakdowns', source_id: 'source-1', version_no: 1, review_status: 'draft', lock_version: 1,
  reviewed_by: null, reviewed_at: null, items: [{ id: 'revision-1', scene_id: 'scene-1', scene_number: '01', location_text: '医院走廊', time_of_day: 'day', source_start: 1, source_end: 2, production_note: null }] }
beforeEach(() => {
  vi.resetAllMocks()
  api.list.mockResolvedValue({ items: [row], total: 1, page: 1, page_size: 20 })
  api.get.mockResolvedValue(row)
  api.reviews.mockResolvedValue({ items: [], total: 0, page: 1, page_size: 20 })
  api.write.mockResolvedValue({ ...row, lock_version: 2 })
})
function start(permissions = ['script.read', 'scene.read', 'scene.edit', 'scene.review'], kind: 'breakdowns' | 'storyboards' = 'breakdowns') {
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })}><CreativePanel crewId="crew" userId="user" sourceId="source-1" permissions={permissions} kind={kind} /></QueryClientProvider>)
}
async function open() { fireEvent.click(await screen.findByRole('button', { name: '第 1 版 · 草稿' })); await screen.findByLabelText('场次编号') }
test('scene editing preserves stable scene identity and source paragraphs in the saved payload', async () => {
  start(); await open()
  fireEvent.change(screen.getByLabelText('剧情地点'), { target: { value: '教学楼走廊' } })
  expect(screen.getByRole('button', { name: '提交审核' }).hasAttribute('disabled')).toBe(true)
  expect(screen.getByRole('button', { name: '新建场戏拆解' }).hasAttribute('disabled')).toBe(true)
  fireEvent.click(screen.getByRole('button', { name: '保存草稿' }))
  expect(api.write).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: '确认创作操作' }))
  await waitFor(() => expect(api.write).toHaveBeenCalledTimes(1))
  expect(api.write.mock.calls[0].slice(0, 4)).toEqual(['crew', 'breakdowns', 'save', 'breakdown-1'])
  expect(api.write.mock.calls[0][4]).toMatchObject({ expected_lock_version: 1, scenes: [{ scene_id: 'scene-1', scene_number: '01', location_text: '教学楼走廊', source_start: 1, source_end: 2 }] })
})
test('readers cannot edit, submit or review scene content', async () => {
  start(['script.read', 'scene.read']); await open()
  expect((screen.getByLabelText('剧情地点').closest('fieldset') as HTMLFieldSetElement).disabled).toBe(true)
  for (const name of ['保存草稿', '提交审核', '新建场戏拆解']) expect(screen.queryByRole('button', { name })).toBeNull()
})

test('storyboard creation references the selected confirmed scene revision', async () => {
  api.get.mockResolvedValue({ ...row, review_status: 'confirmed' })
  api.list.mockImplementation((_crew, kind) => Promise.resolve({ items: kind === 'breakdowns' ? [row] : [], total: kind === 'breakdowns' ? 1 : 0, page: 1, page_size: 20 }))
  start(['script.read', 'scene.read', 'scene.edit', 'storyboard.read', 'storyboard.edit']); await open()
  fireEvent.click(screen.getByRole('button', { name: '场戏 01 的分镜' }))
  await waitFor(() => expect(api.list).toHaveBeenCalledWith('crew', 'storyboards', 'revision-1', 1, expect.any(AbortSignal)))
  fireEvent.click(screen.getByRole('button', { name: '新建文字分镜' }))
  fireEvent.click(screen.getByRole('button', { name: '确认创作操作' }))
  await waitFor(() => expect(api.write).toHaveBeenCalledTimes(1))
  expect(api.write.mock.calls[0].slice(0, 5)).toEqual(['crew', 'storyboards', 'create', null, { source_id: 'revision-1' }])
})

test('review history shows the old production note instead of the edited current note', async () => {
  api.reviews.mockResolvedValue({ items: [{ id: 'review-1', decision: 'reject', review_note: '需要调整', reviewed_lock_version: 2, content_snapshot: { ...row, items: [{ ...row.items[0], production_note: '当时的布光方案' }] } }], total: 1, page: 1, page_size: 20 })
  start(); await open()
  fireEvent.click(screen.getByRole('button', { name: '审核历史' }))
  expect(await screen.findByText('日夜：day · 制作备注：当时的布光方案')).toBeTruthy()
  expect((screen.getByLabelText('制作备注') as HTMLTextAreaElement).value).toBe('')
})
test('review rejection is explicit and records the reason', async () => {
  api.get.mockResolvedValue({ ...row, review_status: 'pending_review' })
  start(); await open()
  fireEvent.change(screen.getByLabelText('审核决定'), { target: { value: 'reject' } })
  fireEvent.change(screen.getByLabelText('审核说明'), { target: { value: '请补充原文范围' } })
  fireEvent.click(screen.getByRole('button', { name: '核对审核决定' }))
  expect(api.write).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: '确认创作操作' }))
  await waitFor(() => expect(api.write).toHaveBeenCalledTimes(1))
  expect(api.write.mock.calls[0][4]).toEqual({ expected_lock_version: 1, decision: 'reject', review_note: '请补充原文范围' })
})
test('confirmed breakdowns allow a new draft without exposing content editing', async () => {
  api.get.mockResolvedValue({ ...row, review_status: 'confirmed' })
  start(); await open()
  expect(screen.queryByRole('button', { name: '保存草稿' })).toBeNull()
  expect(screen.queryByRole('button', { name: '重新打开' })).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: '复制为新草稿' })); fireEvent.click(screen.getByRole('button', { name: '确认创作操作' }))
  await waitFor(() => expect(api.write).toHaveBeenCalledTimes(1))
  expect(api.write.mock.calls[0][4]).toEqual({ source_id: 'source-1', copy_from: 'breakdown-1' })
})
test('unknown outcomes retain the original request key and do not automatically retry', async () => {
  api.write.mockRejectedValueOnce(new AccountError('NETWORK_UNCONFIRMED'))
  start(); await open(); fireEvent.click(screen.getByRole('button', { name: '提交审核' })); fireEvent.click(screen.getByRole('button', { name: '确认创作操作' }))
  const retry = await screen.findByRole('button', { name: '用原创作请求重试' })
  expect(api.write).toHaveBeenCalledTimes(1)
  await waitFor(() => expect(retry.classList.contains('ant-btn-loading')).toBe(false))
  fireEvent.click(retry)
  await waitFor(() => expect(api.write).toHaveBeenCalledTimes(2))
  expect(api.write.mock.calls[0]).toEqual(api.write.mock.calls[1])
})
test('storyboard editing saves ordered textual shots including duration', async () => {
  const board = { ...row, kind: 'storyboards', items: [{ shot_number: '10', shot_size: '全景', visual_content: '雨后的走廊', duration_seconds: 8, sort_order: 1 }] }
  api.list.mockResolvedValue({ items: [board], total: 1, page: 1, page_size: 20 }); api.get.mockResolvedValue(board)
  start(['script.read', 'storyboard.read', 'storyboard.edit'], 'storyboards')
  fireEvent.click(await screen.findByRole('button', { name: '第 1 版 · 草稿' }))
  fireEvent.change(await screen.findByLabelText('画面内容'), { target: { value: '角色走入走廊' } })
  fireEvent.click(screen.getByRole('button', { name: '保存草稿' })); fireEvent.click(screen.getByRole('button', { name: '确认创作操作' }))
  await waitFor(() => expect(api.write).toHaveBeenCalledTimes(1))
  expect(api.write.mock.calls[0][4]).toMatchObject({ shots: [{ shot_number: '10', shot_size: '全景', visual_content: '角色走入走廊', duration_seconds: 8 }] })
})

test('scene preparation saves character names and unbound resource requirements in the same draft', async () => {
  start(); await open()
  fireEvent.click(screen.getByRole('button', { name: '添加出场人物' }))
  fireEvent.change(screen.getByLabelText('人物名称 1'), { target: { value: '林雨' } })
  fireEvent.click(screen.getByRole('button', { name: '添加资源需求' }))
  fireEvent.change(screen.getByLabelText('需求名称 1'), { target: { value: '摄影机' } })
  fireEvent.change(screen.getByLabelText('需求数量 1'), { target: { value: '2' } })
  expect(screen.getByText('待选资源')).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: '保存草稿' }))
  fireEvent.click(screen.getByRole('button', { name: '确认创作操作' }))
  await waitFor(() => expect(api.write).toHaveBeenCalledTimes(1))
  expect(api.write.mock.calls[0][4]).toMatchObject({ scenes: [{ characters: [{ name: '林雨' }], resource_needs: [{ resource_type: 'equipment', name: '摄影机', quantity: 2, unit: '台', resource_id: null }] }] })
})

test('resource picker excludes disabled and mismatched choices and carries the selected unit', async () => {
  const camera = { id: 'camera-1', name: 'A机', resource_type: 'equipment', unit: '套', status: 'available' }
  resources.list.mockResolvedValue({ items: [camera, { ...camera, id: 'disabled', name: '停用机', status: 'disabled' }, { ...camera, id: 'location', name: '教学楼', resource_type: 'location' }], total: 3, page: 1, page_size: 20 })
  start(['script.read', 'scene.read', 'scene.edit', 'resource.read']); await open()
  expect(resources.list).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: '添加资源需求' }))
  fireEvent.click(screen.getByRole('button', { name: '选择已登记资源 1' }))
  const choose = await screen.findByRole('button', { name: 'A机 · 套' })
  expect(screen.getByRole('button', { name: '停用机 · 套' }).hasAttribute('disabled')).toBe(true)
  expect(screen.getByRole('button', { name: '教学楼 · 套' }).hasAttribute('disabled')).toBe(true)
  fireEvent.click(choose)
  expect((screen.getByLabelText('需求单位 1') as HTMLInputElement).value).toBe('套')
  expect(screen.getByText('已关联登记资源')).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: '保存草稿' }))
  fireEvent.click(screen.getByRole('button', { name: '确认创作操作' }))
  await waitFor(() => expect(api.write).toHaveBeenCalledTimes(1))
  expect(api.write.mock.calls[0][4]).toMatchObject({ scenes: [{ resource_needs: [{ resource_id: 'camera-1', unit: '套', quantity: 1 }] }] })
})
