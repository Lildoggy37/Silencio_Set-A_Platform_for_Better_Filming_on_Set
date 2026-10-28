import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeAll, expect, test, vi } from 'vitest'
import App from '../App'
import { preloadScreens } from '../test-preload'

beforeAll(preloadScreens, 60_000)
const userId = '11111111-1111-4111-8111-111111111111'
const crewId = '22222222-2222-4222-8222-222222222222'
const taskId = '33333333-3333-4333-8333-333333333333'
const memberId = '44444444-4444-4444-8444-444444444444'
const submissionId = '55555555-5555-4555-8555-555555555555'
const user = { id: userId, email: 'director@example.com', display_name: '导演', profile_completed: true, missing_fields: [], lock_version: 2 }
const member = { id: memberId, display_name: '导演', status: 'active', role_codes: ['director'], lock_version: 1 }
const crew = { id: crewId, name: '学生剧组', project_name: '雨停之前', description: null, created_at: '2026-10-25T00:00:00Z', membership: member, permissions: ['crew.read', 'member.read', 'task.read', 'task.manage'] }
const task = { id: taskId, crew_id: crewId, title: '整理走廊分镜', description: '明确镜头顺序', owner_member_id: memberId, reviewer_member_id: memberId, created_by_member_id: memberId,
  collaborator_member_ids: [], due_at: '2099-01-01T00:00:00Z', status: 'pending', cancel_reason: null, lock_version: 1, created_at: '2026-10-28T00:00:00Z', updated_at: '2026-10-28T00:00:00Z', allowed_actions: ['start'] }
const submission = { id: submissionId, submission_no: 1, submitted_by: memberId, completion_note: '第一稿成果', review_status: 'pending', reviewed_by: null, review_note: null, submitted_at: '2026-10-28T00:00:00Z', reviewed_at: null }
const ok = (data: unknown, status = 200) => new Response(JSON.stringify({ data }), { status })
const fail = (code: string, status: number) => new Response(JSON.stringify({ error: { code } }), { status })

function start(handler: (path: string, options?: RequestInit) => Response | Promise<Response>, managing = true) {
  const fetcher = vi.fn((path: string, options?: RequestInit) => {
    if (path.endsWith('/users/me')) return Promise.resolve(ok(user))
    if (path.endsWith('/auth/csrf')) return Promise.resolve(ok({ csrf_token: 'c'.repeat(64) }))
    if (path.includes('/health/')) return Promise.resolve(ok({ status: 'alive' }))
    if (path.includes('/crews?')) return Promise.resolve(ok({ items: [crew], page: 1, page_size: 20, total: 1 }))
    if (path.endsWith(`/crews/${crewId}`)) return Promise.resolve(ok(managing ? crew : { ...crew, permissions: ['crew.read', 'task.read'] }))
    if (path.includes('/members?')) return Promise.resolve(ok({ items: [member], page: 1, page_size: 20, total: 1 }))
    return Promise.resolve(handler(path, options)).then(async response => {
      const payload = await response.clone().json()
      if (payload.data?.task) return ok({ ...payload.data, member_names: { [memberId]: '导演' } }, response.status)
      return response
    })
  })
  vi.stubGlobal('fetch', fetcher)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  render(<QueryClientProvider client={client}><App /></QueryClientProvider>)
  return fetcher
}
async function openBoard() {
  fireEvent.click(await screen.findByRole('button', { name: '选择或创建剧组' }))
  fireEvent.click(await screen.findByRole('button', { name: '学生剧组 · 雨停之前' }))
  await screen.findByRole('heading', { name: '筹备任务' })
}
const list = (row = task) => ok({ items: [row], page: 1, page_size: 20, total: 1 })

test('overview lists real tasks and a non-manager has no creation entry', async () => {
  start(path => path.includes('/tasks?') ? list() : ok({ task, current_submission: null, submission_count: 0 }), false)
  await openBoard()
  expect(await screen.findByText('整理走廊分镜')).toBeTruthy()
  expect(screen.queryByRole('button', { name: '创建任务' })).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: /整理走廊分镜/ }))
  await screen.findByRole('button', { name: '开始任务' })
  expect(screen.queryByRole('button', { name: '验收任务' })).toBeNull()
})

test('creation freezes unknown input and explicitly recovers with identical key and body', async () => {
  let attempts = 0
  const writes: RequestInit[] = []
  start((path, options) => {
    if (path.includes('/tasks?')) return list()
    if (path.endsWith('/tasks')) {
      writes.push(options!); attempts += 1
      if (attempts === 1) return Promise.reject(new TypeError('lost reply'))
      return ok(task, 201)
    }
    return ok({ task, current_submission: null, submission_count: 0 })
  })
  await openBoard()
  fireEvent.click(screen.getByRole('button', { name: '创建任务' }))
  fireEvent.change(await screen.findByLabelText('任务标题'), { target: { value: '整理走廊分镜' } })
  await screen.findAllByRole('option', { name: /导演 · 44444444/ })
  fireEvent.change(screen.getByLabelText('负责人'), { target: { value: memberId } })
  fireEvent.change(screen.getByLabelText('确认人'), { target: { value: memberId } })
  fireEvent.change(screen.getByLabelText('截止时间（北京时间）'), { target: { value: '2099-01-01T08:00' } })
  fireEvent.click(screen.getByRole('button', { name: '确认创建任务' }))
  const recover = await screen.findByRole('button', { name: '恢复这次操作结果' })
  expect(screen.getByLabelText('任务标题').hasAttribute('disabled')).toBe(true)
  expect(writes).toHaveLength(1)
  fireEvent.click(recover)
  await screen.findByRole('button', { name: '开始任务' })
  expect(writes).toHaveLength(2)
  expect(writes[1].body).toBe(writes[0].body)
  expect((writes[1].headers as Record<string, string>)['Idempotency-Key']).toBe((writes[0].headers as Record<string, string>)['Idempotency-Key'])
  expect(JSON.parse(String(writes[0].body)).due_at).toBe('2099-01-01T00:00:00.000Z')
})

