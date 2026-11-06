import { expect, test } from '@playwright/test'
import { readFileSync } from 'node:fs'

test('corrects a scheduling conflict and reissues a published call sheet', async ({ page }) => {
  const errors: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  page.on('response', response => {
    const path = new URL(response.url()).pathname
    if (path.startsWith('/api/') && response.status() >= 400
        && !(path.endsWith('/users/me') && response.status() === 401)
        && !path.endsWith('/health/ready')) errors.push(`${response.status()} ${path}`)
  })
  const button = (name: string) => page.getByRole('button', { name, exact: true })
  const confirmSheetAction = async () => {
    const dialog = page.getByRole('dialog')
    const save = dialog.getByRole('button', { name: '保存通告修改' })
    const publish = dialog.getByRole('button', { name: '确认发布通告' })
    if (await save.count()) await save.click()
    else if (await publish.count()) await publish.click()
    else {
      await dialog.getByRole('button', { name: '核对通告操作' }).click()
      await dialog.getByRole('button', { name: '确认通告操作' }).click()
    }
    await expect(dialog).toHaveCount(0)
  }
  const confirmPlanAction = async () => {
    const dialog = page.getByRole('dialog')
    await dialog.getByRole('button', { name: '确认排期操作' }).click()
    await expect(dialog).toHaveCount(0)
  }

  await page.goto('/')
  await page.getByLabel('邮箱', { exact: true }).fill('director@example.com')
  await page.getByLabel('密码', { exact: true }).fill('postgres integration sample password')
  await button('登录').click()
  await button('选择或创建剧组').click()
  await button('寂静剧组 · 雨停之前').click()
  await button('排期').click()
  await button('第一拍摄日').click()
  await button('计划第 1 版 · 已撤回').click()
  await button('复制为新计划草稿').click()
  await confirmPlanAction()
  await expect(page.getByRole('heading', { name: /计划第 \d+ 版 · 草稿/ })).toBeVisible()
  await expect(page.getByRole('heading', { name: '计划第 1 版 · 已撤回' })).toHaveCount(0)

  const end = page.getByLabel('拍摄结束 1', { exact: true })
  const originalEnd = await end.inputValue()
  const day = originalEnd.slice(0, 10)
  await end.fill(`${day}T13:00`)
  await button('保存排期').click()
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await button('检查排期冲突').click()
  const report = page.getByRole('region', { name: '排期检查结果' })
  await expect(report.getByText('缺少覆盖本场时段及数量的已批准预约', { exact: false })).toBeVisible()
  await expect(button('核对并发布排期')).toBeDisabled()

  await end.fill(originalEnd)
  await button('保存排期').click()
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await button('检查排期冲突').click()
  await expect(report.getByText('阻断 0 项 · 档期未知 0 段')).toBeVisible()
  await button('核对并发布排期').click()
  await button('确认发布正式排期').click()
  await expect(page.getByRole('heading', { name: /计划第 \d+ 版 · 已发布/ })).toBeVisible()

  await button(`${day} 通告`).click()
  await button('按当前排期创建通告新版本').click()
  await confirmSheetAction()
  await button('编辑通告内容').click()
  await page.getByLabel('集合时间', { exact: true }).fill(`${day}T09:30`)
  await page.getByLabel('集合地点', { exact: true }).fill('教学楼东侧入口')
  await page.getByLabel('注意事项', { exact: true }).fill('请提前核对拍摄设备。')
  await confirmSheetAction()
  await button('发布当前通告').click()
  await confirmSheetAction()
  await expect(page.getByRole('heading', { name: /通告第 \d+ 版 · 已发布/ })).toBeVisible()
  await expect(page.getByText('当前有效', { exact: true })).toBeVisible()
  await expect(page.getByText(/注意事项：请提前核对拍摄设备。/)).toBeVisible()
  await expect(page.getByText(/演员同学（必选）/)).toBeVisible()

  const downloadEvent = page.waitForEvent('download')
  await button('导出当前所选版本PDF').click()
  const download = await downloadEvent
  expect(download.suggestedFilename()).toMatch(/^SilencioSet-.*\.pdf$/)
  expect(readFileSync(await download.path()).subarray(0, 5).toString()).toBe('%PDF-')
  expect(errors).toEqual([])
})
