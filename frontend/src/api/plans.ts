import type { HttpClient } from './http'
import type { Page } from './crews'

export type Plan = { id: string; name: string; created_at: string; current_published_version_id: string | null }
export type PlanMember = { member_id: string; membership_joined_at: string; display_name: string }
export type PlanResource = { resource_id: string; quantity: number; reservation_id: string | null; name: string; resource_type: string; unit: string }
export type PlanSession = { id?: string; sequence_no?: number; scene_revision_id: string; scene_label: string; starts_at: string; ends_at: string; transition_minutes: number; note: string | null; location_resource_id: string | null; location_reservation_id: string | null; location_name: string | null; members: PlanMember[]; resources: PlanResource[]; characters?: { character_name: string; display_name: string | null }[] }
export type PlanVersionSummary = { id: string; plan_id: string; version_no: number; status: 'draft' | 'published'; lock_version: number; published_at: string | null; withdrawn_at?: string | null; withdrawal_reason?: string | null }
export type PlanVersion = PlanVersionSummary & { sessions: PlanSession[]; review_reasons: string[] }
export type PlanIssue = { code: string; message: string; session_id: string | null; member_id: string | null; resource_id: string | null; starts_at: string | null; ends_at: string | null }
export type PlanCheck = { checked_lock_version: number; blockers: PlanIssue[]; unknown_items: (PlanIssue & { item_id: string })[]; warnings: PlanIssue[]; can_publish: boolean; limitations: string[]; checked_data_revision: string; expected_current_published_version_id: string | null; expires_at: string }
export type PlanPublishBody = { expected_lock_version: number; expected_current_published_version_id: string | null; checked_data_revision: string; confirmed_unknown_item_ids: string[] }
export type PlanWithdrawBody = { expected_lock_version: number; reason: string }
export type PlanWithdrawalResult = { withdrawn_version_id: string; lock_version: number; withdrawn_at: string; withdrawal_reason: string; affected_call_sheet_version_ids: string[]; notification_status: string }
export type PlanPublication = { published_version_id: string; status: 'published'; lock_version: number; previous_published_version_id: string | null; notification_status: string }
export type NextShoot = { session_id: string; plan_name: string; scene_label: string; location_name: string; starts_at: string; ends_at: string; review_reasons: string[] }
export type PlanChoiceKind = 'scenes' | 'members' | 'resources' | 'reservations'
export type PlanChoice = { id: string; label: string; resource_type: string | null; unit: string | null; membership_joined_at: string | null; quantity: number | null }
export type PlanCommand = { action: 'create' | 'version' | 'save'; target: string | null; body: Record<string, unknown>; key: string }

export function createPlanClient(http: HttpClient) {
  const base = (crew: string) => `/crews/${crew}`
  return {
    list: (crew: string, page: number, signal?: AbortSignal) => http.request(`${base(crew)}/shooting-plans?page=${page}&page_size=20`, { signal }) as Promise<Page<Plan>>,
    plan: (crew: string, id: string, signal?: AbortSignal) => http.request(`${base(crew)}/shooting-plans/${id}`, { signal }) as Promise<Plan>,
    versions: (crew: string, plan: string, page: number, signal?: AbortSignal) => http.request(`${base(crew)}/shooting-plans/${plan}/versions?page=${page}&page_size=20`, { signal }) as Promise<Page<PlanVersionSummary>>,
    get: (crew: string, id: string) => http.request(`${base(crew)}/plan-versions/${id}`) as Promise<PlanVersion>,
    next: (crew: string, signal?: AbortSignal) => http.request(`${base(crew)}/shooting-sessions/next`, { signal }) as Promise<NextShoot | null>,
    choices: (crew: string, kind: PlanChoiceKind, page: number, resource?: string, signal?: AbortSignal) => http.request(`${base(crew)}/shooting-plan-options/${kind}?page=${page}&page_size=20${resource ? `&resource_id=${resource}` : ''}`, { signal }) as Promise<Page<PlanChoice>>,
    check: (crew: string, id: string, version: number) => http.mutate(`${base(crew)}/plan-versions/${id}/preflight`, { expected_lock_version: version }) as Promise<PlanCheck>,
    publish: (crew: string, id: string, body: PlanPublishBody, key: string) => http.mutate(`${base(crew)}/plan-versions/${id}/publish`, body, 'POST', [200], key) as Promise<PlanPublication>,
    withdraw: (crew: string, id: string, body: PlanWithdrawBody, key: string) => http.mutate(`${base(crew)}/plan-versions/${id}/withdraw`, body, 'POST', [200], key) as Promise<PlanWithdrawalResult>,
    write: (crew: string, command: PlanCommand) => http.mutate(`${base(crew)}/${command.action === 'create' ? 'shooting-plans' : command.action === 'version' ? `shooting-plans/${command.target}/versions` : `plan-versions/${command.target}/sessions`}`, command.body, command.action === 'save' ? 'PUT' : 'POST', command.action === 'save' ? [200] : [201], command.key) as Promise<Plan | PlanVersion>,
  }
}
export type PlanClient = ReturnType<typeof createPlanClient>
