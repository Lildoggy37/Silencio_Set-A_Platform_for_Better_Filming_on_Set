import { expect, test } from '@playwright/test'
import { readFileSync } from 'node:fs'
import { execFileSync } from 'node:child_process'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'

test.beforeAll(() => {
  const backend = fileURLToPath(new URL('../../backend/', import.meta.url))
  execFileSync(join(backend, '.venv/Scripts/python.exe'), ['scripts/browser_preview.py', '--seed-only'], { cwd: backend })
})

// No mocked requests, injected sessions, production credentials or model calls.
test('login, profile, crew roles and preparation workspaces', async ({ page }, testInfo) => {
  const fixture = JSON.parse(readFileSync(new URL('../../runtime/local-services/browser-fixture.json', import.meta.url), 'utf8'))
  const pageErrors: string[] = []
  const failedResponses: string[] = []
  page.on('pageerror', error => pageErrors.push(error.message))
  page.on('response', response => {
    const path = new URL(response.url()).pathname
    // Initial anonymous session lookup is expected to return 401.
    if (path.startsWith('/api/') && response.status() >= 400 && !(path.endsWith('/users/me') && response.status() === 401)
        && !path.endsWith('/health/ready')) failedResponses.push(`${response.status()} ${path}`)
  })
  await page.goto('/')
  await page.getByLabel('邮箱', { exact: true }).fill(fixture.email)
  await page.getByLabel('密码', { exact: true }).fill('local browser integration password')
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page.getByRole('heading', { name: '补充你的昵称' })).toBeVisible()
  await page.getByLabel('昵称', { exact: true }).fill('浏览器导演')
  await page.getByRole('button', { name: '保存昵称并继续' }).click()
  await expect(page.getByRole('heading', { name: '剧组总览', exact: true })).toBeVisible()
  await page.getByRole('button', { name: '选择或创建剧组', exact: true }).click()
  await page.getByRole('button', { name: '创建新剧组' }).click()
  await page.getByLabel('剧组名称', { exact: true }).fill('浏览器联调剧组')
  await page.getByLabel('影片名称', { exact: true }).fill('雨后的校园')
  await page.getByRole('button', { name: '确认创建剧组' }).click()
  await expect(page.locator('.project-context').getByText('《雨后的校园》', { exact: true })).toBeVisible()

  await page.getByRole('button', { name: '成员', exact: true }).click()
  await page.getByRole('button', { name: /管理.*的岗位/ }).click()
  await page.getByRole('checkbox', { name: '导演', exact: true }).check()
  await page.getByRole('checkbox', { name: '制片', exact: true }).check()
  await page.getByRole('button', { name: '核对岗位变更' }).click()
  await page.getByRole('button', { name: '确认修改岗位' }).click()
  await expect(page.getByRole('dialog')).toHaveCount(0)

  await page.getByRole('button', { name: '资源', exact: true }).click()
  await expect(page.getByRole('heading', { name: '资源工作区' })).toBeVisible()
  await page.getByRole('button', { name: '排期', exact: true }).click()
  await expect(page.getByRole('heading', { name: '排期工作区' })).toBeVisible()
  await page.getByRole('button', { name: '总览', exact: true }).click()
  await page.screenshot({ path: testInfo.outputPath('workspace-desktop.png'), fullPage: true, animations: 'disabled' })
  const panel = await page.locator('.shoot-panel').boundingBox()
  const grid = await page.locator('.workspace-grid').boundingBox()
  expect(panel!.width / grid!.width).toBeGreaterThanOrEqual(0.2)

  await page.reload()
  await expect(page.getByText('浏览器导演', { exact: true }).first()).toBeVisible()
  await page.getByRole('button', { name: '选择或创建剧组', exact: true }).click()
  await page.getByRole('button', { name: '浏览器联调剧组 · 雨后的校园', exact: true }).click()
  await expect(page.locator('.project-context').getByText('《雨后的校园》', { exact: true })).toBeVisible()
  await expect(page.locator('.ant-modal-wrap:visible')).toHaveCount(0)
  await page.setViewportSize({ width: 390, height: 844 })
  await page.screenshot({ path: testInfo.outputPath('workspace-mobile.png'), fullPage: true, animations: 'disabled' })
  const dimensions = await page.evaluate(() => ({ viewport:innerWidth, content:document.documentElement.scrollWidth }))
  expect(dimensions.content).toBeLessThanOrEqual(dimensions.viewport + 1)
  expect(pageErrors).toEqual([])
  expect(failedResponses).toEqual([])
})
