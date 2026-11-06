import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, expect, test, vi } from 'vitest'
import { ChangeWorkbench } from './ChangeWorkbench'
import { AccountError } from '../api/accounts'

const api=vi.hoisted(() => ({ list: vi.fn(),check:vi.fn(),records:vi.fn(),history:vi.fn() }))
const sheets=vi.hoisted(() => ({ get: vi.fn() }))
vi.mock('../auth/AuthProvider', () => ({ useAuth: () => ({ changes: api, callSheets: sheets, refresh: vi.fn() }) }))
vi.mock('../scripts/CreativePanel', () => ({ CreativePanel: (props: { kind: string; sourceId: string; initialId: string }) => <p>{props.kind} · {props.sourceId} · {props.initialId}</p> }))
vi.mock('../plans/CallSheetBoard', () => ({ CallSheetPreview: () => <p>通告只读正文</p> }))
const plan={ id:'version', category:'plans', parent_id:'plan', source_id:null, container_id:null, current_plan_version_id:null, title:'首日安排 · 第2版', status:'review_required', reason_codes:['script_changed'] }
beforeEach(() => {
  vi.resetAllMocks()
  api.list.mockImplementation((_crew,category,page) => Promise.resolve({ category,allowed_categories:['plans','call_sheets'],observed_at:'2026-11-03T00:00:00Z',items:[{ ...plan,category }],total:1,page,page_size:20 }))
  sheets.get.mockResolvedValue({ id:'sheet-version' })
})
function start(permissions=['plan.edit','call_sheet.edit']) {
  const open=vi.fn()
  render(<QueryClientProvider client={new QueryClient({ defaultOptions:{queries:{retry:false,gcTime:0}} })}><ChangeWorkbench crewId="crew" userId="user" permissions={permissions} onOpenPlan={open} onOpenScript={vi.fn()} /></QueryClientProvider>)
  return open
}
async function open() { fireEvent.click(screen.getByRole('button',{name:'查看待确认事项'})); await screen.findByText('首日安排 · 第2版') }
test('opening a producer workbench only fetches the selected permitted category',async () => {
  start(); expect(api.list).not.toHaveBeenCalled(); await open()
  expect(api.list).toHaveBeenCalledWith('crew','plans',1,expect.any(AbortSignal))
  expect(screen.queryByRole('button',{name:'场戏来源'})).toBeNull()
  expect(screen.getByRole('button',{name:'每日通告'})).toBeTruthy()
})
test('related-only readers and organization administrators do not gain a full crew issue list',() => {
  start(['member.authorize','plan.read_related','call_sheet.read_related'])
  expect(screen.queryByRole('button',{name:'查看待确认事项'})).toBeNull()
  expect(api.list).not.toHaveBeenCalled()
})
test('a plan issue opens its exact version regardless of plan list pagination',async () => {
  const target=start(); await open()
  fireEvent.click(screen.getByRole('button',{name:'前往排期处理'}))
  expect(target).toHaveBeenCalledWith({planId:'plan',versionId:'version'})
})
test('call sheet repair navigates to the current plan with the affected sheet version selected',async () => {
  api.list.mockResolvedValue({category:'call_sheets',observed_at:'2026-11-03T00:00:00Z',items:[{...plan,id:'sheet-version',category:'call_sheets',source_id:'old-plan-version',current_plan_version_id:'new-plan-version',container_id:'sheet',title:'待复核通告'}],total:1,page:1,page_size:20})
  const target=start(); fireEvent.click(screen.getByRole('button',{name:'查看待确认事项'}))
  fireEvent.click(screen.getByRole('button',{name:'每日通告'}))
  fireEvent.click(await screen.findByRole('button',{name:'前往排期处理'}))
  expect(target).toHaveBeenCalledWith({planId:'plan',versionId:'new-plan-version',sheetId:'sheet',sheetVersionId:'sheet-version'})
})
test('a creative issue opens the fixed historical artifact for source review',async () => {
  api.list.mockResolvedValue({category:'breakdowns',observed_at:'2026-11-03T00:00:00Z',items:[{...plan,category:'breakdowns',id:'breakdown',source_id:'old-script-version'}],total:1,page:1,page_size:20})
  const target=start(['script.read','scene.read']); await open()
  fireEvent.click(screen.getByRole('button',{name:'查看并处理'}))
  await screen.findByText('breakdowns · old-script-version · breakdown')
  expect(target).not.toHaveBeenCalled()
})
test('call sheet-only reviewers see the actual sheet without manufacturing a readable plan',async () => {
  api.list.mockResolvedValue({category:'call_sheets',observed_at:'2026-11-03T00:00:00Z',items:[{...plan,id:'sheet-version',category:'call_sheets',source_id:'old-plan-version',container_id:'sheet'}],total:1,page:1,page_size:20})
  const target=start(['call_sheet.review']); await open()
  fireEvent.click(screen.getByRole('button',{name:'查看并处理'}))
  await screen.findByText('通告只读正文')
  expect(sheets.get).toHaveBeenCalledWith('crew','sheet-version')
  expect(target).not.toHaveBeenCalled()
})
test('a failed refresh removes cached private issue titles',async () => {
  start(); await open()
  api.list.mockRejectedValue(new AccountError('FORBIDDEN',403))
  fireEvent.click(screen.getByRole('button',{name:'刷新变更清单'}))
  await waitFor(() => expect(screen.queryByText('首日安排 · 第2版')).toBeNull())
})

test('a needs-revision artifact opens its script working draft instead of copying the old source',async () => {
  api.list.mockResolvedValue({category:'breakdowns',observed_at:'2026-11-03T00:00:00Z',items:[{...plan,category:'breakdowns',parent_id:'script',status:'needs_revision'}],total:1,page:1,page_size:20})
  const script=vi.fn()
  render(<QueryClientProvider client={new QueryClient({defaultOptions:{queries:{retry:false,gcTime:0}}})}><ChangeWorkbench crewId="crew" userId="user" permissions={['script.read','scene.read','scene.edit']} onOpenPlan={vi.fn()} onOpenScript={script} /></QueryClientProvider>)
  fireEvent.click(screen.getByRole('button',{name:'查看待确认事项'}))
  fireEvent.click(await screen.findByRole('button',{name:'到工作稿修订'}))
  expect(script).toHaveBeenCalledWith('script')
})

test('an unresolved check prevents switching its category or closing its workbench',async () => {
  api.check.mockRejectedValue(new AccountError('NETWORK_UNCONFIRMED'))
  start(); await open()
  fireEvent.click(screen.getByRole('button',{name:'检查并记录当前类别'}))
  await screen.findByRole('button',{name:'用原检查请求重试'})
  expect((screen.getByRole('button',{name:'每日通告'}) as HTMLButtonElement).disabled).toBe(true)
  expect(screen.queryByRole('button',{name:'Close'})).toBeNull()
})
