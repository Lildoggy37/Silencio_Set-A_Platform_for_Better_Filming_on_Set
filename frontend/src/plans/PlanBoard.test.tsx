import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, expect, test, vi } from 'vitest'
import { PlanBoard } from './PlanBoard'
import { AccountError } from '../api/accounts'

const api = vi.hoisted(() => ({ list: vi.fn(), plan: vi.fn(), versions: vi.fn(), get: vi.fn(), choices: vi.fn(), check: vi.fn(), write: vi.fn(), publish: vi.fn() }))
vi.mock('../auth/AuthProvider', () => ({ useAuth: () => ({ plans: api, refresh: vi.fn() }) }))
const version = { id: 'version-1', plan_id: 'plan-1', version_no: 1, lock_version: 1, status: 'draft', review_reasons: [], published_at: null, sessions: [{
  id: 'session-1', scene_revision_id: 'scene-1', scene_label: '01 · 教学楼走廊', starts_at: '2026-11-02T01:00:00Z', ends_at: '2026-11-02T02:00:00Z', transition_minutes: 0,
  location_resource_id: null, location_reservation_id: null, location_name: null, note: null,
  members: [{ member_id: 'member-1', membership_joined_at: '2026-11-01T00:00:00Z', display_name: '林雨' }], resources: [],
}] }
beforeEach(() => {
  vi.resetAllMocks()
  api.list.mockResolvedValue({ items: [{ id: 'plan-1', name: '首日计划' }], total: 1, page: 1, page_size: 20 })
  api.versions.mockResolvedValue({ items: [version], total: 1, page: 1, page_size: 20 })
  api.get.mockResolvedValue(version)
  api.plan.mockResolvedValue({ id: 'plan-1', name: '首日计划', current_published_version_id: null })
  api.write.mockResolvedValue({ ...version, lock_version: 2 })
  api.choices.mockResolvedValue({ items: [], total: 0, page: 1, page_size: 20 })
  api.publish.mockResolvedValue({ published_version_id: 'version-1', status: 'published', lock_version: 2, previous_published_version_id: null, notification_status: 'pending_dispatch' })
})
function start(permissions = ['plan.edit', 'plan.read_all', 'availability.read_all', 'resource.read', 'member.read']) {
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })}><PlanBoard crewId="crew" userId="user" permissions={permissions} /></QueryClientProvider>)
}
async function open() {
  fireEvent.click(await screen.findByRole('button', { name: '首日计划' }))
  fireEvent.click(await screen.findByRole('button', { name: '计划第 1 版 · 草稿' }))
  await screen.findByText('01 · 教学楼走廊')
}
async function save() {
  fireEvent.click(screen.getByRole('button', { name: '保存排期' }))
  await waitFor(() => expect(api.write).toHaveBeenCalledTimes(1))
}

test('a linked current plan opens even when absent from the first plan list page', async () => {
  const linked = { ...version, id: 'hidden-version', plan_id: 'hidden-plan', status: 'published' }
  api.list.mockResolvedValue({ items: [], total: 0, page: 1, page_size: 20 })
  api.get.mockResolvedValue(linked)
  api.plan.mockResolvedValue({ id: 'hidden-plan', name: '后页计划', current_published_version_id: 'hidden-version' })
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })}><PlanBoard crewId="crew" userId="user" permissions={['plan.read_all', 'plan.edit', 'plan.publish']} initialTarget={{ planId: 'hidden-plan', versionId: 'hidden-version' }} /></QueryClientProvider>)
  await screen.findByRole('button', { name: '撤回当前正式排期' })
  expect(api.get).toHaveBeenCalledWith('crew', 'hidden-version')
  expect(api.plan).toHaveBeenCalledWith('crew', 'hidden-plan', expect.any(AbortSignal))
})

test('plan editing preserves membership identity and uses explicit Beijing timestamps', async () => {
  start(); await open()
  expect((screen.getByLabelText('拍摄开始 1') as HTMLInputElement).value).toBe('2026-11-02T09:00')
  fireEvent.change(screen.getByLabelText('拍摄结束 1'), { target: { value: '2026-11-02T11:00:00' } })
  fireEvent.change(screen.getByLabelText('转场分钟 1'), { target: { value: '15' } })
  expect(screen.getByRole('button', { name: '检查排期冲突' }).hasAttribute('disabled')).toBe(true)
  expect(screen.getByRole('button', { name: '复制为新计划草稿' }).hasAttribute('disabled')).toBe(true)
  await save()
  expect(api.write.mock.calls[0][1]).toMatchObject({ action: 'save', target: 'version-1', body: { expected_lock_version: 1, sessions: [{ ends_at: '2026-11-02T03:00:00.000Z', transition_minutes: 15, members: [{ member_id: 'member-1', membership_joined_at: '2026-11-01T00:00:00Z' }] }] } })
  expect(api.write.mock.calls[0][1].body.sessions[0].members[0].display_name).toBeUndefined()
})

