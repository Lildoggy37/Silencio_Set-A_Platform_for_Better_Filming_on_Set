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
    expect(screen.getByRole('heading', { name: `${tab}模块尚未接入` })).toBeTruthy()
  }
  expect(screen.getAllByRole('button', { pressed: true })).toHaveLength(1)
  fireEvent.click(screen.getByRole('button', { name: '项目资料' }))
  expect(await screen.findByText('这里将提供剧本、场戏与文字分镜入口。当前未接入资料接口。')).toBeTruthy()
  client.clear()
})
