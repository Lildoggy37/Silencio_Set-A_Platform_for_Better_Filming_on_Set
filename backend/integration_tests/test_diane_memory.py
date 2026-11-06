"""Real PostgreSQL checkpoints + MCP; no external model or user trial data."""
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from sqlalchemy import select

from silencio_set.accounts.models import User
from silencio_set.diane.checkpoints import checkpoint_store
from silencio_set.diane.memory_models import ConversationTurn
from silencio_set.diane.memory_schemas import ConversationQuery
from silencio_set.diane.service import DianeService
from silencio_set.diane.tools import BusinessServices
from test_postgres_flow import login, request, seed_actor
from test_workflow_policies import seed


class MemoryModel:
    def __init__(self): self.calls = []
    def bind_tools(self, tools): return self
    async def ainvoke(self, messages, **kwargs):
        self.calls.append(messages.copy())
        index = len(self.calls)
        if index == 1:
            return AIMessage(content='', tool_calls=[{'id':'next','name':'next_shoot','args':{}}],
                usage_metadata={'input_tokens':20,'output_tokens':10,'total_tokens':30})
        if index == 3:
            assert [item.content for item in messages if isinstance(item, HumanMessage)] == ['下一次拍摄是什么？','那先准备什么？']
            assert not any(isinstance(item, ToolMessage) for item in messages)
            assert '详细说明' in messages[0].content and '资源与设备' in messages[0].content
        return AIMessage(content='尚未安排拍摄。' if index == 2 else '建议先整理收音设备。',
            usage_metadata={'input_tokens':30,'output_tokens':10,'total_tokens':40})


def test_private_conversations_checkpoints_idempotency_preferences_and_interruption(postgres_client):
    client, model = postgres_client, MemoryModel()
    state = client.app.state
    emails, member_user = client.portal.call(seed, state.auth.repository.sessions)
    login(client, emails[0])
    crew = request(client,'POST','/crews',{'name':'记忆测试剧组','project_name':'隔离数据'},status=201)
    request(client,'PUT',f"/crews/{crew['id']}/members/{crew['membership']['id']}/roles",
        {'expected_lock_version':crew['membership']['lock_version'],'role_codes':['admin','director','producer']})
    root = f"/crews/{crew['id']}/diane"
    client.portal.call(seed_actor,state.auth.repository.sessions,crew['id'],member_user)
    conversation = request(client,'POST',root+'/conversations',{},status=201)
    path = root+f"/conversations/{conversation['id']}"
    first_id = str(uuid4())
    store = checkpoint_store(state.settings)

    async def install():
        saver = await store.__aenter__()
        state.diane = DianeService(state.settings,BusinessServices(**{name:getattr(state,name)
            for name in BusinessServices.__dataclass_fields__}),model,
            conversations=state.diane_conversations,checkpointer=saver)
        return saver

    saver = client.portal.call(install)
    try:
        first_body = {'question':'下一次拍摄是什么？','turn_id':first_id}
        first = request(client,'POST',path+'/queries',first_body)
        assert first['status'] == 'completed' and first['result']['tools'][0]['status'] == 'success'
        assert first['result']['run_id'] == first_id
        assert request(client,'POST',path+'/queries',first_body) == first
        assert len(model.calls) == 2
        response = client.post('/api/v1'+path+'/queries',json={**first_body,'question':'换了问题'})
        assert response.status_code == 409
        assert request(client,'GET',path)['turns'][0] == first
        request(client,'PUT',root+'/preferences',{'response_style':'detailed','focus':'resources'})
        second = request(client,'POST',path+'/queries',{'question':'那先准备什么？','turn_id':str(uuid4())})
        assert second['status'] == 'completed' and len(model.calls) == 3

        async def inspect_checkpoint():
            checkpoint = await saver.aget_tuple({'configurable':{'thread_id':first_id}})
            values = checkpoint.checkpoint['channel_values']
            assert values['phase'] == 'completed'
            assert len(values['model_calls']) == 2 and len(values['tool_calls']) == 1
            assert values['charged_tokens'] == 70
        client.portal.call(inspect_checkpoint)

        login(client,emails[1])
        assert request(client,'GET',root+'/conversations') == []
        assert client.get('/api/v1'+path).status_code == 404
        assert request(client,'GET',root+'/preferences') == {'response_style':'concise','focus':'general'}
        login(client,emails[0])
        another = request(client,'POST',root+'/conversations',{},status=201)
        assert request(client,'GET',root+'/preferences')['focus'] == 'resources'

        async def leave_stale_run():
            async with state.auth.repository.sessions() as session:
                user = await session.scalar(select(User).where(User.email==emails[0]))
            body = ConversationQuery(question='模拟进程中断',turn_id=uuid4())
            turn, created = await state.diane_conversations.begin(user,UUID(crew['id']),UUID(another['id']),body,60)
            assert created
            replay, created = await state.diane_conversations.begin(user,UUID(crew['id']),UUID(another['id']),body,60)
            assert not created and replay.status == 'running'
            async with state.auth.repository.sessions() as session, session.begin():
                row = await session.get(ConversationTurn,turn.id)
                row.lease_expires_at = datetime.now(UTC)-timedelta(seconds=1)
        client.portal.call(leave_stale_run)
        stale = request(client,'GET',root+f"/conversations/{another['id']}")
        assert stale['turns'][0]['status'] == 'interrupted'
        assert len(model.calls) == 3  # History/replay never re-executes the model.
    finally:
        state.diane = None
        client.portal.call(store.__aexit__,None,None,None)
