import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, expect, test, vi } from 'vitest'
import { CustodyPanel } from './CustodyPanel'
import { AccountError } from '../api/accounts'
import type { Resource, Reservation } from '../api/resources'

const mocks = vi.hoisted(() => ({ listCheckouts: vi.fn(), custody: vi.fn(), get: vi.fn(), getCheckout: vi.fn(), members: vi.fn(), reservation: vi.fn() }))
vi.mock('../auth/AuthProvider', () => ({ useAuth: () => ({ resources: mocks, crews: { members: mocks.members }, refresh: vi.fn() }) }))
const resource: Resource = { id: 'resource-1', crew_id: 'crew-1', name: '摄影机', resource_type: 'equipment', unit: '台', total_quantity: 2,
  status: 'available', description: null, manager_member_id: 'member-1', manager_display_name: '制片', manager_lock_version: 1, review_ready: true,
  lock_version: 1, created_at: '', updated_at: '', allowed_actions: ['authorize'], custody_actions: ['checkout', 'return'] }
const loan = { id: 'loan-1', crew_id: 'crew-1', resource_id: 'resource-1', reservation_id: 'booking-1', borrower_member_id: 'member-1', quantity: 2,
  returned_quantity: 1, outstanding_quantity: 1, checked_out_at: '2026-01-01T00:00:00Z', expected_return_at: '2026-01-02T00:00:00Z', handled_by: 'member-1', lock_version: 2, status: 'overdue' }
const booking = { id: 'booking-1', quantity: 2, ends_at: '2099-01-01T10:00:00Z', lock_version: 3 } as Reservation
beforeEach(() => {
  vi.resetAllMocks()
  mocks.listCheckouts.mockResolvedValue({ items: [loan], total: 1, page: 1, page_size: 20 })
  mocks.members.mockResolvedValue({ items: [{ id: 'member-1', display_name: '摄影同学', lock_version: 4 }], total: 1 })
  mocks.get.mockResolvedValue({ ...resource, lock_version: 2 })
  mocks.getCheckout.mockResolvedValue({ ...loan, lock_version: 3 })
  mocks.custody.mockResolvedValue({ ...loan, returned_quantity: 2, outstanding_quantity: 0, status: 'returned' })
})
function start(overrides: Partial<Resource> = {}, checkoutBooking: Reservation | null = null) {
  const close = vi.fn()
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })}>
    <CustodyPanel crewId="crew-1" userId="user-1" resource={{ ...resource, ...overrides }} checkoutBooking={checkoutBooking} onCheckoutClosed={close} />
  </QueryClientProvider>)
  return close
}
async function returnEditor() {
  fireEvent.click(await screen.findByRole('button', { name: /^登记归还/ }))
  return within(screen.getByLabelText('本次数量').closest('[role="dialog"]') as HTMLElement)
}

test('overdue custody preserves outstanding quantity and read-only viewers have no write controls', async () => {
  start({ allowed_actions: [], custody_actions: [] })
  await screen.findByText('逾期未还')
  expect(screen.getByText('借出 2 · 已还 1 · 未还 1 台')).toBeTruthy()
  expect(screen.queryByRole('button', { name: /登记归还/ })).toBeNull()
  expect(screen.queryByRole('button', { name: '借还经办授权' })).toBeNull()
})

test('return is explicit and carries current version and partial quantity once', async () => {
  start(); const editor = await returnEditor()
  fireEvent.click(editor.getByRole('button', { name: '核对借还操作' }))
  expect(mocks.custody).not.toHaveBeenCalled()
  const confirm = editor.getByRole('button', { name: '确认借还操作' })
  fireEvent.click(confirm); fireEvent.click(confirm)
  await screen.findByText('归还已记录。')
  expect(mocks.custody).toHaveBeenCalledTimes(1)
  expect(mocks.custody.mock.calls[0][1]).toMatchObject({ action: 'return', id: 'loan-1', body: { quantity: 1, expected_lock_version: 2, note: null } })
})

