import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, expect, test, vi } from 'vitest'
import { TaskProposals } from './TaskProposals'
import { AccountError } from '../api/accounts'

const api=vi.hoisted(()=>({proposals:vi.fn(),draftTasks:vi.fn(),decide:vi.fn(),members:vi.fn()}))
vi.mock('../auth/AuthProvider',()=>({useAuth:()=>({diane:api,crews:{members:api.members},refresh:vi.fn()})}))
const proposal={id:'proposal-1',crew_id:'crew',status:'pending',lock_version:1,
  tasks:[{title:'检查话筒电池',description:'准备备用电池',owner_member_id:'member',due_at:'2099-11-10T01:00:00Z',requires_review:false}],
  member_names:{member:'导演'},created_task_ids:[],created_at:'2026-11-05T00:00:00Z',expires_at:'2099-11-10T00:00:00Z',decided_at:null}
beforeEach(()=>{
  vi.resetAllMocks()
  api.members.mockResolvedValue({items:[],total:0})
  api.proposals.mockResolvedValue({items:[proposal]})
  api.draftTasks.mockResolvedValue({proposal,run:{usage:{elapsed_ms:1000,total_tokens:100}}})
  api.decide.mockResolvedValue({...proposal,status:'completed',lock_version:2,created_task_ids:['task-1']})
})
function start(){
  const navigate=vi.fn(), lock=vi.fn()
  render(<QueryClientProvider client={new QueryClient({defaultOptions:{queries:{retry:false,gcTime:0}}})}><TaskProposals crewId="crew" userId="user" selfMemberId="member" timeoutSeconds={60} onTask={navigate} onLockChange={lock} /></QueryClientProvider>)
  return {navigate,lock}
}
async function open(){
  fireEvent.click(screen.getByText('最近的提案'))
  fireEvent.click(await screen.findByRole('button',{name:/检查话筒电池 · 待确认/}))
}
test('drafting does not decide or create until a human confirms the displayed proposal',async()=>{
  const {navigate}=start()
  fireEvent.change(screen.getByLabelText('希望安排什么？'),{target:{value:'准备收音设备'}})
  fireEvent.change(screen.getByLabelText('共同截止时间（北京时间）'),{target:{value:'2099-11-10T09:00'}})
  fireEvent.click(screen.getByRole('button',{name:'拟定任务提案'}))
  await screen.findByRole('heading',{name:'检查话筒电池'})
  expect(api.decide).not.toHaveBeenCalled()
  expect(api.draftTasks).toHaveBeenCalledWith('crew',{question:'准备收音设备',owner_member_id:'member',due_at:'2099-11-10T01:00:00.000Z'},60)
  fireEvent.click(screen.getByRole('button',{name:'确认创建 1 项任务'}))
  await screen.findByText('已创建 1 项任务。')
  expect(api.decide).toHaveBeenCalledWith('crew','proposal-1',{expected_lock_version:1,decision:'accept'})
  fireEvent.click(screen.getByRole('button',{name:'查看已创建任务'}))
  expect(navigate).toHaveBeenCalledWith('task-1')
})
test('an unknown confirmation result freezes the same decision for explicit retry',async()=>{
  api.decide.mockRejectedValueOnce(new AccountError('NETWORK_UNCONFIRMED'))
  const {lock}=start();await open()
  fireEvent.click(screen.getByRole('button',{name:'确认创建 1 项任务'}))
  const retry=await screen.findByRole('button',{name:'用原决定重试'})
  await waitFor(()=>expect(retry.classList.contains('ant-btn-loading')).toBe(false))
  expect((screen.getByRole('button',{name:'拟定另一份'}) as HTMLButtonElement).disabled).toBe(true)
  expect(lock).toHaveBeenLastCalledWith(true)
  fireEvent.click(retry)
  await waitFor(()=>expect(api.decide).toHaveBeenCalledTimes(2))
  expect(api.decide.mock.calls[1]).toEqual(api.decide.mock.calls[0])
})
test('rejecting a proposal never invokes model generation',async()=>{
  api.decide.mockResolvedValue({...proposal,status:'rejected',lock_version:2})
  start();await open()
  fireEvent.click(screen.getByRole('button',{name:'放弃提案'}))
  await screen.findByRole('heading',{name:'任务提案 · 已放弃'})
  expect(api.decide).toHaveBeenCalledWith('crew','proposal-1',{expected_lock_version:1,decision:'reject'})
  expect(api.draftTasks).not.toHaveBeenCalled()
})
