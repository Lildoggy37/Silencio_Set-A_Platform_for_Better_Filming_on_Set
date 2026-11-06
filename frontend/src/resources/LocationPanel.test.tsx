import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, expect, test, vi } from 'vitest'
import { LocationPanel } from './LocationPanel'
import { AccountError } from '../api/accounts'
import type { LocationGallery } from '../api/locations'

const api = vi.hoisted(() => ({ location: vi.fn(), uploadPhoto: vi.fn(), locationWrite: vi.fn() }))
vi.mock('../auth/AuthProvider', () => ({ useAuth: () => ({ resources: api, refresh: vi.fn() }) }))
const gallery: LocationGallery = { resource_id: 'location', resource_name: '教学楼走廊', resource_status: 'available', resource_lock_version: 3,
  details: { address: '教学楼东侧', access_instructions: '从东门进入', shooting_constraints: '上课期间保持安静' },
  photos: [{ id: 'photo', description: '入口与可用空间', file_format: 'jpeg', byte_size: 2048, width_px: 1200, height_px: 800, created_at: '2026-11-03T00:00:00Z' }],
  allowed_actions: ['edit_details', 'upload_photo', 'edit_photo', 'remove_photo'] }
beforeEach(() => {
  vi.resetAllMocks()
  api.location.mockResolvedValue(gallery)
  api.uploadPhoto.mockResolvedValue({ ...gallery, resource_lock_version: 4 })
  api.locationWrite.mockResolvedValue({ ...gallery, resource_lock_version: 4 })
})
function start() { render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })}><LocationPanel crewId="crew" userId="user" resourceId="location" /></QueryClientProvider>) }
async function ready() { await screen.findByText('地址：教学楼东侧') }
async function upload() {
  fireEvent.click(screen.getByRole('button', { name: '上传勘景照片' }))
  const file = new File(['fake image'], '走廊.jpg', { type: 'image/jpeg' })
  fireEvent.change(screen.getByLabelText('勘景照片文件'), { target: { files: [file] } })
  fireEvent.change(screen.getByLabelText('照片说明'), { target: { value: '收音与入口' } })
  fireEvent.click(screen.getByRole('button', { name: '核对场地操作' }))
  return file
}

test('director-style read access shows location and private previews without maintenance controls', async () => {
  api.location.mockResolvedValue({ ...gallery, allowed_actions: [] })
  start(); await ready()
  expect(screen.getByText('拍摄限制：上课期间保持安静')).toBeTruthy()
  expect(screen.getByRole('img', { name: '入口与可用空间' }).getAttribute('src')).toBe('/api/v1/crews/crew/locations/location/photos/photo/content?variant=preview')
  expect(screen.getByRole('link', { name: '查看大图 1' }).getAttribute('href')).toContain('variant=full')
  expect(screen.queryByRole('button', { name: '上传勘景照片' })).toBeNull()
  expect(screen.queryByRole('button', { name: '编辑场地资料' })).toBeNull()
})

test('location details use resource revision and explicit confirmation', async () => {
  start(); await ready(); fireEvent.click(screen.getByRole('button', { name: '编辑场地资料' }))
  fireEvent.change(screen.getByLabelText('场地地址'), { target: { value: '南门入口' } })
  fireEvent.click(screen.getByRole('button', { name: '核对场地操作' }))
  expect(api.locationWrite).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: '确认场地操作' }))
  await waitFor(() => expect(api.locationWrite).toHaveBeenCalledTimes(1))
  expect(api.locationWrite.mock.calls[0][2]).toMatchObject({ action: 'edit_details', body: { expected_lock_version: 3, address: '南门入口', shooting_constraints: '上课期间保持安静' } })
})

