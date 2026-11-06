import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { CallSheetBoard } from './CallSheetBoard'
import { AccountError } from '../api/accounts'
import type { CallSheetVersion } from '../api/callSheets'
import type { PlanVersion } from '../api/plans'

const api = vi.hoisted(() => ({ list: vi.fn(), versions: vi.fn(), get: vi.fn(), recipients: vi.fn(), reviews: vi.fn(), write: vi.fn(), exportPdf: vi.fn() }))
vi.mock('../auth/AuthProvider', () => ({ useAuth: () => ({ callSheets: api, refresh: vi.fn() }) }))
const source: PlanVersion = { id: 'source-1', plan_id: 'plan-1', version_no: 1, lock_version: 2, status: 'published', published_at: '2026-11-01T00:00:00Z', review_reasons: [], sessions: [{
  id: 'session-1', scene_revision_id: 'scene-1', scene_label: '01 · 教学楼', starts_at: '2026-11-02T01:00:00Z', ends_at: '2026-11-02T02:00:00Z', transition_minutes: 0,
  location_resource_id: null, location_reservation_id: null, location_name: '教学楼', note: null,
  members: [{ member_id: 'member-1', membership_joined_at: '2026-11-01T00:00:00Z', display_name: '林雨' }], resources: [],
}] }
const version: CallSheetVersion = {
  id: 'version-1', call_sheet_id: 'sheet-1', source_plan_version_id: 'source-1', version_no: 1, lock_version: 1, status: 'draft', validity_status: null,
  call_sheet: { id: 'sheet-1', plan_id: 'plan-1', shooting_date: '2026-11-02', current_published_version_id: null }, source_current: true,
  gathering_at: '2026-11-02T00:00:00Z', gathering_location: '教学楼入口', contact_note: null, notes: null,
  arrangement_snapshot: { project_name: '雨停之前', plan_name: '首日计划', shooting_date: '2026-11-02', plan_version_no: 1, sessions: source.sessions, overnight_continuations: [] },
  recipients: [{ member_id: 'member-1', display_name: '林雨', required: true }], reviewed_at: null, published_at: null, review_id: null, notification_status: null,
}
beforeEach(() => {
  vi.resetAllMocks()
  api.list.mockResolvedValue({ items: [version.call_sheet], total: 1, page: 1, page_size: 20 })
  api.versions.mockResolvedValue({ items: [version], total: 1, page: 1, page_size: 20 })
  api.get.mockResolvedValue(version)
  api.write.mockResolvedValue({ ...version, lock_version: 2 })
  api.reviews.mockResolvedValue({ items: [], total: 0, page: 1, page_size: 20 })
  api.recipients.mockResolvedValue({ items: [{ member_id: 'member-1', display_name: '林雨', required: true }, { member_id: 'member-2', display_name: '制片', required: false }], total: 2, page: 1, page_size: 20 })
})
afterEach(() => vi.restoreAllMocks())
function start(permissions = ['plan.read_all', 'call_sheet.edit', 'call_sheet.review', 'call_sheet.publish', 'member.read'], current = true) {
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })}><CallSheetBoard crewId="crew" userId="user" source={source} sourceIsCurrent={current} permissions={permissions} /></QueryClientProvider>)
}
async function open(status = '草稿') {
  fireEvent.click(await screen.findByRole('button', { name: '2026-11-02 通告' }))
  fireEvent.click(await screen.findByRole('button', { name: `通告第 1 版 · ${status}` }))
  await screen.findByText('接收人：林雨（必选）')
}

test('a workbench target opens the exact call sheet version without clicking a paged list', async () => {
  api.list.mockResolvedValue({ items: [], total: 0, page: 1, page_size: 20 })
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })}><CallSheetBoard crewId="crew" userId="user" source={source} sourceIsCurrent permissions={['call_sheet.review']} initialTarget={{ planId: source.plan_id, versionId: source.id, sheetId: 'sheet-1', sheetVersionId: 'version-1' }} /></QueryClientProvider>)
  await screen.findByText('接收人：林雨（必选）')
  expect(api.get).toHaveBeenCalledWith('crew', 'version-1')
  expect(api.versions).toHaveBeenCalledWith('crew', 'sheet-1', 1, expect.any(AbortSignal))
})
async function confirm() {
  const save = screen.queryByRole('button', { name: '保存通告修改' })
  if (save) fireEvent.click(save)
  else {
    fireEvent.click(screen.getByRole('button', { name: '核对通告操作' }))
    fireEvent.click(screen.getByRole('button', { name: '确认通告操作' }))
  }
  await waitFor(() => expect(api.write).toHaveBeenCalled())
}

