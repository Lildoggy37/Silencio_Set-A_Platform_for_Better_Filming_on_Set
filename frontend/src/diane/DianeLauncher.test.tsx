import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, expect, test, vi } from 'vitest'
import { DianeLauncher } from './DianeLauncher'

const api = vi.hoisted(() => ({ status: vi.fn(), query: vi.fn(), conversations:vi.fn(), history:vi.fn(),
  createConversation:vi.fn(), converse:vi.fn(), preferences:vi.fn(), savePreferences:vi.fn(),enqueue:vi.fn(),watchTurn:vi.fn() }))
vi.mock('../auth/AuthProvider', () => ({ useAuth: () => ({ diane: api, refresh: vi.fn() }) }))
const source = { kind: 'task', record_id: 'task-1', label: '核对收音设备' }
const result = { run_id: 'run-1', status: 'completed', answer: '还有一项任务待完成。', stop_reason: null,
  sources: [source], tools: [{name:'get_task', status:'success', elapsed_ms:20, source}],
  usage: { elapsed_ms:2000, model_ms:1980, tool_ms:20, input_tokens:80, output_tokens:20, total_tokens:100 } }
beforeEach(() => {
  vi.resetAllMocks()
  api.status.mockResolvedValue({ enabled:true, mode:'read_only', timeout_seconds:60 })
  api.query.mockResolvedValue(result)
  api.conversations.mockResolvedValue([])
  api.createConversation.mockResolvedValue({id:'conversation-1',title:'新对话',created_at:'',updated_at:''})
  api.history.mockResolvedValue({conversation:{id:'conversation-1',title:'新对话'},turns:[],has_earlier:false})
  api.converse.mockResolvedValue({id:'turn-1',ordinal:1,question:'还有什么没做完？',status:'completed',result,created_at:''})
  api.preferences.mockResolvedValue({response_style:'concise',focus:'general'})
  api.watchTurn.mockReturnValue(vi.fn())
})
function start() {
  const navigate = vi.fn()
  render(<QueryClientProvider client={new QueryClient({defaultOptions:{queries:{retry:false,gcTime:0}}})}><DianeLauncher crewId="crew-1" userId="user-1" crewName="寂静剧组" onSource={navigate} /></QueryClientProvider>)
  return navigate
}

test('disabled Diane exposes no usable launcher', async () => {
  api.status.mockResolvedValue({ enabled:false, mode:'read_only', timeout_seconds:60 })
  start()
  await waitFor(() => expect(api.status).toHaveBeenCalled())
  expect(screen.queryByRole('button',{name:'Diane'})).toBeNull()
  expect(api.query).not.toHaveBeenCalled()
})

test('a query uses the selected crew and renders real evidence and usage', async () => {
  const navigate = start()
  fireEvent.click(await screen.findByRole('button',{name:'Diane'}))
  await waitFor(()=>expect((screen.getByLabelText('想了解什么？') as HTMLTextAreaElement).disabled).toBe(false))
  fireEvent.change(screen.getByLabelText('想了解什么？'),{target:{value:'还有什么没做完？'}})
  fireEvent.click(screen.getByRole('button',{name:'查询剧组资料'}))
  expect(await screen.findByText('还有一项任务待完成。')).toBeTruthy()
  expect(api.converse).toHaveBeenCalledExactlyOnceWith('crew-1','conversation-1',{question:'还有什么没做完？',turn_id:expect.any(String)},60)
  expect(screen.getByText(/2.0 秒 · 100 tokens/)).toBeTruthy()
  fireEvent.click(screen.getByText('查看依据 ↗'))
  fireEvent.click(screen.getByRole('button',{name:'核对收音设备 ↗'}))
  expect(navigate).toHaveBeenCalledWith(source)
})

test('unknown usage is not displayed as zero and non-completion is visible', async () => {
  api.query.mockResolvedValue({...result,status:'timed_out',answer:'查询超时。',stop_reason:'timeout',
    usage:{...result.usage,input_tokens:null,output_tokens:null,total_tokens:null}})
  api.converse.mockResolvedValue({id:'turn-1',ordinal:1,question:'查询筹备进展',status:'failed',
    result:{...result,status:'timed_out',answer:'查询超时。',usage:{...result.usage,input_tokens:null,output_tokens:null,total_tokens:null}}})
  start(); fireEvent.click(await screen.findByRole('button',{name:'Diane'}))
  await waitFor(()=>expect((screen.getByLabelText('想了解什么？') as HTMLTextAreaElement).disabled).toBe(false))
  fireEvent.change(screen.getByLabelText('想了解什么？'),{target:{value:'查询筹备进展'}})
  fireEvent.click(screen.getByRole('button',{name:'查询剧组资料'}))
  expect(await screen.findByRole('alert')).toHaveProperty('textContent','查询超时。')
  expect(screen.getByText(/token 用量未完整返回/)).toBeTruthy()
})


test('closing folds the non-modal panel and keeps the current question',async()=>{
  start(); const trigger=await screen.findByRole('button',{name:'Diane'})
  fireEvent.click(trigger)
  expect(screen.getByRole('dialog',{name:'Diane聊天'}).getAttribute('aria-modal')).toBe('false')
  fireEvent.change(screen.getByLabelText('想了解什么？'),{target:{value:'保留这段问题'}})
  fireEvent.click(screen.getByRole('button',{name:'收起Diane'}))
  expect(screen.queryByRole('dialog',{name:'Diane聊天'})).toBeNull()
  fireEvent.click(trigger)
  expect((screen.getByLabelText('想了解什么？') as HTMLTextAreaElement).value).toBe('保留这段问题')
  expect(api.query).not.toHaveBeenCalled()
})

