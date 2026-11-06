import asyncio
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy.exc import OperationalError

from silencio_set.accounts.errors import AuthError
from silencio_set.plans import publication as module
from silencio_set.plans import dependencies
from silencio_set.plans.domain import check_plan, extend_cross_plan
from silencio_set.plans.models import PlanPreflight, PlanPublishConfirmation, PlanPublicationEvent, PlanReceipt, PlanVersion
from silencio_set.plans.repository import version_view, participant_item
from silencio_set.plans.schemas import PublishInput, PublishView, CheckIssue
from test_plans import fixture as draft_fixture, context, at, ZERO, codes


def fixture(monkeypatch, *, unknown=False):
    repo, session, tx, actor, plan, version, user = draft_fixture(monkeypatch)
    session.execute.return_value = Mock(all=Mock(return_value=[]))
    actor = replace(actor, permissions=actor.permissions | {'plan.publish'})
    monkeypatch.setattr(module, 'resource_actor', AsyncMock(return_value=actor))
    item, inputs = context()
    report = check_plan([item], **inputs)
    unknown_id = uuid4()
    if unknown:
        report.unknown_items = [CheckIssue(code='AVAILABILITY_UNKNOWN', message='此时段未登记档期', session_id=item['id'], member_id=item['members'][0]['member_id'], starts_at=at(1), ends_at=at(2))]
    snapshot = {'items':[item], 'current_published_version_id':None, 'resources':[], 'other_items':[], 'report':report.model_dump(mode='json')}
    record = PlanPreflight(id=uuid4(), crew_id=plan.crew_id, plan_version_id=version.id, checked_by=actor.member_id,
        membership_joined_at=actor.joined_at, checked_lock_version=1, expected_current_published_version_id=None,
        snapshot_digest=module.snapshot_digest(snapshot), unknown_items=[{'item_id':str(unknown_id), **report.unknown_items[0].model_dump(mode='json')}] if unknown else [], expires_at=at(1))
    monkeypatch.setattr(module, 'collect_check', AsyncMock(return_value=(report,snapshot)))
    original=session.scalar.side_effect
    async def scalar(query):
        if query.column_descriptions[0].get('entity') is PlanPreflight: return record
        return await original(query)
    session.scalar.side_effect=scalar
    body=PublishInput(expected_lock_version=1, expected_current_published_version_id=None,
        checked_data_revision='t'*43, confirmed_unknown_item_ids=[unknown_id] if unknown else [])
    return repo, session, tx, actor, plan, version, user, record, report, snapshot, body


def publish(values, body=None):
    repo, _, _, _, plan, version, user, *tail=values
    return asyncio.run(module.publish(repo,user,plan.crew_id,version.id,body or tail[-1],'a'*32,'same-request-key'))


def test_publish_switches_current_version_and_records_confirmation_event_receipt_together(monkeypatch):
    values=fixture(monkeypatch,unknown=True)
    _, session, tx, actor, plan, version, _, record, _, _, _=values
    result=publish(values)
    assert plan.current_published_version_id==version.id and version.status=='published' and version.lock_version==2
    saved=[call.args[0] for call in session.add.call_args_list]
    assert all(any(isinstance(row,kind) for row in saved) for kind in (PlanPublishConfirmation,PlanPublicationEvent,PlanReceipt))
    confirmation=next(row for row in saved if isinstance(row,PlanPublishConfirmation))
    assert confirmation.unknown_availability_snapshot==record.unknown_items
    assert result.notification_status=='pending_dispatch' and result.previous_published_version_id is None
    tx.__aexit__.assert_awaited_once_with(None,None,None)