test('full readers cannot change or check unpublished plan drafts', async () => {
  start(['plan.read_all']); await open()
  expect(screen.queryByRole('button', { name: '创建拍摄计划' })).toBeNull()
  expect(screen.queryByRole('button', { name: '保存排期' })).toBeNull()
  expect(screen.queryByRole('button', { name: '检查排期冲突' })).toBeNull()
  expect(screen.queryByLabelText('拍摄开始 1')).toBeNull()
  expect(screen.getByRole('table', { name: '拍摄安排 · 北京时间' })).toBeTruthy()
})

test('preflight displays blockers and exact unknown spans without offering publication', async () => {
  api.check.mockResolvedValue({ checked_lock_version: 1, blockers: [{ code: 'LOCATION_REQUIRED', message: '尚未选择拍摄场地', session_id: 'session-1' }], unknown_items: [{ code: 'AVAILABILITY_UNKNOWN', message: '此时段未登记档期', session_id: 'session-1', member_id: 'member-1', starts_at: '2026-11-02T01:00:00Z', ends_at: '2026-11-02T01:30:00Z' }], warnings: [], can_publish: false, limitations: ['当前仅检查草稿。'] })
  start(); await open()
  fireEvent.click(screen.getByRole('button', { name: '检查排期冲突' }))
  expect(await screen.findByText('阻断 1 项 · 档期未知 1 段')).toBeTruthy()
  expect(screen.getByText(/林雨 · 此时段未登记档期：2026-11-02 09:00:00 — 2026-11-02 09:30:00/)).toBeTruthy()
  expect(api.check).toHaveBeenCalledWith('crew', 'version-1', 1)
  expect(screen.queryByRole('button', { name: '发布排期' })).toBeNull()
})

test('unknown write outcome reuses the same frozen plan command', async () => {
  api.write.mockRejectedValueOnce(new AccountError('NETWORK_UNCONFIRMED'))
  start(); await open(); await save()
  const retry = await screen.findByRole('button', { name: '用原排期请求重试' })
  await waitFor(() => expect(retry.classList.contains('ant-btn-loading')).toBe(false))
  fireEvent.click(retry)
  await waitFor(() => expect(api.write).toHaveBeenCalledTimes(2))
  expect(api.write.mock.calls[0]).toEqual(api.write.mock.calls[1])
})

test('reading a new version keeps the local note and updates the explicit save premise', async () => {
  start(); await open()
  fireEvent.change(screen.getByLabelText('场次备注 1'), { target: { value: '保留布光时间' } })
  api.get.mockResolvedValue({ ...version, lock_version: 3 })
  fireEvent.click(screen.getByRole('button', { name: '读取最新版本并保留编辑' }))
  await waitFor(() => expect(api.get).toHaveBeenCalledTimes(2))
  await waitFor(() => expect(screen.getByRole('button', { name: '保存排期' }).closest('fieldset')!.disabled).toBe(false))
  expect((screen.getByLabelText('场次备注 1') as HTMLTextAreaElement).value).toBe('保留布光时间')
  await save()
  expect(api.write.mock.calls[0][1].body).toMatchObject({ expected_lock_version: 3, sessions: [{ note: '保留布光时间' }] })
})

test('member assignment reads choices only on demand and preserves the selected join time', async () => {
  api.choices.mockResolvedValue({ items: [{ id: 'member-2', label: '小陈', membership_joined_at: '2026-11-02T00:00:00Z', quantity: null }], total: 1, page: 1, page_size: 20 })
  start(); await open()
  expect(api.choices).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: '添加参与人员 1' }))
  fireEvent.click(await screen.findByRole('button', { name: '小陈' }))
  await save()
  expect(api.write.mock.calls[0][1].body.sessions[0].members).toContainEqual({ member_id: 'member-2', membership_joined_at: '2026-11-02T00:00:00Z' })
})

