"""Queued HTTP + real PG/MCP + Redis pub/sub; external model is simulated."""
import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

from sqlalchemy import select

from silencio_set.accounts.models import User
from silencio_set.crews.models import CrewMember
from silencio_set.diane.dispatcher import dispatch_once
from silencio_set.diane.events import ProgressEvents
from silencio_set.diane.jobs import AgentJobs
from silencio_set.diane.memory_models import ConversationTurn
from silencio_set.diane.service import DianeService
from silencio_set.diane.tools import BusinessServices
from silencio_set.diane.worker import execute_job
from test_diane_queries import ReadModel
from test_postgres_flow import login, request, seed_actor
from test_workflow_policies import seed


def test_queue_delivery_deduplication_sse_reconnect_and_claim_fencing(postgres_client):
    client, model = postgres_client, ReadModel('next_shoot', {})
    state = client.app.state
    emails, member_user = client.portal.call(seed, state.auth.repository.sessions)
    login(client, emails[0])
    crew = request(client,'POST','/crews',{'name':'后台测试剧组','project_name':'虚构内容'},status=201)
    request(client,'PUT',f"/crews/{crew['id']}/members/{crew['membership']['id']}/roles",
        {'expected_lock_version':crew['membership']['lock_version'],'role_codes':['admin','director','producer']})
    client.portal.call(seed_actor,state.auth.repository.sessions,crew['id'],member_user)
    root = f"/crews/{crew['id']}/diane"
    conversation = request(client,'POST',root+'/conversations',{},status=201)
    path = root+f"/conversations/{conversation['id']}"

    async def install():
        state.diane = DianeService(state.settings,BusinessServices(**{name:getattr(state,name)
            for name in BusinessServices.__dataclass_fields__}),model,conversations=state.diane_conversations)
        state.settings.diane_background_enabled = True
    client.portal.call(install)
    try:
        body = {'question':'下一次拍摄是什么？','turn_id':str(uuid4())}
        queued = request(client,'POST',path+'/runs',body,status=202)
        assert queued['status']=='queued' and queued['phase']=='queued' and not model.calls
        assert request(client,'POST',path+'/runs',body,status=202)==queued
        assert not model.calls

        async def deliver_and_stream():
            jobs = AgentJobs(state.diane_conversations)
            failed = SimpleNamespace(publish=lambda _: (_ for _ in ()).throw(ConnectionError()))
            assert await dispatch_once(jobs,failed)==1
            # A failed/ambiguous delivery remains persisted and may be redelivered.
            async with jobs.sessions() as session, session.begin():
                row = await session.get(ConversationTurn,UUID(body['turn_id']))
                assert row.status=='queued' and row.dispatched_at is None
                row.dispatch_after = datetime.now(UTC)-timedelta(seconds=1)
            sent = []
            assert await dispatch_once(jobs,SimpleNamespace(publish=sent.append))==1
            assert len(sent)==1 and str(sent[0].turn_id)==body['turn_id']
            assert await dispatch_once(jobs,SimpleNamespace(publish=sent.append))==0
            async with jobs.sessions() as session:
                user = await session.scalar(select(User).where(User.email==emails[0]))
            events = ProgressEvents(state.dependencies.redis)
            stream = events.stream(state.diane_conversations,user,UUID(crew['id']),UUID(conversation['id']),UUID(body['turn_id']),AsyncMock())
            first = await anext(stream)
            assert '"status":"queued"' in first
            # Disconnecting the viewer does not claim or cancel the job.
            await stream.aclose()
            stream = events.stream(state.diane_conversations,user,UUID(crew['id']),UUID(conversation['id']),UUID(body['turn_id']),AsyncMock())
            assert '"status":"queued"' in await anext(stream)
            async def duplicate_deliveries():
                await asyncio.gather(*(execute_job(state.diane,jobs,events,UUID(body['turn_id'])) for _ in range(2)))
            worker = asyncio.create_task(duplicate_deliveries())
            snapshots = []
            async for packet in stream:
                if 'event: turn' in packet: snapshots.append(packet)
            await worker
            assert '"status":"completed"' in snapshots[-1]
            assert len(model.calls)==2
            # After completion, reconnect is an immediate durable final snapshot.
            again = events.stream(state.diane_conversations,user,UUID(crew['id']),UUID(conversation['id']),UUID(body['turn_id']),AsyncMock())
            assert '"status":"completed"' in await anext(again)
            await again.aclose()
        client.portal.call(deliver_and_stream)
        done = request(client,'GET',path+'/runs/'+body['turn_id'])
        assert done['status']=='completed' and done['result']['sources'][0]['kind']=='plans'
        replay = request(client,'POST',path+'/runs',body,status=202)
        assert replay==done and len(model.calls)==2
        response = client.get('/api/v1'+path+'/runs/'+body['turn_id']+'/events')
        assert response.status_code==200 and response.headers['content-type'].startswith('text/event-stream')
        assert '"status":"completed"' in response.text
        login(client,emails[1])
        assert client.get('/api/v1'+path+'/runs/'+body['turn_id']+'/events').status_code==404
        login(client,emails[0])

        another = request(client,'POST',root+'/conversations',{},status=201)
        other_path = root+f"/conversations/{another['id']}"
        next_body = {'question':'模拟进程中断','turn_id':str(uuid4())}
        request(client,'POST',other_path+'/runs',next_body,status=202)
        async def expired_claim():
            jobs = AgentJobs(state.diane_conversations)
            execution = await jobs.claim(UUID(next_body['turn_id']),60)
            assert execution is not None
            async with jobs.sessions() as session, session.begin():
                row = await session.get(ConversationTurn,execution.turn.id)
                row.lease_expires_at = datetime.now(UTC)-timedelta(seconds=1)
            view = await state.diane_conversations.get_turn(execution.user,execution.crew_id,execution.conversation_id,execution.turn.id)
            assert view.status=='interrupted'
            assert await jobs.complete(execution) is None
            assert await jobs.claim(execution.turn.id,60) is None
            assert len(model.calls)==2
        client.portal.call(expired_claim)
        last = request(client,'POST',root+'/conversations',{},status=201)
        denied_body = {'question':'旧入组身份不得继续执行','turn_id':str(uuid4())}
        request(client,'POST',root+f"/conversations/{last['id']}/runs",denied_body,status=202)
        async def invalidate_membership():
            jobs = AgentJobs(state.diane_conversations)
            async with jobs.sessions() as session, session.begin():
                member = await session.get(CrewMember,UUID(crew['membership']['id']))
                member.joined_at += timedelta(seconds=1)
            assert await jobs.claim(UUID(denied_body['turn_id']),60) is None
            async with jobs.sessions() as session:
                turn = await session.get(ConversationTurn,UUID(denied_body['turn_id']))
                assert turn.status=='failed' and turn.phase=='access_denied' and turn.result is None
            assert len(model.calls)==2
        client.portal.call(invalidate_membership)
    finally:
        state.diane = None
        state.settings.diane_background_enabled = False