@pytest.mark.parametrize('change,code',[
    ('expired','PREFLIGHT_STALE'),('membership','PREFLIGHT_STALE'),('reviewer','PREFLIGHT_STALE'),
    ('data','PREFLIGHT_STALE'),('new-empty-set-row','PREFLIGHT_STALE'),('pointer','CURRENT_VERSION_CHANGED'),
    ('unknown','UNKNOWN_CONFIRMATION_REQUIRED'),('blocked','PLAN_BLOCKED'),
])
def test_invalid_evidence_cannot_partially_publish(monkeypatch,change,code):
    values=fixture(monkeypatch,unknown=True)
    _,session,_,_,plan,version,_,record,report,snapshot,body=values
    if change=='expired': record.expires_at=ZERO
    elif change=='membership': record.membership_joined_at-=timedelta(seconds=1)
    elif change=='reviewer': record.checked_by=uuid4()
    elif change=='data': snapshot['resources']=[{'quantity':2}]
    elif change=='new-empty-set-row': snapshot['other_items']=[{'id':str(uuid4())}]
    elif change=='pointer': plan.current_published_version_id=uuid4()
    elif change=='unknown': body=body.model_copy(update={'confirmed_unknown_item_ids':[]})
    else:
        report.blockers=[CheckIssue(code='MEMBER_UNAVAILABLE',message='不可用')]
    before=plan.current_published_version_id
    with pytest.raises(AuthError) as caught: publish(values,body)
    assert caught.value.code==code
    assert version.status=='draft' and plan.current_published_version_id==before
    session.add.assert_not_called()


def test_success_receipt_replays_after_expiry_and_publication_without_reexecuting(monkeypatch):
    values=fixture(monkeypatch)
    _,session,_,_,_,_,_,record,_,_,_=values
    first=publish(values)
    receipt=next(call.args[0] for call in session.add.call_args_list if isinstance(call.args[0],PlanReceipt))
    record.expires_at=ZERO
    async def scalar(query):
        if query.column_descriptions[0].get('entity') is PlanReceipt: return receipt
        raise AssertionError('Committed replay must not recheck expired evidence')
    session.scalar.side_effect=scalar
    session.add.reset_mock()
    assert publish(values)==first
    session.add.assert_not_called()


def test_publish_permission_is_checked_before_receipt_replay(monkeypatch):
    values=fixture(monkeypatch); actor=values[3]
    monkeypatch.setattr(module,'resource_actor',AsyncMock(return_value=replace(actor,permissions=actor.permissions-{'plan.publish'})))
    with pytest.raises(AuthError) as caught: publish(values)
    assert caught.value.code=='FORBIDDEN'
    values[1].scalar.assert_not_awaited()


def test_commit_failure_does_not_return_success(monkeypatch):
    values=fixture(monkeypatch); values[2].__aexit__.side_effect=OperationalError('private SQL',{},Exception('commit failed'))
    with pytest.raises(OperationalError): publish(values)


def test_published_detail_reads_frozen_labels_without_live_resource_queries(monkeypatch):
    values=fixture(monkeypatch); publish(values)
    session, version=values[1],values[5]
    session.scalars.reset_mock()
    result=asyncio.run(version_view(session,version))
    assert result.status=='published' and result.sessions==version.publication_snapshot['items']
    session.scalars.assert_not_awaited()


def test_related_visibility_requires_exact_membership_and_filters_to_own_sessions():
    member=uuid4(); actor=NS(member_id=member,joined_at=ZERO)
    own={'members':[{'member_id':str(member),'membership_joined_at':ZERO.isoformat()}]}
    assert participant_item(own,actor)
    assert not participant_item(own,NS(member_id=member,joined_at=at(1)))
    assert not participant_item(own,NS(member_id=uuid4(),joined_at=ZERO))


def test_persisted_preflight_has_unique_unknown_ids_and_expiry(monkeypatch):
    values=fixture(monkeypatch,unknown=True)
    _,session,_,actor,_,version,_,_,_,_,_=values
    report=asyncio.run(module.create_preflight(session,actor,version,600))
    assert report.can_publish and report.unknown_items[0].item_id and report.expires_at==ZERO+timedelta(seconds=600)
    record=next(call.args[0] for call in session.add.call_args_list if isinstance(call.args[0],PlanPreflight))
    assert record.checked_by==actor.member_id and record.token_digest!=report.checked_data_revision
    assert record.unknown_items[0]['item_id']==str(report.unknown_items[0].item_id)


