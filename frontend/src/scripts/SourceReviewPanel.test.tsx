import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, expect, test, vi } from 'vitest'
import { SourceReviewPanel } from './SourceReviewPanel'
import { AccountError } from '../api/accounts'

const api = vi.hoisted(() => ({ sourceAssessment: vi.fn(), reviewSource: vi.fn(), sourceReviewHistory: vi.fn() }))
const scripts = vi.hoisted(() => ({ get: vi.fn() }))
vi.mock('../auth/AuthProvider', () => ({ useAuth: () => ({ creative: api, scripts, refresh: vi.fn() }) }))
const basis = { kind: 'breakdowns', id: 'breakdown', source_id: 'fixed', version_no: 1, review_status: 'confirmed', lock_version: 4, script_id: 'script', script_title: '雨停之前', source_script_version_id: 'fixed', source_script_version_no: 1, working_script_version_id: 'working', working_script_version_no: 2, working_revision: 3, status: 'review_required', latest_review: null }
beforeEach(() => {
  vi.resetAllMocks(); api.sourceAssessment.mockResolvedValue(basis); api.reviewSource.mockResolvedValue(basis)
  api.sourceReviewHistory.mockResolvedValue({ items: [], total: 0, page: 1, page_size: 20 })
  scripts.get.mockImplementation((_crew, _script, id) => Promise.resolve({ version: { paragraphs: [{ id, paragraph_no: 1, text: id === 'fixed' ? '旧稿走廊重逢' : '工作稿改为雨夜重逢' }] } }))
})
function start(permissions = ['script.read', 'scene.read', 'scene.review']) {
  const cache = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  render(<QueryClientProvider client={cache}><SourceReviewPanel crewId="crew" userId="user" kind="breakdowns" objectId="breakdown" permissions={permissions} /></QueryClientProvider>)
  return cache
}
async function open() {
  fireEvent.click(screen.getByRole('button', { name: '检查资料来源' }))
  await screen.findByText('来源已变化，待复核')
}
async function prepare() {
  fireEvent.change(screen.getByLabelText('复核结论'), { target: { value: 'still_applicable' } })
  fireEvent.change(screen.getByLabelText('复核依据'), { target: { value: '已核对工作稿第1段，场地与参与人物不变。' } })
  fireEvent.click(screen.getByRole('button', { name: '核对来源复核结论' }))
}
test('source comparison loads on request and does not silently review the artifact', async () => {
  start(); expect(api.sourceAssessment).not.toHaveBeenCalled(); await open()
  expect(scripts.get).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: '对照剧本文字' }))
  await screen.findByText('旧稿走廊重逢'); await screen.findByText('工作稿改为雨夜重逢')
  expect(api.reviewSource).not.toHaveBeenCalled()
})
test('review confirmation binds exact target lock working epoch and previous decision', async () => {
  const cache = start(); const invalidate = vi.spyOn(cache, 'invalidateQueries'); await open(); await prepare()
  expect(api.reviewSource).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: '确认来源复核' }))
  await waitFor(() => expect(api.reviewSource).toHaveBeenCalledTimes(1))
  expect(api.reviewSource.mock.calls[0]).toEqual(['crew', 'breakdowns', 'breakdown', { expected_lock_version: 4, expected_working_version_id: 'working', expected_working_revision: 3, expected_review_id: null, conclusion: 'still_applicable', review_note: '已核对工作稿第1段，场地与参与人物不变。' }, expect.any(String)])
  await waitFor(() => expect(invalidate).toHaveBeenCalledWith({ queryKey: ['crews', 'user', 'crew'] }))
})
test('readers can inspect sources without writing applicability conclusions', async () => {
  start(['script.read', 'scene.read']); await open()
  expect(screen.queryByLabelText('复核依据')).toBeNull()
  expect(screen.getByRole('button', { name: '对照剧本文字' })).toBeTruthy()
})
test('unknown review outcome retries frozen original body and key', async () => {
  api.reviewSource.mockRejectedValueOnce(new AccountError('NETWORK_UNCONFIRMED'))
  start(); await open(); await prepare(); fireEvent.click(screen.getByRole('button', { name: '确认来源复核' }))
  const retry = await screen.findByRole('button', { name: '用原复核请求重试' })
  await waitFor(() => expect(retry.classList.contains('ant-btn-loading')).toBe(false))
  fireEvent.click(retry)
  await waitFor(() => expect(api.reviewSource).toHaveBeenCalledTimes(2))
  expect(api.reviewSource.mock.calls[1]).toEqual(api.reviewSource.mock.calls[0])
})
test('changed working basis requires a refresh and a fresh confirmation while retaining note', async () => {
  api.reviewSource.mockRejectedValueOnce(new AccountError('SOURCE_BASIS_CHANGED', 409))
  start(); await open(); await prepare(); fireEvent.click(screen.getByRole('button', { name: '确认来源复核' }))
  await screen.findByText('提交依据已变化。请刷新来源依据，保留的说明需重新核对后再提交。')
  expect((screen.getByLabelText('复核依据') as HTMLTextAreaElement).value).toBe('已核对工作稿第1段，场地与参与人物不变。')
  api.sourceAssessment.mockResolvedValue({ ...basis, working_revision: 5 })
  fireEvent.click(screen.getByRole('button', { name: '刷新来源依据' }))
  await waitFor(() => expect(screen.queryByText('提交依据已变化。请刷新来源依据，保留的说明需重新核对后再提交。')).toBeNull())
  expect(api.reviewSource).toHaveBeenCalledTimes(1)
})
test('unconfirmed content cannot acquire source applicability and keeps old history distinct', async () => {
  api.sourceAssessment.mockResolvedValue({ ...basis, review_status: 'pending_review' })
  start(); await open()
  expect(screen.queryByLabelText('复核依据')).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: '查看来源复核历史' }))
  await waitFor(() => expect(api.sourceReviewHistory).toHaveBeenCalledWith('crew', 'breakdowns', 'breakdown', 1, expect.any(AbortSignal)))
})
