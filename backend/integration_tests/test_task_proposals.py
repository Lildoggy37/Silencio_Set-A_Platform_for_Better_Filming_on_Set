"""Atomic human-confirmed tasks on real PostgreSQL; no external model calls."""
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import UUID

from langchain_core.messages import AIMessage
from sqlalchemy import select, update

from silencio_set.accounts.errors import AuthError
from silencio_set.crews.models import CrewMember
from silencio_set.diane.proposal_models import TaskProposal
from silencio_set.diane.service import DianeService
from silencio_set.diane.tools import BusinessServices
from test_postgres_flow import login, request, seed_actor, saved_session, restore_session
from test_workflow_policies import seed


class DraftModel:
    def __init__(self): self.count = 0
    def bind_tools(self, tools): return self
    async def ainvoke(self, messages, **kwargs):
        self.count += 1
        return AIMessage(content='请审阅任务提案，尚未创建任务。' if self.count > 1 else '',
            tool_calls=[] if self.count > 1 else [{'id':'draft','name':'draft_tasks','args':{'tasks':[
                {'title':'核对话筒电池','description':'整理备用电池'}, {'title':'检查收音线材','description':'核对接头'}]}}],
            usage_metadata={'input_tokens':20,'output_tokens':10,'total_tokens':30})


def test_task_proposal_confirmation_is_private_atomic_and_replayable(postgres_client, monkeypatch):
    client=postgres_client; state=client.app.state
    emails, other_user=client.portal.call(seed,state.auth.repository.sessions)
    login(client,emails[0])
    crew=request(client,'POST','/crews',{'name':'提案联调','project_name':'准备开拍'},status=201)
    root=f"/crews/{crew['id']}"; own=crew['membership']
    request(client,'PUT',root+f"/members/{own['id']}/roles",{'expected_lock_version':own['lock_version'],'role_codes':['admin','producer','director']})
    other_id=client.portal.call(seed_actor,state.auth.repository.sessions,crew['id'],other_user)
    # Give another member full task authority: that still does not expose private proposals.
    async def promote():
        from silencio_set.crews.models import MemberRole
        async with state.auth.repository.sessions() as session,session.begin():
            session.add(MemberRole(crew_id=UUID(crew['id']),member_id=UUID(str(other_id)),role_code='producer'))
    client.portal.call(promote)
    async def install():
        state.diane=DianeService(state.settings,BusinessServices(**{name:getattr(state,name) for name in BusinessServices.__dataclass_fields__}),DraftModel())
    def draft():
        client.portal.call(install)
        return request(client,'POST',root+'/diane/task-proposals/draft',{
            'question':'把收音准备拆成两项任务','owner_member_id':own['id'],
            'due_at':(datetime.now(UTC)+timedelta(days=2)).isoformat()})['proposal']
    def decide(proposal,decision='accept',version=None):
        return client.post('/api/v1'+root+f"/diane/task-proposals/{proposal['id']}/decisions",json={
            'expected_lock_version':proposal['lock_version'] if version is None else version,'decision':decision})
    def count(): return request(client,'GET',root+'/tasks')['total']
    try:
        proposal=draft(); assert proposal['status']=='pending' and count()==0
        login(client,emails[1])
        assert request(client,'GET',root+'/diane/task-proposals')['items']==[]
        assert decide(proposal).status_code==404
        login(client,emails[0])
        assert decide(proposal,version=99).status_code==409 and count()==0
        token=client.headers.pop('X-CSRF-Token')
        assert decide(proposal).status_code==403
        client.headers['X-CSRF-Token']=token
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses=list(pool.map(lambda _:decide(proposal),range(2)))
        assert all(response.status_code==200 for response in responses)
        done=responses[0].json()['data']
        assert done['status']=='completed' and len(done['created_task_ids'])==2 and count()==2
        assert responses[1].json()['data']['created_task_ids']==done['created_task_ids']
        assert decide(proposal,'reject').status_code==409

        rejected=draft()
        assert decide(rejected,'reject').json()['data']['status']=='rejected'
        assert decide(rejected,'reject').status_code==200 and count()==2

        rollback=draft()
        original=state.tasks.create_in_transaction
        calls=0
        async def fail_second(*args,**kwargs):
            nonlocal calls
            calls+=1
            if calls==2: raise AuthError(409,'MEMBER_INACTIVE','测试第二项执行前校验失败')
            return await original(*args,**kwargs)
        monkeypatch.setattr(state.tasks,'create_in_transaction',fail_second)
        assert decide(rollback).status_code==409 and count()==2
        pending=request(client,'GET',root+'/diane/task-proposals')['items']
        assert next(item for item in pending if item['id']==rollback['id'])['status']=='pending'
        monkeypatch.setattr(state.tasks,'create_in_transaction',original)
        assert decide(rollback).json()['data']['status']=='completed' and count()==4

        stale=draft()
        async def change_member():
            async with state.auth.repository.sessions() as session,session.begin():
                await session.execute(update(CrewMember).where(CrewMember.id==UUID(own['id'])).values(lock_version=CrewMember.lock_version+1))
        client.portal.call(change_member)
        assert decide(stale).json()['error']['code']=='PROPOSAL_STALE' and count()==4

        expired=draft()
        async def expire():
            async with state.auth.repository.sessions() as session,session.begin():
                await session.execute(update(TaskProposal).where(TaskProposal.id==UUID(expired['id'])).values(expires_at=datetime.now(UTC)-timedelta(seconds=1)))
        client.portal.call(expire)
        assert decide(expired).json()['error']['code']=='PROPOSAL_EXPIRED' and count()==4
    finally:
        state.diane=None