const checked = { checked_lock_version: 1, checked_data_revision: 'opaque-check-token', expected_current_published_version_id: null, expires_at: '2026-11-02T02:00:00Z',
  blockers: [], warnings: [], can_publish: true, limitations: [], unknown_items: [{ item_id: 'unknown-1', code: 'AVAILABILITY_UNKNOWN', message: '此时段未登记档期', session_id: 'session-1', member_id: 'member-1', starts_at: '2026-11-02T01:00:00Z', ends_at: '2026-11-02T01:30:00Z' }] }
async function ready() {
  api.check.mockResolvedValue(checked)
  start(['plan.edit', 'plan.read_all', 'plan.publish', 'availability.read_all', 'resource.read', 'member.read']); await open()
  fireEvent.click(screen.getByRole('button', { name: '检查排期冲突' }))
  return await screen.findByRole('checkbox')
}

test('publication requires an explicit check of every unknown item and freezes the check token', async () => {
  const checkbox = await ready()
  expect((checkbox as HTMLInputElement).checked).toBe(false)
  expect(screen.getByRole('button', { name: '核对并发布排期' }).hasAttribute('disabled')).toBe(true)
  fireEvent.click(checkbox)
  fireEvent.click(screen.getByRole('button', { name: '核对并发布排期' }))
  expect(api.publish).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: '确认发布正式排期' }))
  await waitFor(() => expect(api.publish).toHaveBeenCalledTimes(1))
  expect(api.publish.mock.calls[0].slice(0, 3)).toEqual(['crew', 'version-1', { expected_lock_version: 1, expected_current_published_version_id: null, checked_data_revision: 'opaque-check-token', confirmed_unknown_item_ids: ['unknown-1'] }])
  await waitFor(() => expect(screen.queryByRole('button', { name: '保存排期' })).toBeNull())
})

test('unknown publication outcomes keep the original token confirmations and request key', async () => {
  api.publish.mockRejectedValueOnce(new AccountError('NETWORK_UNCONFIRMED'))
  fireEvent.click(await ready())
  fireEvent.click(screen.getByRole('button', { name: '核对并发布排期' }))
  fireEvent.click(screen.getByRole('button', { name: '确认发布正式排期' }))
  const retry = await screen.findByRole('button', { name: '用原发布请求重试' })
  expect(api.publish).toHaveBeenCalledTimes(1)
  await waitFor(() => expect(retry.classList.contains('ant-btn-loading')).toBe(false))
  fireEvent.click(retry)
  await waitFor(() => expect(api.publish).toHaveBeenCalledTimes(2))
  expect(api.publish.mock.calls[0]).toEqual(api.publish.mock.calls[1])
})

test('stale evidence requires another check and does not retain old unknown confirmations', async () => {
  api.publish.mockRejectedValueOnce(new AccountError('PREFLIGHT_STALE'))
  fireEvent.click(await ready())
  fireEvent.click(screen.getByRole('button', { name: '核对并发布排期' }))
  fireEvent.click(screen.getByRole('button', { name: '确认发布正式排期' }))
  await waitFor(() => expect(api.publish).toHaveBeenCalledTimes(1))
  fireEvent.click(await screen.findByRole('button', { name: 'Close' }))
  api.check.mockResolvedValue({ ...checked, checked_data_revision: 'fresh-token', unknown_items: [{ ...checked.unknown_items[0], item_id: 'unknown-2' }] })
  fireEvent.click(screen.getByRole('button', { name: '检查排期冲突' }))
  await waitFor(() => expect(api.check).toHaveBeenCalledTimes(2))
  expect((await screen.findByRole('checkbox') as HTMLInputElement).checked).toBe(false)
  expect(screen.getByRole('button', { name: '核对并发布排期' }).hasAttribute('disabled')).toBe(true)
  expect(api.publish).toHaveBeenCalledTimes(1)
})

test('published versions are read-only and show pending review reasons', async () => {
  const published = { ...version, status: 'published', review_reasons: ['availability_changed'] }
  api.get.mockResolvedValue(published)
  api.versions.mockResolvedValue({ items: [published], total: 1, page: 1, page_size: 20 })
  start(); fireEvent.click(await screen.findByRole('button', { name: '首日计划' }))
  fireEvent.click(await screen.findByRole('button', { name: '计划第 1 版 · 已发布' }))
  expect(await screen.findByText(/排期待复核：人员档期有变化/)).toBeTruthy()
  expect(screen.queryByRole('button', { name: '保存排期' })).toBeNull()
  expect(screen.queryByRole('button', { name: '检查排期冲突' })).toBeNull()
  expect(screen.getByRole('button', { name: '复制为新计划草稿' })).toBeTruthy()
})
