import { AccountError } from './accounts'
import type { HttpClient } from './http'
import type { Page } from './crews'

export const taskStatusNames = { pending: '待处理', in_progress: '进行中', pending_review: '待确认', completed: '已完成', cancelled: '已取消' }
export type TaskStatus = keyof typeof taskStatusNames
export type TaskAction = 'start' | 'submit' | 'review' | 'edit' | 'cancel'
export type Task = {
  id: string; crew_id: string; title: string; description: string | null; owner_member_id: string; reviewer_member_id: string; created_by_member_id: string
  collaborator_member_ids: string[]; due_at: string; status: TaskStatus; cancel_reason: string | null; lock_version: number; created_at: string; updated_at: string; allowed_actions: TaskAction[]
}
export type Submission = { id: string; submission_no: number; submitted_by: string; completion_note: string; review_status: 'pending' | 'accepted' | 'rejected' | 'cancelled'; reviewed_by: string | null; review_note: string | null; submitted_at: string; reviewed_at: string | null }
export type TaskDetail = { task: Task; member_names: Record<string, string>; current_submission: Submission | null; submission_count: number }
export type TaskInput = { title: string; description: string | null; owner_member_id: string; reviewer_member_id: string; collaborator_member_ids: string[]; due_at: string }
export type TaskCommand = { action: 'create' | TaskAction; taskId?: string; body: Record<string, unknown>; key?: string }
const uuid = /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i
const record = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object' && !Array.isArray(v)
const id = (v: unknown): v is string => typeof v === 'string' && uuid.test(v)
const integer = (v: unknown): v is number => typeof v === 'number' && Number.isSafeInteger(v) && v >= 0
const date = (v: unknown): v is string => typeof v === 'string' && Number.isFinite(Date.parse(v)) && /(?:Z|[+-]\d\d:\d\d)$/.test(v)
const text = (v: unknown, max: number): v is string => typeof v === 'string' && Array.from(v).length >= 1 && Array.from(v).length <= max && !/[\p{Cs}]/u.test(v)
const invalid = () => new AccountError('INVALID_RESPONSE')
function checkedId(v: string) { if (!id(v)) throw invalid(); return v }
function checkedPage(v: number) { if (!Number.isInteger(v) || v < 1 || v > 100_000) throw new AccountError('VALIDATION_ERROR'); return v }

function parseTask(v: unknown): Task {
  if (!record(v) || !id(v.id) || !id(v.crew_id) || !id(v.owner_member_id) || !id(v.reviewer_member_id) || !id(v.created_by_member_id)
    || !text(v.title, 100) || !(v.description === null || text(v.description, 3000)) || !date(v.due_at) || !date(v.created_at) || !date(v.updated_at)
    || typeof v.status !== 'string' || !Object.hasOwn(taskStatusNames, v.status) || !integer(v.lock_version) || v.lock_version < 1 || v.lock_version > 2_147_483_647
    || !Array.isArray(v.collaborator_member_ids) || v.collaborator_member_ids.length > 20 || !v.collaborator_member_ids.every(id) || new Set(v.collaborator_member_ids).size !== v.collaborator_member_ids.length
    || !Array.isArray(v.allowed_actions) || !v.allowed_actions.every(a => ['start', 'submit', 'review', 'edit', 'cancel'].includes(String(a)))
    || !(v.status === 'cancelled' ? text(v.cancel_reason, 500) : v.cancel_reason === null)) throw invalid()
  return { id: v.id, crew_id: v.crew_id, title: v.title, description: v.description, due_at: v.due_at, owner_member_id: v.owner_member_id,
    reviewer_member_id: v.reviewer_member_id, created_by_member_id: v.created_by_member_id, status: v.status as TaskStatus, cancel_reason: v.cancel_reason as string | null,
    collaborator_member_ids: [...v.collaborator_member_ids], allowed_actions: [...v.allowed_actions] as TaskAction[], lock_version: v.lock_version, created_at: v.created_at, updated_at: v.updated_at }
}
function parseSubmission(v: unknown): Submission {
  if (!record(v) || !id(v.id) || !id(v.submitted_by) || !integer(v.submission_no) || v.submission_no < 1 || !text(v.completion_note, 2000)
    || !['pending', 'accepted', 'rejected', 'cancelled'].includes(String(v.review_status)) || !date(v.submitted_at)
    || !(v.review_note === null || text(v.review_note, 500))
    || !(v.review_status === 'pending' ? v.reviewed_by === null && v.reviewed_at === null : id(v.reviewed_by) && date(v.reviewed_at))) throw invalid()
  if (v.review_status === 'rejected' && !text(v.review_note, 500)) throw invalid()
  return { id: v.id, submission_no: v.submission_no, submitted_by: v.submitted_by, completion_note: v.completion_note, review_status: v.review_status as Submission['review_status'],
    reviewed_by: v.reviewed_by as string | null, review_note: v.review_note as string | null, submitted_at: v.submitted_at, reviewed_at: v.reviewed_at as string | null }
}
function parsePage<T>(v: unknown, number: number, parse: (item: unknown) => T): Page<T> {
  if (!record(v) || !Array.isArray(v.items) || v.items.length > 20 || v.page !== number || v.page_size !== 20 || !integer(v.total) || v.total < v.items.length) throw invalid()
  return { items: v.items.map(parse), page: number, page_size: 20, total: v.total }
}