test('upload freezes file and description and manually retries same key after an unknown result', async () => {
  api.uploadPhoto.mockRejectedValueOnce(new AccountError('NETWORK_UNCONFIRMED'))
  start(); await ready(); const file = await upload()
  expect(api.uploadPhoto).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: '确认场地操作' }))
  const retry = await screen.findByRole('button', { name: '用原场地请求重试' })
  expect(api.uploadPhoto).toHaveBeenCalledTimes(1)
  await waitFor(() => expect(retry.classList.contains('ant-btn-loading')).toBe(false))
  fireEvent.click(retry)
  await waitFor(() => expect(api.uploadPhoto).toHaveBeenCalledTimes(2))
  expect(api.uploadPhoto.mock.calls[0].slice(0, 5)).toEqual(['crew', 'location', file, '收音与入口', 3])
  expect(api.uploadPhoto.mock.calls[1]).toEqual(api.uploadPhoto.mock.calls[0])
})

test('version conflict reload preserves selected file and text while refreshing the write premise', async () => {
  api.uploadPhoto.mockRejectedValueOnce(new AccountError('VERSION_CONFLICT', 409))
  start(); await ready(); const file = await upload()
  fireEvent.click(screen.getByRole('button', { name: '确认场地操作' }))
  api.location.mockResolvedValue({ ...gallery, resource_lock_version: 8 })
  fireEvent.click(await screen.findByRole('button', { name: '读取最新场地并保留输入' }))
  await waitFor(() => expect(screen.queryByRole('button', { name: '读取最新场地并保留输入' })).toBeNull())
  expect(screen.getByText('已选：走廊.jpg')).toBeTruthy()
  expect((screen.getByLabelText('照片说明') as HTMLTextAreaElement).value).toBe('收音与入口')
  fireEvent.click(screen.getByRole('button', { name: '核对场地操作' }))
  fireEvent.click(screen.getByRole('button', { name: '确认场地操作' }))
  await waitFor(() => expect(api.uploadPhoto).toHaveBeenCalledTimes(2))
  expect(api.uploadPhoto.mock.calls[1].slice(0, 5)).toEqual(['crew', 'location', file, '收音与入口', 8])
})

test('photo removal requires confirmation and removes the image from returned gallery', async () => {
  api.locationWrite.mockResolvedValue({ ...gallery, photos: [], resource_lock_version: 4 })
  api.location.mockResolvedValueOnce(gallery).mockResolvedValue({ ...gallery, photos: [], resource_lock_version: 4 })
  start(); await ready(); fireEvent.click(screen.getByRole('button', { name: '移除照片 1' }))
  fireEvent.click(screen.getByRole('button', { name: '核对场地操作' }))
  expect(screen.getByText('照片将从场地资料中移除，已有查看链接也将失效。')).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: '确认场地操作' }))
  await screen.findByText('尚未上传勘景照片。')
  expect(api.locationWrite.mock.calls[0][2]).toMatchObject({ action: 'remove_photo', photoId: 'photo', body: { expected_lock_version: 3 } })
  expect(screen.queryByRole('img', { name: '入口与可用空间' })).toBeNull()
})

test('refresh denial hides old private location text and images', async () => {
  start(); await ready()
  api.location.mockRejectedValue(new AccountError('FORBIDDEN', 403))
  fireEvent.click(screen.getByRole('button', { name: '刷新场地资料' }))
  await screen.findByText('当前没有执行此操作的权限，请刷新后检查。')
  expect(screen.queryByRole('img', { name: '入口与可用空间' })).toBeNull()
  expect(screen.queryByText('地址：教学楼东侧')).toBeNull()
})

test('a failed preview can retry locally without replacing other gallery content', async () => {
  start(); await ready(); fireEvent.error(screen.getByRole('img', { name: '入口与可用空间' }))
  expect(screen.getByText('照片读取失败。')).toBeTruthy()
  expect(screen.getByText('拍摄限制：上课期间保持安静')).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: '重新读取照片 1' }))
  expect(screen.getByRole('img', { name: '入口与可用空间' })).toBeTruthy()
})

test('twenty active photos disable additional uploads', async () => {
  api.location.mockResolvedValue({ ...gallery, photos: Array.from({ length: 20 }, (_, index) => ({ ...gallery.photos[0], id: `photo-${index}` })) })
  start(); await ready()
  expect((screen.getByRole('button', { name: '上传勘景照片' }) as HTMLButtonElement).disabled).toBe(true)
})
