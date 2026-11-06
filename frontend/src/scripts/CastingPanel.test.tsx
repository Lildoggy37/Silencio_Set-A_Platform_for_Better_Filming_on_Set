import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, expect, test, vi } from 'vitest'
import { CastingPanel, CastingLibrary } from './CastingPanel'
import { AccountError } from '../api/accounts'

const api = vi.hoisted(() => ({ casting: vi.fn(), castingActors: vi.fn(), saveCasting: vi.fn(), castingScripts: vi.fn() }))
vi.mock('../auth/AuthProvider', () => ({ useAuth: () => ({ creative: api, refresh: vi.fn() }) }))
const entry = { character_name: '林雨', is_current_character: true, member_id: null, membership_joined_at: null, display_name: null, status: 'unassigned' }
const page = { script_id: 'script', lock_version: 3, items: [entry], total: 1, page: 1, page_size: 20 }
const actor = { id: 'actor', display_name: '陈同学', membership_joined_at: '2026-11-03T00:00:00Z' }
beforeEach(() => {
  vi.resetAllMocks()
  api.casting.mockResolvedValue(page)
  api.castingActors.mockResolvedValue({ items: [actor], total: 1, page: 1, page_size: 20 })
  api.castingScripts.mockResolvedValue({ items: [{ id: 'script', title: '雨停之前' }], total: 1, page: 1, page_size: 20 })
  api.saveCasting.mockResolvedValue({ script_id: 'script', lock_version: 4 })
})
function start(permissions = ['plan.edit', 'member.read'], library = false) {
  const cache = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  const props = { crewId: 'crew', userId: 'user', permissions }
  render(<QueryClientProvider client={cache}>{library ? <CastingLibrary {...props} /> : <CastingPanel {...props} scriptId="script" />}</QueryClientProvider>)
  return cache
}
async function choose() {
  fireEvent.click(await screen.findByRole('button', { name: '选择演员 · 林雨' }))
  fireEvent.click(await screen.findByRole('button', { name: '陈同学' }))
  await screen.findByText('林雨：确认绑定 陈同学？')
}

test('producer can open casting from script titles without reading script bodies', async () => {
  start(undefined, true)
  fireEvent.click(await screen.findByRole('button', { name: '雨停之前 · 演员安排' }))
  await screen.findByText('尚未绑定演员')
  expect(api.casting).toHaveBeenCalledWith('crew', 'script', 1, expect.any(AbortSignal))
  expect(api.castingActors).not.toHaveBeenCalled()
})
test('binding asks for confirmation and preserves the selected membership epoch', async () => {
  const cache = start(); const invalidate = vi.spyOn(cache, 'invalidateQueries')
  await choose()
  expect(api.saveCasting).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: '确认绑定操作' }))
  await waitFor(() => expect(api.saveCasting).toHaveBeenCalledTimes(1))
  expect(api.saveCasting.mock.calls[0]).toEqual(['crew', 'script', { expected_lock_version: 3, character_name: '林雨', member_id: actor.id, membership_joined_at: actor.membership_joined_at }, expect.any(String)])
  await waitFor(() => expect(invalidate).toHaveBeenCalledWith({ queryKey: ['crews', 'user', 'crew'] }))
})
test('read only users cannot change or remove a binding', async () => {
  start(['script.read', 'scene.read', 'member.read'])
  await screen.findByText('尚未绑定演员')
  expect(screen.queryByRole('button', { name: '选择演员 · 林雨' })).toBeNull()
})
test('uncertain requests retain body key and original actor for manual retry', async () => {
  api.saveCasting.mockRejectedValueOnce(new AccountError('NETWORK_UNCONFIRMED'))
  start(); await choose()
  fireEvent.click(screen.getByRole('button', { name: '确认绑定操作' }))
  fireEvent.click(await screen.findByRole('button', { name: '用原绑定请求重试' }))
  await waitFor(() => expect(api.saveCasting).toHaveBeenCalledTimes(2))
  expect(api.saveCasting.mock.calls[1]).toEqual(api.saveCasting.mock.calls[0])
})
test('known version conflicts refresh the binding and require selecting again', async () => {
  api.saveCasting.mockRejectedValueOnce(new AccountError('VERSION_CONFLICT', 409))
  start(); await choose()
  fireEvent.click(screen.getByRole('button', { name: '确认绑定操作' }))
  await waitFor(() => expect(api.casting).toHaveBeenCalledTimes(2))
  await waitFor(() => expect(screen.queryByRole('button', { name: '用原绑定请求重试' })).toBeNull())
  expect(api.saveCasting).toHaveBeenCalledTimes(1)
})
test('stale historical bindings can be explicitly removed', async () => {
  api.casting.mockResolvedValue({ ...page, items: [{ ...entry, member_id: actor.id, membership_joined_at: actor.membership_joined_at, display_name: actor.display_name, status: 'invalid', is_current_character: false }] })
  start()
  await screen.findByText('陈同学 · 身份已失效，请重新绑定 · 工作稿未登记此人物')
  fireEvent.click(screen.getByRole('button', { name: '解除绑定 · 林雨' }))
  fireEvent.click(screen.getByRole('button', { name: '确认绑定操作' }))
  await waitFor(() => expect(api.saveCasting).toHaveBeenCalledTimes(1))
  expect(api.saveCasting.mock.calls[0][2]).toEqual({ expected_lock_version: 3, character_name: '林雨', member_id: null, membership_joined_at: null })
})
test('a failed refresh hides private cached bindings', async () => {
  start(); await screen.findByText('尚未绑定演员')
  api.casting.mockRejectedValue(new AccountError('FORBIDDEN', 403))
  fireEvent.click(screen.getByRole('button', { name: '刷新演员绑定' }))
  await waitFor(() => expect(screen.queryByText('尚未绑定演员')).toBeNull())
})

test('the script picker cannot discard an unresolved casting request', async () => {
  api.saveCasting.mockRejectedValueOnce(new AccountError('NETWORK_UNCONFIRMED'))
  start(undefined, true)
  fireEvent.click(await screen.findByRole('button', { name: '雨停之前 · 演员安排' }))
  await choose()
  fireEvent.click(screen.getByRole('button', { name: '确认绑定操作' }))
  await screen.findByRole('button', { name: '用原绑定请求重试' })
  expect((screen.getByRole('button', { name: '雨停之前 · 演员安排' }) as HTMLButtonElement).disabled).toBe(true)
  fireEvent.click(screen.getByRole('button', { name: '用原绑定请求重试' }))
  await waitFor(() => expect((screen.getByRole('button', { name: '雨停之前 · 演员安排' }) as HTMLButtonElement).disabled).toBe(false))
})
