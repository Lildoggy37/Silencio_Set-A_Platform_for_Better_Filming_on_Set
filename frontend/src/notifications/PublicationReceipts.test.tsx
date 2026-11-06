import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, expect, test, vi } from 'vitest'
import { PublicationReceipts, canReadReceipts } from './PublicationReceipts'
import { AccountError } from '../api/accounts'

const api = vi.hoisted(() => ({ receipts: vi.fn(), read: vi.fn(), acknowledge: vi.fn() }))
vi.mock('../auth/AuthProvider', () => ({ useAuth: () => ({ notifications: api, refresh: vi.fn() }) }))
const row = { member_id: 'member', membership_joined_at: '2026-11-03T00:00:00Z', display_name: '陈同学', identity_status: 'active', notification_status: 'generated', requires_confirmation: true, read_at: null, confirmed_at: null }
const data = { source_type: 'plan', source_id: 'version', version_no: 2, source_status: 'current', observed_at: '2026-11-03T01:00:00Z', dispatch_status: 'processed',
  counts: { intended: 1, generated: 1, pending_dispatch: 0, skipped: 0, read: 0, confirmed: 0, unconfirmed: 1 }, items: [row], total: 1, page: 1, page_size: 20 }
beforeEach(() => { vi.resetAllMocks(); api.receipts.mockResolvedValue(data) })
function start(permissions = ['plan.edit', 'plan.publish', 'member.read']) {
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })}><PublicationReceipts crewId="crew" userId="user" kind="plan" versionId="version" permissions={permissions} /></QueryClientProvider>)
}
test('receipt management loads only when opened and never marks someone else read', async () => {
  start(); expect(api.receipts).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: '查看发布通知回执' }))
  await screen.findByText('陈同学')
  expect(api.receipts).toHaveBeenCalledWith('crew', 'plan', 'version', 1, expect.any(AbortSignal))
  expect(api.read).not.toHaveBeenCalled(); expect(api.acknowledge).not.toHaveBeenCalled()
  expect(screen.getByText('已读 0 · 已知悉 0 · 已生成通知中需知悉但未确认 1')).toBeTruthy()
})
test('related-only readers have no management entry', () => {
  start(['plan.read_related', 'member.read', 'notification.receipts'])
  expect(screen.queryByRole('button', { name: '查看发布通知回执' })).toBeNull()
  expect(canReadReceipts('call_sheet', ['call_sheet.review', 'notification.receipts', 'member.read'])).toBe(true)
})
test('withdrawn receipts are visibly historical and retain old confirmations', async () => {
  api.receipts.mockResolvedValue({ ...data, source_status: 'withdrawn', items: [{ ...row, identity_status: 'rejoined', read_at: '2026-11-03T00:30:00Z', confirmed_at: '2026-11-03T00:31:00Z' }] })
  start(); fireEvent.click(screen.getByRole('button', { name: '查看发布通知回执' }))
  await screen.findByText('以下为历史发布回执，不代表成员已知悉当前有效安排。')
  expect(screen.getByText('入组身份已变化')).toBeTruthy()
})
test('pending publication events do not look like generated unread notices', async () => {
  api.receipts.mockResolvedValue({ ...data, dispatch_status: 'pending_dispatch', counts: { ...data.counts, generated: 0, pending_dispatch: 1, unconfirmed: 0 }, items: [{ ...row, notification_status: 'pending_dispatch' }] })
  start(); fireEvent.click(screen.getByRole('button', { name: '查看发布通知回执' }))
  await screen.findByText('发布事件尚待通知消费者处理，请稍后手动刷新。')
  expect(screen.queryByText('已生成站内通知')).toBeNull()
})
test('paging and manual refresh keep the selected source version', async () => {
  api.receipts.mockResolvedValue({ ...data, total: 21 })
  start(); fireEvent.click(screen.getByRole('button', { name: '查看发布通知回执' }))
  fireEvent.click(await screen.findByRole('button', { name: '下一页回执' }))
  await waitFor(() => expect(api.receipts).toHaveBeenCalledWith('crew', 'plan', 'version', 2, expect.any(AbortSignal)))
  await waitFor(() => expect(screen.getByRole('button', { name: '刷新发布回执' }).classList.contains('ant-btn-loading')).toBe(false))
  fireEvent.click(screen.getByRole('button', { name: '刷新发布回执' }))
  await waitFor(() => expect(api.receipts).toHaveBeenCalledTimes(3))
})
test('a permission failure removes cached private recipient data', async () => {
  start(); fireEvent.click(screen.getByRole('button', { name: '查看发布通知回执' }))
  await screen.findByText('陈同学')
  api.receipts.mockRejectedValue(new AccountError('FORBIDDEN', 403))
  fireEvent.click(screen.getByRole('button', { name: '刷新发布回执' }))
  await waitFor(() => expect(screen.queryByText('陈同学')).toBeNull())
})