test('restores a saved conversation and keeps prior turns when following up',async()=>{
  api.conversations.mockResolvedValue([{id:'saved-1',title:'收音准备'}])
  api.history.mockResolvedValue({conversation:{id:'saved-1',title:'收音准备'},turns:[
    {id:'previous',ordinal:1,question:'昨天的问题',status:'completed',result:{...result,answer:'上次的回答'}}],has_earlier:false})
  api.converse.mockResolvedValue({id:'followup',ordinal:2,question:'接着说',status:'completed',result})
  start();fireEvent.click(await screen.findByRole('button',{name:'Diane'}))
  expect(await screen.findByText('上次的回答')).toBeTruthy()
  fireEvent.change(screen.getByLabelText('想了解什么？'),{target:{value:'接着说'}})
  fireEvent.click(screen.getByRole('button',{name:'查询剧组资料'}))
  expect(await screen.findByText('还有一项任务待完成。')).toBeTruthy()
  expect(screen.getByText('上次的回答')).toBeTruthy()
  expect(api.createConversation).not.toHaveBeenCalled()
  expect(api.converse).toHaveBeenCalledWith('crew-1','saved-1',expect.objectContaining({question:'接着说'}),60)
})

test('background chat subscribes to progress and reconnects after folding without resubmitting',async()=>{
  api.status.mockResolvedValue({enabled:true,mode:'queries_and_task_proposals',execution_mode:'background',timeout_seconds:60})
  const queued={id:'queued-1',ordinal:1,question:'后台查询',status:'queued',phase:'queued',progress_version:1,result:null,created_at:''}
  api.enqueue.mockResolvedValue(queued)
  const closeStream=vi.fn()
  api.watchTurn.mockReturnValue(closeStream)
  start();fireEvent.click(await screen.findByRole('button',{name:'Diane'}))
  await waitFor(()=>expect((screen.getByLabelText('想了解什么？') as HTMLTextAreaElement).disabled).toBe(false))
  fireEvent.change(screen.getByLabelText('想了解什么？'),{target:{value:'后台查询'}})
  fireEvent.click(screen.getByRole('button',{name:'查询剧组资料'}))
  expect(await screen.findByText('Diane · 排队中…')).toBeTruthy()
  await waitFor(()=>expect(api.watchTurn).toHaveBeenCalledTimes(1))
  api.history.mockResolvedValue({conversation:{id:'conversation-1',title:'后台查询'},turns:[queued],has_earlier:false})
  fireEvent.click(screen.getByRole('button',{name:'收起Diane'}))
  expect(closeStream).toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button',{name:'Diane'}))
  await waitFor(()=>expect(api.watchTurn).toHaveBeenCalledTimes(2))
  const onTurn=api.watchTurn.mock.calls[1][3]
  const {act}=await import('@testing-library/react')
  act(()=>onTurn({...queued,status:'completed',phase:'completed',progress_version:5,result}))
  expect(await screen.findByText('还有一项任务待完成。')).toBeTruthy()
  expect(api.enqueue).toHaveBeenCalledTimes(1)
  expect(api.converse).not.toHaveBeenCalled()
})

test('failed runs without a model result show a failure and diagnostic identifier',async()=>{
  api.conversations.mockResolvedValue([{id:'saved-1',title:'后台故障'}])
  api.history.mockResolvedValue({conversation:{id:'saved-1',title:'后台故障'},turns:[
    {id:'failed-run-1',ordinal:1,question:'团队人数',status:'failed',phase:'execution_error',
      progress_version:4,result:null}],has_earlier:false})
  start();fireEvent.click(await screen.findByRole('button',{name:'Diane'}))
  expect(await screen.findByRole('alert')).toHaveProperty('textContent',expect.stringContaining('Diane 执行失败'))
  expect(screen.getByText('查询标识：failed-run-1')).toBeTruthy()
  expect(screen.queryByText('上次执行未完成，可以重新提问。')).toBeNull()
  expect(api.enqueue).not.toHaveBeenCalled()
})

test('crew directory evidence can navigate to the member page',async()=>{
  const members={kind:'members',record_id:null,label:'当前剧组成员'}
  api.converse.mockResolvedValue({id:'turn-1',ordinal:1,question:'剧组人数',status:'completed',
    result:{...result,answer:'当前剧组有7名成员。',sources:[members]}})
  const navigate=start();fireEvent.click(await screen.findByRole('button',{name:'Diane'}))
  await waitFor(()=>expect((screen.getByLabelText('想了解什么？') as HTMLTextAreaElement).disabled).toBe(false))
  fireEvent.change(screen.getByLabelText('想了解什么？'),{target:{value:'剧组人数'}})
  fireEvent.click(screen.getByRole('button',{name:'查询剧组资料'}))
  await screen.findByText('当前剧组有7名成员。')
  fireEvent.click(screen.getByText('查看依据 ↗'))
  fireEvent.click(screen.getByRole('button',{name:'当前剧组成员 ↗'}))
  expect(navigate).toHaveBeenCalledWith(members)
})