def test_related_detail_only_returns_current_members_own_published_sessions(monkeypatch):
    from silencio_set.plans import repository
    values=fixture(monkeypatch)
    repo,session,_,actor,plan,version,user,*_=values
    own={'id':str(uuid4()),'members':[{'member_id':str(actor.member_id),'membership_joined_at':actor.joined_at.isoformat()}]}
    other={'id':str(uuid4()),'members':[{'member_id':str(uuid4()),'membership_joined_at':actor.joined_at.isoformat()}]}
    version.status='published'; version.publication_snapshot={'items':[own,other]}; plan.current_published_version_id=version.id
    monkeypatch.setattr(repository,'resource_actor',AsyncMock(return_value=replace(actor,permissions=frozenset({'plan.read_related'}))))
    result=asyncio.run(repo.read(user,plan.crew_id,version_id=version.id))
    assert result.sessions==[own]
    session.scalars.assert_not_awaited()


def test_other_current_plan_people_and_shared_reservations_block_simultaneous_use():
    item,data=context(); report=check_plan([item],**data)
    other=dict(item,id=uuid4())
    data['resources'][item['location_resource_id']].total_quantity=2
    extend_cross_plan(report,[item],[other],data['resources'],data['reservations'],[],ZERO)
    assert {'OTHER_PLAN_MEMBER_CONFLICT','OTHER_PLAN_RESERVATION_CONFLICT'} <= codes(report)


@pytest.mark.parametrize('now,blocker,warning',[(ZERO,False,True),(at(1.5),True,False)])
def test_outstanding_property_is_future_risk_but_current_insufficiency_blocks(now,blocker,warning):
    item,data=context(); report=check_plan([item],**data)
    held=NS(resource_id=item['location_resource_id'],reservation_id=uuid4(),quantity=1,returned_quantity=0)
    extend_cross_plan(report,[item],[],data['resources'],data['reservations'],[held],now)
    assert ('PHYSICAL_CAPACITY_CONFLICT' in codes(report))==blocker
    assert bool(report.warnings)==warning


@pytest.mark.parametrize('check,args,code',[
    (dependencies.check_booking_dependency,(),'RESERVATION_IN_PLAN'),
    (dependencies.check_plan_responsibilities,(),'PLAN_MEMBER_IN_USE'),
    (dependencies.check_resource_unit_dependency,(),'RESOURCE_IN_PLAN'),
])
def test_current_plan_dependencies_block_destructive_relationship_changes(check,args,code):
    session=NS(scalar=AsyncMock(return_value=True))
    with pytest.raises(AuthError) as caught: asyncio.run(check(session,uuid4(),uuid4()))
    assert caught.value.code==code


def test_calendar_change_flags_review_without_rewriting_published_content():
    version=PlanVersion(status='published',lock_version=3,review_reasons=[],publication_snapshot={'items':[]})
    async def selected_rows(query):
        rows = [version] if query.column_descriptions[0].get('entity') is PlanVersion else []
        return Mock(all=Mock(return_value=rows))
    session=NS(scalars=AsyncMock(side_effect=selected_rows))
    asyncio.run(dependencies.mark_plan_review(session,uuid4(),'availability_changed',now=ZERO,member_id=uuid4(),range_start=at(1),range_end=at(2)))
    asyncio.run(dependencies.mark_plan_review(session,uuid4(),'availability_changed',now=ZERO,member_id=uuid4(),range_start=at(1),range_end=at(2)))
    assert version.review_reasons==['availability_changed'] and version.lock_version==3 and version.publication_snapshot=={'items':[]}
