import type { HttpClient } from './http'
import type { Page } from './crews'
export type Script = { id: string; crew_id: string; title: string; current_version_id: string | null; lock_version: number; created_at: string; updated_at: string }
export type ScriptVersion = { id: string; script_id: string; version_no: number; status: 'pending_check' | 'confirmed'; lock_version: number; confirmed_by: string | null; confirmed_at: string | null }
export type ScriptSourceFile = { id: string; original_name: string; file_format: string; byte_size: number; page_count: number | null; extraction_notes: string[] }
export type VersionText = ScriptVersion & { source_file: ScriptSourceFile | null; paragraphs: { id: string; paragraph_no: number; text: string; source_page: number | null }[] }
export type ScriptDetail = { script: Script; version: VersionText | null }
export type ScriptCommand = { action: 'create' | 'add_version' | 'correct' | 'confirm' | 'set_working'; scriptId?: string; versionId?: string; key: string; body: Record<string, unknown> }
export function createScriptClient(http: HttpClient) {
  const base = (crew: string) => `/crews/${crew}/scripts`
  return {
    list: (crew: string, page: number, signal?: AbortSignal) => http.request(`${base(crew)}?page=${page}&page_size=20`, { signal }) as Promise<Page<Script>>,
    get: (crew: string, id: string, version?: string) => http.request(`${base(crew)}/${id}${version ? `/versions/${version}` : ''}`) as Promise<ScriptDetail>,
    versions: (crew: string, id: string, page: number, signal?: AbortSignal) => http.request(`${base(crew)}/${id}/versions?page=${page}&page_size=20`, { signal }) as Promise<Page<ScriptVersion>>,
    importFile: (crew: string, id: string, file: File, version: number, key: string) => {
      const form = new FormData()
      form.append('file', file); form.append('expected_lock_version', String(version))
      return http.upload(`${base(crew)}/${id}/imports`, form, key) as Promise<ScriptDetail>
    },
    execute: (crew: string, command: ScriptCommand) => {
      const suffix = command.action === 'create' ? '' : `/${command.scriptId}${command.action === 'add_version' ? '/versions' : command.action === 'set_working' ? '/working-version' : `/versions/${command.versionId}${command.action === 'confirm' ? '/confirm' : ''}`}`
      return http.mutate(`${base(crew)}${suffix}`, command.body, command.action === 'correct' ? 'PATCH' : command.action === 'set_working' ? 'PUT' : 'POST', ['create', 'add_version'].includes(command.action) ? [201] : [200], command.key) as Promise<ScriptDetail>
    },
  }
}
export type ScriptClient = ReturnType<typeof createScriptClient>