test('generation explicitly selects a Beijing shooting day and current source', async () => {
  start()
  fireEvent.click(screen.getByRole('button', { name: '生成每日通告' }))
  fireEvent.change(screen.getByLabelText('通告拍摄日期'), { target: { value: '2026-11-02' } })
  await confirm()
  expect(api.write.mock.calls[0][1]).toMatchObject({ action: 'create', target: null, body: { source_plan_version_id: 'source-1', shooting_date: '2026-11-02' } })
})

test('editing uses explicit Beijing conversion and approved content requires review again', async () => {
  api.get.mockResolvedValue({ ...version, status: 'approved' })
  api.versions.mockResolvedValue({ items: [{ ...version, status: 'approved' }], total: 1 })
  start(); await open('已审核')
  fireEvent.click(screen.getByRole('button', { name: '编辑通告内容' }))
  expect((screen.getByLabelText('集合时间') as HTMLInputElement).value).toBe('2026-11-02T08:00')
  expect(screen.getByText('修改后将恢复草稿，发布前需要重新预览确认。')).toBeTruthy()
  fireEvent.change(screen.getByLabelText('集合时间'), { target: { value: '2026-11-02T07:30:00' } })
  fireEvent.change(screen.getByLabelText('集合地点'), { target: { value: '东门' } })
  await confirm()
  expect(api.write.mock.calls[0][1]).toMatchObject({ action: 'edit', target: 'version-1', body: { expected_lock_version: 1, gathering_at: '2026-11-01T23:30:00.000Z', gathering_location: '东门' } })
})

test('shooting participants stay required while extra recipients can be added', async () => {
  start(); await open()
  fireEvent.click(screen.getByRole('button', { name: '编辑通告接收人' }))
  expect((screen.getByRole('checkbox', { name: '林雨（必选）' }) as HTMLInputElement).disabled).toBe(true)
  fireEvent.click(await screen.findByRole('checkbox', { name: '制片' }))
  await confirm()
  expect(api.write.mock.calls[0][1]).toMatchObject({ action: 'recipients', body: { expected_lock_version: 1, member_ids: ['member-1', 'member-2'] } })
})

test.each([
  [null, /补充集合时间和地点/],
  ['2026-11-02T01:01:00Z', /集合时间晚于当天第一场拍摄/],
])('draft with gathering time %s explains why it cannot be submitted', async (gathering_at, message) => {
  api.get.mockResolvedValue({ ...version, gathering_at })
  start(); await open()
  expect(screen.getByText(message)).toBeTruthy()
  expect((screen.getByRole('button', { name: '发布当前通告' }) as HTMLButtonElement).disabled).toBe(true)
  expect((screen.getByRole('button', { name: '编辑通告内容' }) as HTMLButtonElement).disabled).toBe(false)
})

test('uncertain writes retry original request and key without repeating side effects automatically', async () => {
  api.write.mockRejectedValueOnce(new AccountError('NETWORK_UNCONFIRMED'))
  start(['call_sheet.edit']); await open()
  fireEvent.click(screen.getByRole('button', { name: '送审通告' }))
  await confirm()
  const retry = await screen.findByRole('button', { name: '用原通告请求重试' })
  await waitFor(() => expect(retry.classList.contains('ant-btn-loading')).toBe(false))
  fireEvent.click(retry)
  await waitFor(() => expect(api.write).toHaveBeenCalledTimes(2))
  expect(api.write.mock.calls[1]).toEqual(api.write.mock.calls[0])
  expect(api.write.mock.calls[0][1]).toMatchObject({ action: 'submit', body: { expected_lock_version: 1 } })
})

test('conflict reload preserves entered content and uses latest lock version', async () => {
  api.write.mockRejectedValueOnce(new AccountError('VERSION_CONFLICT', 409))
  start(); await open()
  fireEvent.click(screen.getByRole('button', { name: '编辑通告内容' }))
  fireEvent.change(screen.getByLabelText('注意事项'), { target: { value: '记得带雨具' } })
  await confirm()
  api.get.mockResolvedValue({ ...version, lock_version: 5 })
  fireEvent.click(await screen.findByRole('button', { name: '读取最新通告并保留输入' }))
  await waitFor(() => expect(screen.queryByRole('button', { name: '读取最新通告并保留输入' })).toBeNull())
  expect((screen.getByLabelText('注意事项') as HTMLTextAreaElement).value).toBe('记得带雨具')
  await confirm()
  await waitFor(() => expect(api.write).toHaveBeenCalledTimes(2))
  expect(api.write.mock.calls[1][1].body).toMatchObject({ expected_lock_version: 5, notes: '记得带雨具' })
})

