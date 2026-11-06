import type { HttpClient } from './http'
import type { Page } from './crews'

export type Checkout = {
  id: string; crew_id: string; resource_id: string; reservation_id: string; borrower_member_id: string
  quantity: number; returned_quantity: number; outstanding_quantity: number; checked_out_at: string
  expected_return_at: string; handled_by: string; lock_version: number; status: 'outstanding' | 'overdue' | 'returned'
}
export type CustodyAuthorization = { resource_id: string; member_id: string; resource_lock_version: number; member_lock_version: number; permission_codes: string[] }
export type CustodyCommand =
  | { action: 'checkout'; id: string; key: string; body: { quantity: number; expected_lock_version: number; expected_return_at: string } }
  | { action: 'return'; id: string; key: string; body: { quantity: number; expected_lock_version: number; note: string | null } }
  | { action: 'authorize'; id: string; key: string; body: { member_id: string; expected_lock_version: number; expected_member_lock_version: number; permission_codes: string[]; grant_capabilities: boolean } }

export function createCustodyClient(http: HttpClient) {
  return {
    listCheckouts(crewId: string, resourceId: string, page: number, signal?: AbortSignal) {
      return http.request(`/crews/${crewId}/resources/${resourceId}/checkouts?page=${page}&page_size=20`, { signal }) as Promise<Page<Checkout>>
    },
    getCheckout(crewId: string, id: string) {
      return http.request(`/crews/${crewId}/resource-checkouts/${id}`) as Promise<Checkout>
    },
    custody(crewId: string, command: CustodyCommand) {
      const path = command.action === 'checkout' ? `/resource-reservations/${command.id}/checkouts`
        : command.action === 'return' ? `/resource-checkouts/${command.id}/returns` : `/resources/${command.id}/custody-authorization`
      return http.mutate(`/crews/${crewId}${path}`, command.body, command.action === 'authorize' ? 'PUT' : 'POST', command.action === 'checkout' ? [201] : [200], command.key) as Promise<Checkout | CustodyAuthorization>
    },
  }
}
