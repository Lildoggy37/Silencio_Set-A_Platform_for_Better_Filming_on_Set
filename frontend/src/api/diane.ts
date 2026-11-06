import type { HttpClient } from './http'
import { AccountError } from './http'

export type DianeStatus = { enabled: boolean; model?: string | null; mode: 'read_only' | 'queries_and_task_proposals'; timeout_seconds: number; execution_mode?: 'synchronous' | 'background' }
export type DianeSource = { kind: string; label: string; record_id: string | null }
export type TaskProposal = {
  id: string; crew_id: string; status: 'pending' | 'completed' | 'rejected'; lock_version: number
  tasks: { title: string; description: string | null; owner_member_id: string; due_at: string; requires_review: boolean }[]
  member_names: Record<string, string>; created_task_ids: string[]; created_at: string; expires_at: string; decided_at: string | null
}
export type DianeResult = {
  run_id: string; status: 'completed' | 'budget_exceeded' | 'failed' | 'timed_out'; answer: string; stop_reason: string | null
  tools: { name: string; status: string; elapsed_ms: number; error_code: string | null; source: DianeSource | null }[]
  sources: DianeSource[]
  usage: { elapsed_ms: number; model_ms: number; tool_ms: number; input_tokens: number | null; output_tokens: number | null; total_tokens: number | null; usage_complete: boolean; budget_charged_tokens: number; budget_contains_estimates: boolean }
}
export type DianeConversation = { id: string; title: string; created_at: string; updated_at: string }
export type DianeTurn = { id: string; ordinal: number; question: string; status: 'queued' | 'running' | 'completed' | 'failed' | 'interrupted'; phase: string; progress_version: number; result: DianeResult | null; created_at: string }
export type DianeHistory = { conversation: DianeConversation; turns: DianeTurn[]; has_earlier: boolean }
export type DianePreferences = { response_style: 'concise' | 'detailed'; focus: 'general' | 'schedule' | 'resources' }

export function createDianeClient(http: HttpClient) {
  const base = (crew: string) => `/crews/${crew}/diane`
  return {
    enqueue: (crew: string, id: string, body: {question:string;turn_id:string}) =>
      http.mutate(`${base(crew)}/conversations/${id}/runs`,body,'POST',[202]) as Promise<DianeTurn>,
    watchTurn(crew: string, id: string, turnId: string, onTurn: (turn:DianeTurn)=>void, onConnection: (connected:boolean)=>void, onFailure:(error:unknown)=>void) {
      const stream=new EventSource(`/api/v1${base(crew)}/conversations/${id}/runs/${turnId}/events`)
      stream.onopen=()=>onConnection(true)
      stream.onerror=()=>onConnection(false) // EventSource reconnects reads, never POSTs.
      stream.addEventListener('turn',event=>{
        const turn=JSON.parse((event as MessageEvent).data) as DianeTurn
        onTurn(turn)
        if(!['queued','running'].includes(turn.status))stream.close()
      })
      stream.addEventListener('access_revoked',event=>{stream.close();onFailure(new AccountError(JSON.parse((event as MessageEvent).data).code))})
      return ()=>stream.close()
    },
    conversations: (crew: string, signal?: AbortSignal) => http.request(`${base(crew)}/conversations`, {signal}) as Promise<DianeConversation[]>,
    createConversation: (crew: string) => http.mutate(`${base(crew)}/conversations`, {}, 'POST', [201]) as Promise<DianeConversation>,
    history: (crew: string, id: string, signal?: AbortSignal, before?: number) =>
      http.request(`${base(crew)}/conversations/${id}${before ? `?before=${before}` : ''}`, {signal}) as Promise<DianeHistory>,
    preferences: (crew: string, signal?: AbortSignal) => http.request(`${base(crew)}/preferences`, {signal}) as Promise<DianePreferences>,
    savePreferences: (crew: string, body: DianePreferences) => http.mutate(`${base(crew)}/preferences`, body, 'PUT') as Promise<DianePreferences>,
    async converse(crew: string, id: string, body: {question: string; turn_id: string}, timeoutSeconds: number) {
      const csrf = await http.prepareCsrf()
      return await http.request(`${base(crew)}/conversations/${id}/queries`, {method:'POST', body, csrf,
        timeoutMs:(timeoutSeconds+10)*1000}) as DianeTurn
    },
    status: (crew: string, signal?: AbortSignal) => http.request(base(crew), { signal }) as Promise<DianeStatus>,
    proposals: (crew: string, signal?: AbortSignal) => http.request(`${base(crew)}/task-proposals`, { signal }) as Promise<{items:TaskProposal[]}>,
    async draftTasks(crew: string, body: {question: string; owner_member_id: string; due_at: string}, timeoutSeconds: number) {
      const csrf = await http.prepareCsrf()
      return await http.request(`${base(crew)}/task-proposals/draft`, { method:'POST', body, csrf,
        timeoutMs:(timeoutSeconds+5)*1000 }) as {run:DianeResult; proposal:TaskProposal|null}
    },
    decide: (crew: string, id: string, body: {expected_lock_version:number; decision:'accept'|'reject'}) =>
      http.mutate(`${base(crew)}/task-proposals/${id}/decisions`, body) as Promise<TaskProposal>,
    async query(crew: string, question: string, timeoutSeconds: number): Promise<DianeResult> {
      const csrf = await http.prepareCsrf()
      return await http.request(`${base(crew)}/queries`, { method: 'POST', body: { question }, csrf,
        timeoutMs: (timeoutSeconds + 5) * 1000 }) as DianeResult
    },
  }
}
export type DianeClient = ReturnType<typeof createDianeClient>
