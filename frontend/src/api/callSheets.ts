import type { HttpClient } from './http'
import type { Page } from './crews'
import type { PlanSession } from './plans'

export type CallSheet = { id: string; plan_id: string; shooting_date: string; current_published_version_id: string | null }
export type CallSheetSummary = { id: string; call_sheet_id: string; source_plan_version_id: string; version_no: number; lock_version: number; status: 'draft' | 'pending_review' | 'approved' | 'published'; validity_status: 'valid' | 'review_required' | 'superseded' | 'withdrawn' | null }
export type SheetArrangement = { project_name: string; plan_name: string; shooting_date: string; plan_version_no: number; sessions: PlanSession[]; overnight_continuations: Pick<PlanSession, 'scene_label' | 'starts_at' | 'ends_at'>[] }
export type SheetContent = { gathering_at: string | null; gathering_location: string | null; contact_note: string | null; notes: string | null; arrangement_snapshot: SheetArrangement }
export type SheetRecipient = { member_id: string; display_name: string; required: boolean }
export type CallSheetVersion = CallSheetSummary & SheetContent & { call_sheet: CallSheet; source_current: boolean; recipients: SheetRecipient[]; reviewed_at: string | null; published_at: string | null; review_id: string | null; notification_status: string | null }
export type SheetReview = { id: string; reviewed_by: string; reviewed_at: string; decision: 'accept' | 'reject'; review_note: string | null; reviewed_lock_version: number; content_snapshot: SheetContent & { recipients: { member_id: string; display_name: string }[] } }
export type SheetAction = 'create' | 'version' | 'edit' | 'recipients' | 'submit' | 'review' | 'publish'
export type SheetCommand = { action: SheetAction; target: string | null; body: Record<string, unknown>; key: string }

export function createCallSheetClient(http: HttpClient) {
  const base = (crew: string) => `/crews/${crew}`
  return {
    list: (crew: string, plan: string, page: number, signal?: AbortSignal) => http.request(`${base(crew)}/call-sheets?plan_id=${plan}&page=${page}&page_size=20`, { signal }) as Promise<Page<CallSheet>>,
    versions: (crew: string, sheet: string, page: number, signal?: AbortSignal) => http.request(`${base(crew)}/call-sheets/${sheet}/versions?page=${page}&page_size=20`, { signal }) as Promise<Page<CallSheetSummary>>,
    get: (crew: string, id: string) => http.request(`${base(crew)}/call-sheet-versions/${id}`) as Promise<CallSheetVersion>,
    exportPdf: (crew: string, id: string, version: number) => http.downloadPdf(`${base(crew)}/call-sheet-versions/${id}/export.pdf?expected_lock_version=${version}`),
    reviews: (crew: string, id: string, page: number) => http.request(`${base(crew)}/call-sheet-versions/${id}/reviews?page=${page}&page_size=20`) as Promise<Page<SheetReview>>,
    recipients: (crew: string, id: string, page: number, signal?: AbortSignal) => http.request(`${base(crew)}/call-sheet-versions/${id}/recipient-options?page=${page}&page_size=20`, { signal }) as Promise<Page<SheetRecipient>>,
    write: (crew: string, command: SheetCommand) => {
      const suffix = command.action === 'create' ? 'call-sheets' : command.action === 'version' ? `call-sheets/${command.target}/versions`
        : `call-sheet-versions/${command.target}${command.action === 'edit' ? '' : command.action === 'review' ? '/reviews' : `/${command.action}`}`
      return http.mutate(`${base(crew)}/${suffix}`, command.body, command.action === 'edit' ? 'PATCH' : command.action === 'recipients' ? 'PUT' : 'POST', ['create', 'version'].includes(command.action) ? [201] : [200], command.key) as Promise<CallSheetVersion>
    },
  }
}
export type CallSheetClient = ReturnType<typeof createCallSheetClient>
