import type { HttpClient } from './http'
import type { Page } from './crews'

export type ChangeCategory = 'breakdowns' | 'storyboards' | 'plans' | 'call_sheets'
export type ChangeIssue = { id: string; category: ChangeCategory; parent_id: string; source_id: string | null; container_id: string | null; current_plan_version_id: string | null; title: string; status: string; reason_codes: string[] }
export type ChangeIssuePage = Page<ChangeIssue> & { category: ChangeCategory; allowed_categories: ChangeCategory[]; observed_at: string }
export type PlanTarget = { planId: string; versionId: string; sheetId?: string; sheetVersionId?: string }
export type ImpactCheck = { id: string; category: ChangeCategory; checked_at: string; detected_count: number; updated_count: number; resolved_count: number; reopened_count: number; open_count: number }
export type RecordedIssue = { id: string; target: ChangeIssue; status: 'open' | 'resolved'; first_detected_at: string; last_checked_at: string; resolved_at: string | null; reopened_count: number; lock_version: number; resolution_reason: string | null }
export type RecordedIssuePage = Page<RecordedIssue> & { latest_check: ImpactCheck | null }
export type ImpactTransition = { id: string; check_id: string; kind: 'detected' | 'updated' | 'resolved' | 'reopened'; recorded_at: string; before_status: string | null; after_status: string; target: ChangeIssue; evidence: Record<string, unknown>; resolution_reason: string | null }
export type ImpactHistoryPage = Page<ImpactTransition> & { issue: RecordedIssue }
export function changeCategories(permissions: string[]): ChangeCategory[] {
  const result: ChangeCategory[] = []
  if (permissions.includes('script.read')) {
    if (permissions.includes('scene.read')) result.push('breakdowns')
    if (permissions.includes('storyboard.read')) result.push('storyboards')
  }
  if (['plan.read_all', 'plan.edit'].some(code => permissions.includes(code))) result.push('plans')
  if (['call_sheet.edit', 'call_sheet.review', 'call_sheet.publish'].some(code => permissions.includes(code))) result.push('call_sheets')
  return result
}
export function createChangeClient(http: HttpClient) {
  const base = (crew: string, category: ChangeCategory) => `/crews/${crew}/change-issues/${category}`
  return {
    list: (crew: string, category: ChangeCategory, page: number, signal?: AbortSignal) => http.request(`${base(crew,category)}?page=${page}&page_size=20`, { signal }) as Promise<ChangeIssuePage>,
    check: (crew: string, category: ChangeCategory, key: string) => http.mutate(`${base(crew,category)}/checks`, {}, 'POST', [201], key) as Promise<ImpactCheck>,
    records: (crew: string, category: ChangeCategory, status: 'all' | 'open' | 'resolved', page: number, signal?: AbortSignal) => http.request(`${base(crew,category)}/records?status=${status}&page=${page}&page_size=20`, { signal }) as Promise<RecordedIssuePage>,
    history: (crew: string, category: ChangeCategory, issue: string, page: number, signal?: AbortSignal) => http.request(`${base(crew,category)}/records/${issue}/history?page=${page}&page_size=20`, { signal }) as Promise<ImpactHistoryPage>,
  }
}
export type ChangeClient = ReturnType<typeof createChangeClient>