test('unknown return retries the original key and quantity only after user action', async () => {
  mocks.custody.mockRejectedValueOnce(new AccountError('NETWORK_UNCONFIRMED'))
  start(); const editor = await returnEditor()
  fireEvent.click(editor.getByRole('button', { name: '核对借还操作' })); fireEvent.click(editor.getByRole('button', { name: '确认借还操作' }))
  const retry = await editor.findByRole('button', { name: '用原借还请求重试' })
  expect(editor.getByRole('button', { name: '返回修改' }).hasAttribute('disabled')).toBe(true)
  expect(mocks.custody).toHaveBeenCalledTimes(1)
  fireEvent.click(retry); await screen.findByText('归还已记录。')
  expect(mocks.custody.mock.calls[0][1]).toEqual(mocks.custody.mock.calls[1][1])
})

test('a conflict preserves quantity and requires a fresh loan version before resubmission', async () => {
  mocks.custody.mockRejectedValueOnce(new AccountError('VERSION_CONFLICT', 409))
  start(); const editor = await returnEditor()
  fireEvent.click(editor.getByRole('button', { name: '核对借还操作' })); fireEvent.click(editor.getByRole('button', { name: '确认借还操作' }))
  fireEvent.click(await editor.findByRole('button', { name: '读取最新借还状态' }))
  await waitFor(() => expect(editor.getByLabelText('本次数量').closest('fieldset')!.disabled).toBe(false))
  expect((editor.getByLabelText('本次数量') as HTMLInputElement).value).toBe('1')
  fireEvent.click(editor.getByRole('button', { name: '核对借还操作' })); fireEvent.click(editor.getByRole('button', { name: '确认借还操作' }))
  await screen.findByText('归还已记录。')
  expect(mocks.custody.mock.calls[1][1].body.expected_lock_version).toBe(3)
})

test('checkout uses the approved booking version and converts Beijing input to UTC', async () => {
  start({}, booking)
  const editor = within(screen.getByLabelText('本次数量').closest('[role="dialog"]') as HTMLElement)
  fireEvent.click(editor.getByRole('button', { name: '核对借还操作' }))
  fireEvent.click(editor.getByRole('button', { name: '确认借还操作' }))
  await screen.findByText('借出已记录。')
  expect(mocks.custody.mock.calls[0][1]).toMatchObject({ action: 'checkout', id: 'booking-1', body: { quantity: 1, expected_lock_version: 3, expected_return_at: '2099-01-01T10:00:00.000Z' } })
})

test('authorization explicitly grants capabilities; empty selection explicitly revokes scope', async () => {
  start(); fireEvent.click(screen.getByRole('button', { name: '借还经办授权' }))
  const editor = within(screen.getByLabelText('经办成员').closest('[role="dialog"]') as HTMLElement)
  await editor.findByRole('option', { name: '摄影同学' })
  fireEvent.change(editor.getByLabelText('经办成员'), { target: { value: 'member-1' } })
  fireEvent.click(editor.getByRole('button', { name: '核对借还操作' }))
  expect(editor.getByText('撤销该成员在本资源的全部借还经办范围。')).toBeTruthy()
  fireEvent.click(editor.getByRole('button', { name: '返回修改' }))
  expect((editor.getByLabelText('明确授予所选经办能力') as HTMLInputElement).checked).toBe(false)
  fireEvent.click(editor.getByLabelText('允许经办归还')); fireEvent.click(editor.getByLabelText('明确授予所选经办能力'))
  fireEvent.click(editor.getByRole('button', { name: '核对借还操作' })); fireEvent.click(editor.getByRole('button', { name: '确认借还操作' }))
  await screen.findByText('经办范围已保存。')
  expect(mocks.custody.mock.calls[0][1]).toMatchObject({ action: 'authorize', body: { member_id: 'member-1', expected_member_lock_version: 4, expected_lock_version: 1, permission_codes: ['resource.return'], grant_capabilities: true } })
})
