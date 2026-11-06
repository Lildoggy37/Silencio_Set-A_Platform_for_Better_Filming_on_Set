import { fireEvent,render,screen,waitFor } from '@testing-library/react'
import { QueryClient,QueryClientProvider } from '@tanstack/react-query'
import { beforeEach,expect,test,vi } from 'vitest'
import { ImpactHistory } from './ImpactHistory'
import { AccountError } from '../api/accounts'

const api=vi.hoisted(() => ({ check:vi.fn(),records:vi.fn(),history:vi.fn() }))
vi.mock('../auth/AuthProvider',() => ({ useAuth:() => ({ changes:api,refresh:vi.fn() }) }))
const report={id:'check',category:'plans',checked_at:'2026-11-03T00:00:00Z',detected_count:1,updated_count:0,resolved_count:0,reopened_count:0,open_count:1}
const target={id:'version',category:'plans',title:'旧版排期',reason_codes:['script_changed']}
const issue={id:'issue',target,status:'open',first_detected_at:report.checked_at,last_checked_at:report.checked_at,resolved_at:null,reopened_count:0,lock_version:1,resolution_reason:null}
beforeEach(() => {
  vi.resetAllMocks(); api.check.mockResolvedValue(report)
  api.records.mockResolvedValue({items:[issue],latest_check:report,total:1,page:1,page_size:20})
  api.history.mockResolvedValue({issue,items:[{id:'event',kind:'detected',recorded_at:report.checked_at,before_status:null,after_status:'open',target,evidence:{},resolution_reason:null}],total:1,page:1,page_size:20})
})
function start() {
  const lock=vi.fn()
  render(<QueryClientProvider client={new QueryClient({defaultOptions:{queries:{retry:false,gcTime:0}}})}><ImpactHistory crewId="crew" userId="user" category="plans" onLock={lock} /></QueryClientProvider>)
  return lock
}
test('opening recorded history reads without scanning or writing a new check',async () => {
  start(); expect(api.records).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button',{name:'查看问题记录'}))
  await screen.findByText('旧版排期')
  expect(api.records).toHaveBeenCalledWith('crew','plans','all',1,expect.any(AbortSignal))
  expect(api.check).not.toHaveBeenCalled()
})
test('explicit checking records a batch and then loads history without business publication',async () => {
  const lock=start(); fireEvent.click(screen.getByRole('button',{name:'检查并记录当前类别'}))
  await screen.findByText('旧版排期')
  expect(api.check).toHaveBeenCalledWith('crew','plans',expect.any(String))
  expect(lock.mock.calls.map(call=>call[0])).toEqual([true,false])
  expect(screen.getByText(/本次记录：发现 1/)).toBeTruthy()
})
test('an uncertain check retains the original key and keeps category changes locked',async () => {
  api.check.mockRejectedValueOnce(new AccountError('NETWORK_UNCONFIRMED'))
  const lock=start(); fireEvent.click(screen.getByRole('button',{name:'检查并记录当前类别'}))
  const retry=await screen.findByRole('button',{name:'用原检查请求重试'})
  expect(lock).toHaveBeenLastCalledWith(true)
  await waitFor(() => expect(retry.classList.contains('ant-btn-loading')).toBe(false))
  fireEvent.click(retry)
  await waitFor(() => expect(api.check).toHaveBeenCalledTimes(2))
  expect(api.check.mock.calls[1]).toEqual(api.check.mock.calls[0])
  await waitFor(() => expect(lock).toHaveBeenLastCalledWith(false))
})
test('rapid clicks cannot replace the original key before an uncertain response',async () => {
  let reject!: (error:unknown)=>void
  api.check.mockImplementationOnce(() => new Promise((_resolve,fail) => {reject=fail}))
  start(); const button=screen.getByRole('button',{name:'检查并记录当前类别'})
  fireEvent.click(button); fireEvent.click(button)
  expect(api.check).toHaveBeenCalledTimes(1)
  reject(new AccountError('NETWORK_UNCONFIRMED'))
  const retry=await screen.findByRole('button',{name:'用原检查请求重试'})
  await waitFor(() => expect(retry.classList.contains('ant-btn-loading')).toBe(false))
  fireEvent.click(retry)
  await waitFor(() => expect(api.check).toHaveBeenCalledTimes(2))
  expect(api.check.mock.calls[1]).toEqual(api.check.mock.calls[0])
})
test('an oversized scan leaves historical state untouched and unlocks the interface',async () => {
  api.check.mockRejectedValue(new AccountError('CHANGE_CHECK_TOO_LARGE',409))
  const lock=start(); fireEvent.click(screen.getByRole('button',{name:'检查并记录当前类别'}))
  await screen.findByText('当前类别超过1000个待处理目标，未更新问题记录；请继续使用实时清单处理。')
  expect(lock).toHaveBeenLastCalledWith(false)
  expect(api.check).toHaveBeenCalledTimes(1); expect(api.records).not.toHaveBeenCalled()
})
test('record filtering and transition reads are separate from issue resolution writes',async () => {
  start(); fireEvent.click(screen.getByRole('button',{name:'查看问题记录'}))
  fireEvent.click(await screen.findByRole('button',{name:'查看变化历史'}))
  await screen.findByRole('heading',{name:'旧版排期 · 观察历史'})
  expect(api.history).toHaveBeenCalledWith('crew','plans','issue',1,expect.any(AbortSignal))
  expect(screen.getByText('当时的问题：剧本工作版本有变化')).toBeTruthy()
  fireEvent.change(screen.getByLabelText('记录状态'),{target:{value:'resolved'}})
  await waitFor(() => expect(api.records).toHaveBeenCalledWith('crew','plans','resolved',1,expect.any(AbortSignal)))
  expect(api.check).not.toHaveBeenCalled()
})
test('a history read failure hides cached recorded titles',async () => {
  start(); fireEvent.click(screen.getByRole('button',{name:'查看问题记录'})); await screen.findByText('旧版排期')
  api.records.mockRejectedValue(new AccountError('FORBIDDEN',403))
  const refresh=screen.getByRole('button',{name:'刷新问题记录'})
  await waitFor(() => expect(refresh.classList.contains('ant-btn-loading')).toBe(false))
  fireEvent.click(refresh)
  await waitFor(() => expect(screen.queryByText('旧版排期')).toBeNull())
})

test('a known permission rejection rechecks and hides previously readable history',async () => {
  start(); fireEvent.click(screen.getByRole('button',{name:'查看问题记录'})); await screen.findByText('旧版排期')
  api.check.mockRejectedValue(new AccountError('FORBIDDEN',403))
  api.records.mockRejectedValue(new AccountError('FORBIDDEN',403))
  fireEvent.click(screen.getByRole('button',{name:'检查并记录当前类别'}))
  await waitFor(() => expect(screen.queryByText('旧版排期')).toBeNull())
  expect(api.check).toHaveBeenCalledTimes(1)
})
