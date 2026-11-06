import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, expect, test, vi } from 'vitest'
import { PlanWithdrawal } from './PlanWithdrawal'
import { AccountError } from '../api/accounts'
import type { PlanVersion } from '../api/plans'

const api = vi.hoisted(() => ({ withdraw: vi.fn() }))
vi.mock('../auth/AuthProvider', () => ({ useAuth: () => ({ plans: api, refresh: vi.fn() }) }))
const version: PlanVersion = { id: 'version', plan_id: 'plan', version_no: 2, status: 'published', lock_version: 3, published_at: '', sessions: [], review_reasons: [] }
beforeEach(() => { vi.resetAllMocks(); api.withdraw.mockResolvedValue({ withdrawn_version_id: 'version', lock_version: 4 }) })
function start() {
  const cache = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  render(<QueryClientProvider client={cache}><PlanWithdrawal crewId="crew" userId="user" version={version} /></QueryClientProvider>)
  return cache
}
async function prepare() {
  fireEvent.click(screen.getByRole('button', { name: '撤回当前正式排期' }))
  fireEvent.change(await screen.findByLabelText('撤回原因'), { target: { value: ' 场地关闭，取消当天拍摄 ' } })
  fireEvent.click(screen.getByRole('button', { name: '核对撤回影响' }))
}
test('withdrawal requires reason and review before touching backend', async () => {
  const cache = start(); const invalidation = vi.spyOn(cache, 'invalidateQueries')
  await prepare()
  expect(api.withdraw).not.toHaveBeenCalled()
  await screen.findByText('撤回原因：场地关闭，取消当天拍摄')
  fireEvent.click(screen.getByRole('button', { name: '确认撤回排期' }))
  await waitFor(() => expect(api.withdraw).toHaveBeenCalledTimes(1))
  expect(api.withdraw.mock.calls[0]).toEqual(['crew', 'version', { expected_lock_version: 3, reason: '场地关闭，取消当天拍摄' }, expect.any(String)])
  await waitFor(() => expect(invalidation).toHaveBeenCalledWith({ queryKey: ['crews', 'user', 'crew'] }))
})
test('unknown withdrawal outcome preserves exact original request and key', async () => {
  api.withdraw.mockRejectedValueOnce(new AccountError('NETWORK_UNCONFIRMED'))
  start(); await prepare()
  fireEvent.click(screen.getByRole('button', { name: '确认撤回排期' }))
  const retry = await screen.findByRole('button', { name: '用原撤回请求重试' })
  await waitFor(() => expect(retry.classList.contains('ant-btn-loading')).toBe(false))
  fireEvent.click(retry)
  await waitFor(() => expect(api.withdraw).toHaveBeenCalledTimes(2))
  expect(api.withdraw.mock.calls[1]).toEqual(api.withdraw.mock.calls[0])
})
test('known conflicts refresh current plan without an automatic second withdrawal', async () => {
  api.withdraw.mockRejectedValueOnce(new AccountError('CURRENT_VERSION_CHANGED', 409))
  const cache = start(); const invalidation = vi.spyOn(cache, 'invalidateQueries')
  await prepare(); fireEvent.click(screen.getByRole('button', { name: '确认撤回排期' }))
  await waitFor(() => expect(invalidation).toHaveBeenCalled())
  expect(api.withdraw).toHaveBeenCalledTimes(1)
  expect(screen.queryByRole('button', { name: '用原撤回请求重试' })).toBeNull()
})
test('blank reasons cannot proceed to cancellation confirmation', async () => {
  start(); fireEvent.click(screen.getByRole('button', { name: '撤回当前正式排期' }))
  fireEvent.change(await screen.findByLabelText('撤回原因'), { target: { value: '  ' } })
  expect((screen.getByRole('button', { name: '核对撤回影响' }) as HTMLButtonElement).disabled).toBe(true)
  expect(api.withdraw).not.toHaveBeenCalled()
})