test('publication compares the current published pointer and states delivery is pending', async () => {
  api.get.mockResolvedValue({ ...version, status: 'approved', call_sheet: { ...version.call_sheet, current_published_version_id: 'older' } })
  api.versions.mockResolvedValue({ items: [{ ...version, status: 'approved' }], total: 1 })
  start(); await open('已审核')
  fireEvent.click(screen.getByRole('button', { name: '发布当前通告' }))
  expect(screen.getByText(/发布成功不代表成员已经收到或知悉/)).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: '确认发布通告' }))
  await waitFor(() => expect(api.write).toHaveBeenCalledTimes(1))
  expect(api.write.mock.calls[0][1]).toMatchObject({ action: 'publish', body: { expected_lock_version: 1, expected_current_published_version_id: 'older' } })
})

test('related readers get a read-only withdrawn notice and source warning', async () => {
  api.get.mockResolvedValue({ ...version, status: 'published', validity_status: 'withdrawn', source_current: false })
  api.versions.mockResolvedValue({ items: [{ ...version, status: 'published' }], total: 1 })
  start(['call_sheet.read_related']); await open('已发布')
  expect(screen.getByText('已撤回，不再作为拍摄安排')).toBeTruthy()
  expect(screen.getByText(/来源排期已更新或待复核/)).toBeTruthy()
  expect(screen.queryByRole('button', { name: '生成每日通告' })).toBeNull()
  expect(screen.queryByRole('button', { name: '编辑通告内容' })).toBeNull()
  expect(screen.queryByRole('button', { name: '通告审核历史' })).toBeNull()
  expect(screen.queryByRole('button', { name: '导出当前所选版本PDF' })).toBeNull()
})

test('PDF export binds selected version and revision and downloads only the successful blob', async () => {
  const blob = new Blob(['%PDF-example'], { type: 'application/pdf' })
  api.exportPdf.mockResolvedValue(blob)
  const create = vi.fn(() => 'blob:call-sheet'), release = vi.fn()
  vi.stubGlobal('URL', class extends URL { static createObjectURL = create; static revokeObjectURL = release })
  const clicked: { name: string; url: string }[] = []
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function(this: HTMLAnchorElement) { clicked.push({ name: this.download, url: this.href }) })
  start(['plan.read_all', 'call_sheet.edit', 'call_sheet.export']); await open()
  fireEvent.click(screen.getByRole('button', { name: '导出当前所选版本PDF' }))
  await waitFor(() => expect(clicked).toHaveLength(1))
  expect(api.exportPdf).toHaveBeenCalledWith('crew', 'version-1', 1)
  expect(create).toHaveBeenCalledWith(blob)
  expect(clicked[0]).toEqual({ name: 'SilencioSet-2026-11-02-v1-version-.pdf', url: 'blob:call-sheet' })
  expect(api.write).not.toHaveBeenCalled()
})

test('failed PDF export retains web content and never starts a stale file download', async () => {
  api.exportPdf.mockRejectedValue(new AccountError('VERSION_CONFLICT', 409))
  const create = vi.fn()
  vi.stubGlobal('URL', class extends URL { static createObjectURL = create; static revokeObjectURL = vi.fn() })
  start(['plan.read_all', 'call_sheet.edit', 'call_sheet.export']); await open()
  fireEvent.click(screen.getByRole('button', { name: '导出当前所选版本PDF' }))
  expect(await screen.findByText(/网页版仍可查看/)).toBeTruthy()
  expect(screen.getByText('接收人：林雨（必选）')).toBeTruthy()
  expect(api.exportPdf).toHaveBeenCalledTimes(1)
  expect(create).not.toHaveBeenCalled()
})

test('old source disables generation and review history renders its own frozen content', async () => {
  api.reviews.mockResolvedValue({ items: [{ id: 'review', reviewed_at: '2026-11-01T00:00:00Z', decision: 'accept', review_note: null, reviewed_lock_version: 1,
    content_snapshot: { ...version, gathering_location: '旧集合地点', recipients: [{ member_id: 'old', display_name: '原接收人' }] } }], total: 1 })
  start(undefined, false); await open()
  expect((screen.getByRole('button', { name: '生成每日通告' }) as HTMLButtonElement).disabled).toBe(true)
  fireEvent.click(screen.getByRole('button', { name: '通告审核历史' }))
  expect(await screen.findByText(/旧集合地点/)).toBeTruthy()
  expect(screen.getByText('接收人：原接收人')).toBeTruthy()
})


test('a complete draft can be previewed and published with one confirmation', async () => {
  start(); await open()
  expect(screen.queryByRole('button', { name: '送审通告' })).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: '发布当前通告' }))
  expect(api.write).not.toHaveBeenCalled()
  expect(screen.getAllByText(/教学楼入口/).length).toBeGreaterThan(0)
  fireEvent.click(screen.getByRole('button', { name: '确认发布通告' }))
  await waitFor(() => expect(api.write).toHaveBeenCalledTimes(1))
  expect(api.write.mock.calls[0][1]).toMatchObject({ action: 'publish', body: { expected_lock_version: 1, expected_current_published_version_id: null } })
})
