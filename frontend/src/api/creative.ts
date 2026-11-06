import type { HttpClient } from './http'
import type { Page } from './crews'
import { createCastingClient } from './casting'
import { createSourceReviewClient } from './sourceReviews'
export type CreativeKind = 'breakdowns' | 'storyboards'
export type SceneCharacter = { name: string }
export type SceneResourceNeed = { resource_type: 'equipment' | 'prop' | 'location' | 'consumable'; name: string; quantity: number; unit: string; resource_id: string | null }
export type CreativeItem = { id?: string; scene_id?: string; scene_number?: string; location_text?: string; time_of_day?: string; source_start?: number; source_end?: number; production_note?: string | null; characters?: SceneCharacter[]; resource_needs?: SceneResourceNeed[]; shot_number?: string; shot_size?: string; visual_content?: string; camera_movement?: string | null; dialogue_sound?: string | null; duration_seconds?: number | null; sort_order?: number }
export type Creative = { created_by?: string | null; id: string; kind: CreativeKind; source_id: string; version_no: number; review_status: 'draft' | 'pending_review' | 'confirmed' | 'rejected'; lock_version: number; reviewed_by: string | null; reviewed_at: string | null; items: CreativeItem[] }
export type CreativeSummary = Omit<Creative, 'items'>
export type CreativeReview = { id: string; decision: string; review_note: string | null; reviewed_at: string; reviewed_lock_version: number; content_snapshot: Creative }
export function createCreativeClient(http: HttpClient) {
  const base = (crew: string, kind: CreativeKind) => `/crews/${crew}/${kind}`
  return {
    ...createCastingClient(http),
    ...createSourceReviewClient(http),
    list: (crew: string, kind: CreativeKind, source: string, page: number, signal?: AbortSignal) => http.request(`${base(crew, kind)}?source_id=${source}&page=${page}&page_size=20`, { signal }) as Promise<Page<CreativeSummary>>,
    get: (crew: string, kind: CreativeKind, id: string) => http.request(`${base(crew, kind)}/${id}`) as Promise<Creative>,
    reviews: (crew: string, kind: CreativeKind, id: string, page: number) => http.request(`${base(crew, kind)}/${id}/reviews?page=${page}&page_size=20`) as Promise<Page<CreativeReview>>,
    write: (crew: string, kind: CreativeKind, action: string, id: string | null, body: Record<string, unknown>, key: string) => http.mutate(`${base(crew, kind)}${action === 'create' ? '' : `/${id}${action === 'save' ? '' : action === 'review' ? '/reviews' : `/${action}`}`}`, body, action === 'save' ? 'PUT' : 'POST', action === 'create' ? [201] : [200], key) as Promise<Creative>,
  }
}
export type CreativeClient = ReturnType<typeof createCreativeClient>
