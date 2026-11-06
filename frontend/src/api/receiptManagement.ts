import type { HttpClient } from './http'
import type { Page } from './crews'

export type ReceiptKind = 'plan' | 'call_sheet'
export type PublicationReceipt = { member_id: string; membership_joined_at: string; display_name: string; identity_status: 'active' | 'left' | 'rejoined' | 'disabled'; notification_status: 'pending_dispatch' | 'generated' | 'skipped'; requires_confirmation: boolean; read_at: string | null; confirmed_at: string | null }
export type PublicationReceiptPage = Page<PublicationReceipt> & { source_type: ReceiptKind; source_id: string; version_no: number; source_status: 'current' | 'updated' | 'review_required' | 'superseded' | 'withdrawn'; observed_at: string; dispatch_status: 'pending_dispatch' | 'processed'; counts: { intended: number; generated: number; pending_dispatch: number; skipped: number; read: number; confirmed: number; unconfirmed: number } }
export function createReceiptClient(http: HttpClient) {
  return { receipts: (crew: string, kind: ReceiptKind, version: string, page: number, signal?: AbortSignal) => http.request(`/crews/${crew}/notifications/receipts/${kind}/${version}?page=${page}&page_size=20`, { signal }) as Promise<PublicationReceiptPage> }
}
