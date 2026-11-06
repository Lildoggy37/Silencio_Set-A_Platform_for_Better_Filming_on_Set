import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, expect, test, vi } from 'vitest'
import { SourceImpacts } from './SourceImpacts'

const api = vi.hoisted(() => ({ sourceImpacts: vi.fn() }))
vi.mock('../auth/AuthProvider', () => ({ useAuth: () => ({ creative: api, refresh: vi.fn() }) }))
vi.mock('./CreativePanel', () => ({ CreativePanel: (props: { sourceId: string; initialId: string; kind: string }) => <p>{props.kind} · {props.sourceId} · {props.initialId}</p> }))
beforeEach(() => { vi.resetAllMocks(); api.sourceImpacts.mockResolvedValue({ items: [{ id: 'breakdown', kind: 'breakdowns', source_id: 'old-script-version', source_script_version_no: 1, version_no: 2, status: 'review_required' }], total: 1, page: 1, page_size: 20 }) })
function start() { render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })}><SourceImpacts crewId="crew" userId="user" scriptId="script" permissions={['scene.read', 'storyboard.read']} /></QueryClientProvider>) }
test('impact links open the fixed historical source and artifact rather than the working draft', async () => {
  start(); fireEvent.click(await screen.findByRole('button', { name: '查看受影响资料' }))
  await screen.findByText('breakdowns · old-script-version · breakdown')
  expect(api.sourceImpacts).toHaveBeenCalledWith('crew', 'script', 'breakdowns', 1, expect.any(AbortSignal))
})
test('changing impact category clears prior selection and requests a new scoped page', async () => {
  start(); await screen.findByText('来源已变化，待复核')
  fireEvent.click(screen.getByRole('button', { name: '分镜来源影响' }))
  await waitFor(() => expect(api.sourceImpacts).toHaveBeenCalledWith('crew', 'script', 'storyboards', 1, expect.any(AbortSignal)))
  expect(screen.queryByText('breakdowns · old-script-version · breakdown')).toBeNull()
})
