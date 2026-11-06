import type { HttpClient } from './http'
import type { Page } from './crews'
export type Stock = { id: string; crew_id: string; name: string; unit: string; stock_quantity: number; manager_member_id: string; status: string; lock_version: number; allowed_actions: ('change' | 'authorize')[] }
export type Movement = { id: string; resource_id: string; quantity_delta: number; balance_after: number; movement_type: string; reason: string; operated_by: string; created_at: string }
export type StockCommand = { action: 'create' | 'change' | 'authorize'; id?: string; key: string; body: Record<string, unknown> }
export const movementNames: Record<string, string> = { initial: '初始化', restock: '补充', issue: '领用', return: '退回', adjustment: '盘点调整' }
export function createStockClient(http: HttpClient) {
  const base = (crew: string) => `/crews/${crew}/consumables`
  return {
    stocks: (crew: string, page: number, signal?: AbortSignal) => http.request(`${base(crew)}?page=${page}&page_size=20`, { signal }) as Promise<Page<Stock>>,
    stock: (crew: string, id: string) => http.request(`${base(crew)}/${id}`) as Promise<Stock>,
    movements: (crew: string, id: string, page: number, signal?: AbortSignal) => http.request(`${base(crew)}/${id}/movements?page=${page}&page_size=20`, { signal }) as Promise<Page<Movement>>,
    stockWrite: (crew: string, command: StockCommand) => http.mutate(`${base(crew)}${command.action === 'create' ? '' : `/${command.id}/${command.action === 'change' ? 'movements' : 'authorization'}`}`, command.body, command.action === 'authorize' ? 'PUT' : 'POST', command.action === 'authorize' ? [200] : [201], command.key),
  }
}