test('review rejects blank reasons locally and sends the exact current submission and version', async () => {
  let reviewed = false
  const pending = { ...task, status: 'pending_review', lock_version: 3, allowed_actions: ['review'] }
  const fetcher = start((path, options) => {
    if (path.includes('/tasks?')) return list(pending)
    if (path.endsWith('/reviews')) {
      expect(JSON.parse(String(options?.body))).toEqual({ expected_lock_version: 3, submission_id: submissionId, decision: 'reject', review_note: '补充景别' })
      reviewed = true; return ok({ ...pending, status: 'in_progress', lock_version: 4, allowed_actions: ['submit'] })
    }
    return ok({ task: reviewed ? { ...pending, status: 'in_progress', lock_version: 4, allowed_actions: ['submit'] } : pending,
      current_submission: reviewed ? { ...submission, review_status: 'rejected', review_note: '补充景别', reviewed_by: memberId, reviewed_at: '2026-10-28T01:00:00Z' } : submission, submission_count: 1 })
  })
  await openBoard(); fireEvent.click(await screen.findByRole('button', { name: /整理走廊分镜/ }))
  fireEvent.click(await screen.findByRole('button', { name: '验收任务' }))
  fireEvent.change(await screen.findByLabelText('验收决定'), { target: { value: 'reject' } })
  fireEvent.click(screen.getByRole('button', { name: '确认验收任务' }))
  await screen.findByText('填写内容未通过服务端校验，请检查后重试。')
  expect(fetcher.mock.calls.some(([path]) => path.endsWith('/reviews'))).toBe(false)
  fireEvent.change(screen.getByLabelText('验收说明（退回时必填）'), { target: { value: ' 补充景别 ' } })
  fireEvent.click(screen.getByRole('button', { name: '确认验收任务' }))
  await screen.findByRole('button', { name: '提交成果' })
  expect(await screen.findByText('确认说明：补充景别')).toBeTruthy()
})

test('stale start blocks resubmission until a read and does not change state optimistically', async () => {
  let version = 1; let writes = 0
  const fetcher = start(path => {
    if (path.includes('/tasks?')) return list()
    if (path.endsWith('/start')) { writes += 1; version = 2; return fail('VERSION_CONFLICT', 409) }
    return ok({ task: { ...task, lock_version: version }, current_submission: null, submission_count: 0 })
  })
  await openBoard(); fireEvent.click(await screen.findByRole('button', { name: /整理走廊分镜/ }))
  fireEvent.click(await screen.findByRole('button', { name: '开始任务' }))
  fireEvent.click(await screen.findByRole('button', { name: '确认开始任务' }))
  fireEvent.click(await screen.findByRole('button', { name: '重新读取任务状态' }))
  await waitFor(() => expect(screen.getByRole('button', { name: '确认开始任务' }).hasAttribute('disabled')).toBe(false))
  expect(writes).toBe(1)
  expect(fetcher.mock.calls.filter(([path]) => path.endsWith('/start'))).toHaveLength(1)
  const form = screen.getByRole('button', { name: '确认开始任务' }).closest('form')!
  expect(within(form).getByText(/版本 2/)).toBeTruthy()
})

test('loss of current task access hides previously loaded submission history', async () => {
  let revoked = false
  const rejected = { ...submission, review_status: 'rejected', review_note: '调整', reviewed_by: memberId, reviewed_at: '2026-10-28T01:00:00Z' }
  start(path => {
    if (path.includes('/tasks?')) return list()
    if (path.includes('/submissions?')) return ok({ items: [{ ...rejected, completion_note: '历史私有成果' }], page: 1, page_size: 20, total: 1 })
    if (revoked) return fail('NOT_FOUND', 404)
    return ok({ task: { ...task, status: 'in_progress', allowed_actions: ['submit'] }, current_submission: rejected, submission_count: 1 })
  })
  await openBoard(); fireEvent.click(await screen.findByRole('button', { name: /整理走廊分镜/ }))
  fireEvent.click(await screen.findByRole('button', { name: '查看历次提交' }))
  await screen.findByText('历史私有成果')
  revoked = true
  fireEvent.click(screen.getByRole('button', { name: /刷新任务详情/ }))
  await screen.findByText('剧组不存在或无权访问，请刷新剧组列表。')
  expect(screen.queryByText('历史私有成果')).toBeNull()
})
