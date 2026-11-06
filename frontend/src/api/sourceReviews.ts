import type { HttpClient } from './http'
import type { Page } from './crews'
import type { CreativeKind } from './creative'

export type SourceReview = { id: string; working_script_version_id: string; working_revision: number; target_lock_version: number; reviewed_by: string; conclusion: 'still_applicable' | 'needs_revision'; review_note: string; reviewed_at: string }
export type SourceAssessment = { kind: CreativeKind; id: string; source_id: string; version_no: number; review_status: string; lock_version: number; script_id: string; script_title: string; source_script_version_id: string; source_script_version_no: number; working_script_version_id: string | null; working_script_version_no: number | null; working_revision: number; status: 'current' | 'review_required' | 'still_applicable' | 'needs_revision' | 'no_working_version'; latest_review: SourceReview | null }
export type SourceReviewBody = { expected_lock_version: number; expected_working_version_id: string; expected_working_revision: number; expected_review_id: string | null; conclusion: SourceReview['conclusion']; review_note: string }
export function createSourceReviewClient(http: HttpClient) {
  const path = (crew: string, kind: CreativeKind, id: string) => `/crews/${crew}/${kind}/${id}`
  return {
    sourceAssessment: (crew: string, kind: CreativeKind, id: string, signal?: AbortSignal) => http.request(`${path(crew, kind, id)}/source-assessment`, { signal }) as Promise<SourceAssessment>,
    sourceImpacts: (crew: string, script: string, kind: CreativeKind, page: number, signal?: AbortSignal) => http.request(`/crews/${crew}/scripts/${script}/source-impacts?kind=${kind}&page=${page}`, { signal }) as Promise<Page<SourceAssessment>>,
    sourceReviewHistory: (crew: string, kind: CreativeKind, id: string, page: number, signal?: AbortSignal) => http.request(`${path(crew, kind, id)}/source-reviews?page=${page}`, { signal }) as Promise<Page<SourceReview>>,
    reviewSource: (crew: string, kind: CreativeKind, id: string, body: SourceReviewBody, key: string) => http.mutate(`${path(crew, kind, id)}/source-reviews`, body, 'POST', [201], key) as Promise<SourceAssessment>,
  }
}
