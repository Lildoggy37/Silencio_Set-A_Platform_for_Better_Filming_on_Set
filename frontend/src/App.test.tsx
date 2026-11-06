import { fireEvent, render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeAll, test, expect, vi } from 'vitest'
import App from './App'
import { preloadScreens } from './test-preload'

beforeAll(preloadScreens, 60_000)

test('navigates four workspaces and reports real connection failures', async () => {
  vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('offline')))
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  render(<QueryClientProvider client={client}><App /></QueryClientProvider>)
  fireEvent.click(await screen.findByRole('button', { name: '查看布局预览' }))
  expect(await screen.findByText('尚未选择剧组')).toBeTruthy()
  expect(await screen.findByText('无法连接')).toBeTruthy()
  for (const tab of ['排期', '资源', '成员', '总览']) {
    fireEvent.click(screen.getByRole('button', { name: tab }))
    expect(screen.getByRole('heading', { name: '剧组工作区布局预览' })).toBeTruthy()
  }
  expect(screen.getAllByRole('button', { pressed: true })).toHaveLength(1)
  fireEvent.click(screen.getByRole('button', { name: '项目资料' }))
  expect(await screen.findByText('请先选择剧组，再查看项目资料。')).toBeTruthy()
  client.clear()
})