export function createTaskClient(http: HttpClient) {
  return {
    async list(crewId: string, number = 1, status: TaskStatus | '' = '', signal?: AbortSignal) {
      if (status && !Object.hasOwn(taskStatusNames, status)) throw new AccountError('VALIDATION_ERROR')
      const result = parsePage(await http.request(`/crews/${checkedId(crewId)}/tasks?page=${checkedPage(number)}&page_size=20${status ? `&status=${status}` : ''}`, { signal }), number, parseTask)
      if (result.items.some(task => task.crew_id !== crewId)) throw invalid()
      return result
    },
    async get(crewId: string, taskId: string, signal?: AbortSignal): Promise<TaskDetail> {
      const v = await http.request(`/crews/${checkedId(crewId)}/tasks/${checkedId(taskId)}`, { signal })
      if (!record(v) || !integer(v.submission_count)) throw invalid()
      if (!record(v.member_names) || Object.keys(v.member_names).length > 22 || !Object.entries(v.member_names).every(([key, value]) => id(key) && text(value, 30))) throw invalid()
      const task = parseTask(v.task)
      if (task.id !== taskId || task.crew_id !== crewId) throw invalid()
      const current = v.current_submission === null ? null : parseSubmission(v.current_submission)
      if ((v.submission_count === 0) !== (current === null) || task.status === 'pending_review' && current?.review_status !== 'pending'
        || task.status !== 'pending_review' && current?.review_status === 'pending') throw invalid()
      return { task, member_names: { ...v.member_names } as Record<string, string>, current_submission: current, submission_count: v.submission_count }
    },
    async history(crewId: string, taskId: string, number = 1, signal?: AbortSignal) {
      return parsePage(await http.request(`/crews/${checkedId(crewId)}/tasks/${checkedId(taskId)}/submissions?page=${checkedPage(number)}&page_size=20`, { signal }), number, parseSubmission)
    },
    async execute(crewId: string, command: TaskCommand) {
      const keyed = ['create', 'submit', 'review', 'cancel'].includes(command.action)
      if (keyed && !id(command.key)) throw new AccountError('VALIDATION_ERROR')
      if (!['create', 'edit', 'start', 'submit', 'review', 'cancel'].includes(command.action)) throw new AccountError('VALIDATION_ERROR')
      const suffixes = { edit: '', start: '/start', submit: '/submissions', review: '/reviews', cancel: '/cancel' }
      const path = `/crews/${checkedId(crewId)}/tasks${command.action === 'create' ? '' : `/${checkedId(command.taskId!)}${suffixes[command.action]}`}`
      const result = parseTask(await http.mutate(path, command.body, command.action === 'edit' ? 'PATCH' : 'POST', command.action === 'create' ? [201] : [200], keyed ? command.key : undefined))
      if (result.crew_id !== crewId || command.taskId && result.id !== command.taskId) throw invalid()
      if (command.action !== 'create' && result.lock_version !== Number(command.body.expected_lock_version) + 1) throw invalid()
      const expectedStatus = command.action === 'create' ? 'pending' : command.action === 'start' ? 'in_progress' : command.action === 'submit' ? 'pending_review'
        : command.action === 'cancel' ? 'cancelled' : command.action === 'review' ? command.body.decision === 'accept' ? 'completed' : 'in_progress' : undefined
      if (expectedStatus && result.status !== expectedStatus || command.action === 'create' && result.lock_version !== 1) throw invalid()
      return result
    },
  }
}
export type TaskClient = ReturnType<typeof createTaskClient>
