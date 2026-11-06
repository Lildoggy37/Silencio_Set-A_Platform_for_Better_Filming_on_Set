import type { HttpClient } from './http'
import type { Page } from './crews'

export type CastActor = { id: string; display_name: string; membership_joined_at: string }
export type CastingEntry = { character_name: string; is_current_character: boolean; member_id: string | null; membership_joined_at: string | null; display_name: string | null; status: 'unassigned' | 'active' | 'invalid' }
export type CastingPage = Page<CastingEntry> & { script_id: string; lock_version: number }
export type CastingBody = { expected_lock_version: number; character_name: string; member_id: string | null; membership_joined_at: string | null }
export type CastingResult = Omit<CastingBody, 'expected_lock_version'> & { script_id: string; lock_version: number; display_name: string | null }

export function createCastingClient(http: HttpClient) {
  const base = (crew: string, script: string) => `/crews/${crew}/scripts/${script}/casting`
  return {
    castingScripts: (crew: string, page: number, signal?: AbortSignal) => http.request(`/crews/${crew}/casting-scripts?page=${page}`, { signal }) as Promise<Page<{ id: string; title: string }>>,
    casting: (crew: string, script: string, page: number, signal?: AbortSignal) => http.request(`${base(crew, script)}?page=${page}`, { signal }) as Promise<CastingPage>,
    castingActors: (crew: string, script: string, page: number, signal?: AbortSignal) => http.request(`${base(crew, script)}/actors?page=${page}`, { signal }) as Promise<Page<CastActor>>,
    saveCasting: (crew: string, script: string, body: CastingBody, key: string) => http.mutate(base(crew, script), body, 'PATCH', [200], key) as Promise<CastingResult>,
  }
}
