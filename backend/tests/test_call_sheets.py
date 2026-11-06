import asyncio
from copy import deepcopy
from dataclasses import replace
from datetime import date, timedelta
from io import StringIO
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from pydantic import ValidationError
from sqlalchemy.exc import OperationalError

from silencio_set.accounts.errors import AuthError
from silencio_set.call_sheets import repository as module, dependencies
from silencio_set.call_sheets.repository import active_people as real_active_people
from silencio_set.call_sheets.domain import day_snapshot, require_complete, content_digest
from silencio_set.call_sheets.models import CallSheet, CallSheetVersion, DraftRecipient, PublishedRecipient, CallSheetReview, CallSheetReceipt, CallSheetEvent
from silencio_set.call_sheets.schemas import CallSheetCreate, CallSheetVersionCreate, CallSheetEdit, CallSheetPublish, RecipientEdit
from silencio_set.core import config as config_module
from silencio_set.core.config import Settings
from silencio_set.creative.schemas import Decision
from silencio_set.crews.models import Crew
from silencio_set.plans.models import ShootingPlan, PlanVersion
from silencio_set.resources.repository import ResourceActor
from silencio_set.tasks.schemas import VersionInput
from test_account_repository import fake_session_factory
from test_auth_api import auth_client
from test_crews import crew_client
from test_plans import ZERO, at


def fixture(monkeypatch):
    factory, session, tx = fake_session_factory()
    crew_id, user, member_id, plan_id, source_id, sheet_id, version_id = [uuid4() for _ in range(7)]
    actor = ResourceActor(member_id, frozenset({'call_sheet.edit', 'call_sheet.review', 'call_sheet.publish', 'plan.read_all', 'member.read'}), frozenset(), ZERO)
    person = NS(id=member_id, user_id=user, status='active', joined_at=ZERO, display_name='林雨')
    item = dict(id=str(uuid4()), scene_label='01 · 教学楼', sequence_no=1, starts_at=at(1).isoformat(), ends_at=at(2).isoformat(),
                members=[dict(member_id=str(member_id), membership_joined_at=ZERO.isoformat(), display_name='林雨')], resources=[])
    plan = ShootingPlan(id=plan_id, crew_id=crew_id, name='首日计划', current_published_version_id=source_id)
    source = PlanVersion(id=source_id, crew_id=crew_id, plan_id=plan_id, status='published', version_no=1, review_reasons=[], publication_snapshot={'items':[item]})
    sheet = CallSheet(id=sheet_id, crew_id=crew_id, plan_id=plan_id, shooting_date=date(2026,11,2), current_published_version_id=None)
    version = CallSheetVersion(id=version_id, crew_id=crew_id, call_sheet_id=sheet_id, plan_id=plan_id, source_plan_version_id=source_id,
        version_no=1, lock_version=1, status='draft', validity_status=None,
        arrangement_snapshot=day_snapshot(source, sheet.shooting_date, project_name='雨停之前', plan_name=plan.name),
        gathering_at=ZERO, gathering_location='教学楼入口', contact_note=None, notes=None,
        reviewed_by=None, reviewed_at=None, approved_content_digest=None, published_at=None)
    state = NS(receipt=None, rows=[DraftRecipient(crew_id=crew_id, call_sheet_version_id=version_id, member_id=member_id,
        membership_joined_at=ZERO, display_name='林雨')], people={member_id:person})
    async def scalar(query):
        entity = query.column_descriptions[0].get('entity')
        if entity is CallSheetReceipt: return state.receipt
        if entity is CallSheetVersion: return version
        if entity is CallSheet: return sheet
        if entity is PlanVersion: return source
        if entity is ShootingPlan: return plan
        if entity is Crew: return NS(project_name='雨停之前')
        return ZERO
    session.scalar.side_effect = scalar
    monkeypatch.setattr(module, 'resource_actor', AsyncMock(return_value=actor))
    monkeypatch.setattr(module, 'recipient_rows', AsyncMock(side_effect=lambda *_: state.rows))
    monkeypatch.setattr(module, 'active_people', AsyncMock(side_effect=lambda _, crew, ids: {key:state.people[key] for key in ids}))
    def add_all(rows):
        if rows and isinstance(rows[0], (PublishedRecipient, DraftRecipient)): state.rows = rows
    session.add_all.side_effect = add_all
    repo = module.CallSheetRepository(factory)
    def run(action, body, key='same-call-sheet-request'):
        return asyncio.run(repo.mutate(user, crew_id, action, body, 'a'*32, idempotency_key=key, target_id=version.id))
    return NS(repo=repo, session=session, tx=tx, actor=actor, person=person, plan=plan, source=source, sheet=sheet,
              version=version, state=state, run=run, user=user, crew_id=crew_id)


def approve(value):
    value.run('submit', VersionInput(expected_lock_version=value.version.lock_version))
    return value.run('review', Decision(expected_lock_version=value.version.lock_version, decision='accept'))


def publish(value):
    return value.run('publish', CallSheetPublish(expected_lock_version=value.version.lock_version,
        expected_current_published_version_id=value.sheet.current_published_version_id))


def test_beijing_day_grouping_keeps_overnight_session_on_start_day(monkeypatch):
    v = fixture(monkeypatch)
    original = v.source.publication_snapshot['items'][0]
    overnight = dict(original, starts_at=at(15).isoformat(), ends_at=at(17).isoformat())
    midnight_end = dict(original, starts_at=at(14).isoformat(), ends_at=at(16).isoformat())
    tomorrow = dict(original, starts_at=at(25).isoformat(), ends_at=at(26).isoformat())
    v.source.publication_snapshot = {'items':[overnight, midnight_end, tomorrow]}
    first = day_snapshot(v.source, date(2026,11,2), project_name='片名', plan_name='计划')
    second = day_snapshot(v.source, date(2026,11,3), project_name='片名', plan_name='计划')
    assert first['sessions'] == [overnight, midnight_end] and second['sessions'] == [tomorrow]
    assert second['overnight_continuations'] == [dict(scene_label=overnight['scene_label'], starts_at=overnight['starts_at'], ends_at=overnight['ends_at'])]
    with pytest.raises(AuthError) as caught: day_snapshot(v.source, date(2026,11,4), project_name='片名', plan_name='计划')
    assert caught.value.code == 'CALL_SHEET_NO_SESSIONS'


@pytest.mark.parametrize('fields', [dict(shooting_date=0), dict(shooting_date='2026-11-02T00:00:00'), dict(shooting_date='2026-02-30')])
def test_calendar_date_is_explicit(fields):
    with pytest.raises(ValidationError): CallSheetCreate(source_plan_version_id=uuid4(), **fields)


@pytest.mark.parametrize('fields', [dict(gathering_at='2026-11-02T08:00:00'), dict(gathering_at='2026-11-02T00:00:00.1Z'), dict(notes='字'*2001), {}])
def test_invalid_edit_is_rejected(fields):
    with pytest.raises(ValidationError): CallSheetEdit(expected_lock_version=1, **fields)


def test_collection_can_be_previous_day_but_not_after_first_session(monkeypatch):
    v = fixture(monkeypatch)
    v.version.gathering_at = ZERO-timedelta(hours=1)
    require_complete(v.version)
    v.version.gathering_at = at(1)+timedelta(seconds=1)
    with pytest.raises(AuthError) as caught: require_complete(v.version)
    assert caught.value.code == 'GATHERING_AFTER_SHOOT'


@pytest.mark.parametrize('action', ['create', 'version'])
def test_generate_freezes_source_and_defaults_to_required_recipients(monkeypatch, action):
    v=fixture(monkeypatch)
    original=v.session.scalar.side_effect
    async def scalar(query):
        column=query.column_descriptions[0]
        if column['name']=='coalesce': return 0
        if action=='create' and column.get('entity') is CallSheet: return None
        return await original(query)
    v.session.scalar.side_effect=scalar
    body=CallSheetCreate(source_plan_version_id=v.source.id,shooting_date=v.sheet.shooting_date) if action=='create' else CallSheetVersionCreate(source_plan_version_id=v.source.id,copy_from=v.version.id)
    target=None if action=='create' else v.sheet.id
    result=asyncio.run(v.repo.mutate(v.user,v.crew_id,action,body,'a'*32,idempotency_key='generate',target_id=target))
    assert result.status=='draft' and result.source_current and result.arrangement_snapshot['sessions']==v.source.publication_snapshot['items']
    assert len(result.recipients)==1 and result.recipients[0].required
    assert result.gathering_location==(None if action=='create' else '教学楼入口')


@pytest.mark.parametrize('member_status,enabled', [('left',True),('active',False)])
def test_inactive_recipient_or_account_cannot_be_selected(monkeypatch,member_status,enabled):
    v=fixture(monkeypatch); v.person.status=member_status
    monkeypatch.setattr(module,'rows_by_ids',AsyncMock(return_value={v.person.id:v.person}))
    v.session.scalars.return_value=Mock(all=Mock(return_value=[v.user] if enabled else []))
    with pytest.raises(AuthError) as caught: asyncio.run(real_active_people(v.session,v.crew_id,[v.person.id]))
    assert caught.value.code=='RECIPIENT_INACTIVE'


def test_review_and_publication_freeze_content_recipients_and_event_in_same_transaction(monkeypatch):
    v = fixture(monkeypatch)
    reviewed = approve(v)
    review = next(call.args[0] for call in v.session.add.call_args_list if isinstance(call.args[0], CallSheetReview))
    assert review.id == reviewed.review_id and review.content_snapshot['gathering_location'] == '教学楼入口'
    assert review.content_snapshot['recipients'][0]['membership_joined_at'] == ZERO.isoformat().replace('+00:00','Z')
    result = publish(v)
    assert result.status == 'published' and result.validity_status == 'valid' and result.lock_version == 4
    assert v.sheet.current_published_version_id == v.version.id and result.notification_status == 'pending_dispatch'
    assert isinstance(v.state.rows[0], PublishedRecipient) and v.state.rows[0].required_at_publish
    event = next(call.args[0] for call in v.session.add.call_args_list if isinstance(call.args[0], CallSheetEvent))
    assert event.kind == 'published' and event.consumed_at is None
    assert v.tx.__aexit__.await_count == 3


def test_edit_after_approval_resets_only_current_approval_and_preserves_review_snapshot(monkeypatch):
    v = fixture(monkeypatch); approve(v)
    review = next(call.args[0] for call in v.session.add.call_args_list if isinstance(call.args[0], CallSheetReview))
    result = v.run('edit', CallSheetEdit(expected_lock_version=3, gathering_location='新入口'))
    assert result.status == 'draft' and v.version.approved_content_digest is None and v.version.reviewed_at is None
    assert review.content_snapshot['gathering_location'] == '教学楼入口'


@pytest.mark.parametrize('change,code', [('source','SOURCE_PLAN_STALE'), ('pointer','CURRENT_VERSION_CHANGED'), ('content','REVIEW_REQUIRED'), ('rejoin','RECIPIENT_SET_INVALID')])
def test_publish_rechecks_source_pointer_content_and_membership(monkeypatch, change, code):
    v = fixture(monkeypatch); approve(v)
    body = CallSheetPublish(expected_lock_version=3, expected_current_published_version_id=None)
    if change == 'source': v.source.review_reasons = ['availability_changed']
    elif change == 'pointer': v.sheet.current_published_version_id = uuid4()
    elif change == 'content': v.version.notes = '未经审核的变动'
    else: v.person.joined_at = at(1)
    v.session.add.reset_mock()
    with pytest.raises(AuthError) as caught: v.run('publish', body)
    assert caught.value.code == code and v.version.status == 'approved'
    v.session.add.assert_not_called()


@pytest.mark.parametrize('status', ['pending_review', 'published'])
def test_submitted_and_published_versions_cannot_be_edited(monkeypatch, status):
    v = fixture(monkeypatch); v.version.status = status
    with pytest.raises(AuthError) as caught: v.run('edit', CallSheetEdit(expected_lock_version=1, notes='修改'))
    assert caught.value.code == 'INVALID_STATE'


def test_required_recipient_cannot_be_removed_and_extra_recipient_enters_review_digest(monkeypatch):
    v = fixture(monkeypatch)
    with pytest.raises(AuthError) as caught: v.run('recipients', RecipientEdit(expected_lock_version=1, member_ids=[]))
    assert caught.value.code == 'REQUIRED_RECIPIENT_MISSING'
    extra = NS(id=uuid4(), joined_at=ZERO, display_name='制片')
    v.state.people[extra.id] = extra
    before = content_digest(v.version, v.state.rows)
    result = v.run('recipients', RecipientEdit(expected_lock_version=1, member_ids=[v.person.id, extra.id]))
    assert len(result.recipients) == 2 and content_digest(v.version, v.state.rows) != before
    assert {row.member_id for row in result.recipients if row.required} == {v.person.id}


def test_stale_source_can_be_rejected_with_snapshot_but_not_accepted(monkeypatch):
    v = fixture(monkeypatch); v.version.status = 'pending_review'; v.source.review_reasons = ['script_changed']
    with pytest.raises(AuthError) as caught: v.run('review', Decision(expected_lock_version=1, decision='accept'))
    assert caught.value.code == 'SOURCE_PLAN_STALE'
    result = v.run('review', Decision(expected_lock_version=1, decision='reject', review_note='更新来源排期'))
    assert result.status == 'draft' and result.review_id is not None


def test_receipt_replays_exact_review_result_after_state_change(monkeypatch):
    v = fixture(monkeypatch)
    v.version.status = 'pending_review'
    body = Decision(expected_lock_version=1, decision='accept')
    first = v.run('review', body)
    v.state.receipt = next(call.args[0] for call in v.session.add.call_args_list if isinstance(call.args[0], CallSheetReceipt))
    v.source.review_reasons = ['script_changed']; v.session.add.reset_mock()
    assert v.run('review', body) == first
    v.session.add.assert_not_called()
    with pytest.raises(AuthError) as caught: v.run('review', body.model_copy(update={'review_note':'不同请求'}))
    assert caught.value.code == 'IDEMPOTENCY_KEY_REUSED'
    v.state.receipt.membership_joined_at = at(1)
    with pytest.raises(AuthError) as caught: v.run('review', body)
    assert caught.value.code == 'MEMBERSHIP_CHANGED'


def test_current_permission_checked_before_receipt_lookup(monkeypatch):
    v = fixture(monkeypatch)
    monkeypatch.setattr(module, 'resource_actor', AsyncMock(return_value=replace(v.actor, permissions=frozenset({'call_sheet.edit'}))))
    with pytest.raises(AuthError) as caught: publish(v)
    assert caught.value.code == 'FORBIDDEN'
    v.session.scalar.assert_not_awaited()


def test_failed_commit_does_not_return_success(monkeypatch):
    v = fixture(monkeypatch)
    v.tx.__aexit__.side_effect = OperationalError('private SQL', {}, Exception('lost commit'))
    with pytest.raises(OperationalError): v.run('edit', CallSheetEdit(expected_lock_version=1, notes='雨具'))


def test_new_plan_marks_remaining_day_for_review_and_removes_absent_day_without_rewriting(monkeypatch):
    v = fixture(monkeypatch)
    other_sheet = NS(shooting_date=date(2026,11,3))
    other = NS(id=uuid4(), validity_status='valid', lock_version=2)
    v.version.status='published'; v.version.validity_status='valid'
    frozen = deepcopy(v.version.arrangement_snapshot)
    v.session.execute.return_value = Mock(all=Mock(return_value=[(v.sheet,v.version), (other_sheet,other)]))
    sender = AsyncMock(); monkeypatch.setattr(dependencies, 'event', sender)
    affected = asyncio.run(dependencies.invalidate_for_plan(v.session, v.crew_id, v.plan.id, v.source, ZERO))
    assert set(affected) == {v.version.id, other.id}
    assert v.version.validity_status == 'review_required' and other.validity_status == 'withdrawn'
    assert v.version.arrangement_snapshot == frozen and sender.await_count == 2
    asyncio.run(dependencies.invalidate_for_plan(v.session,v.crew_id,v.plan.id,v.source,ZERO))
    assert sender.await_count == 2


def test_source_review_marks_current_notice_for_review(monkeypatch):
    v = fixture(monkeypatch); v.version.status='published'; v.version.validity_status='valid'
    v.session.scalars.return_value = Mock(all=Mock(return_value=[v.version]))
    sender = AsyncMock(); monkeypatch.setattr(dependencies,'event',sender)
    asyncio.run(dependencies.mark_source_review(v.session,v.crew_id,v.source.id,ZERO))
    assert v.version.validity_status == 'review_required' and v.version.lock_version == 2
    assert sender.await_args.args[2] == 'review_required'


@pytest.mark.parametrize('published,visible,allowed', [(False,True,False), (True,False,False), (True,True,True)])
def test_related_reader_sees_only_published_notices_received_in_current_membership(monkeypatch,published,visible,allowed):
    v=fixture(monkeypatch)
    if published: v.version.status='published'; v.state.rows[0].required_at_publish=True
    monkeypatch.setattr(module,'resource_actor',AsyncMock(return_value=replace(v.actor,permissions=frozenset({'call_sheet.read_related'}))))
    original=v.session.scalar.side_effect
    async def scalar(query):
        if query.column_descriptions[0].get('entity') is None: return visible
        return await original(query)
    v.session.scalar.side_effect=scalar
    operation=v.repo.read(v.user,v.crew_id,version_id=v.version.id)
    if allowed: assert asyncio.run(operation).id == v.version.id
    else:
        with pytest.raises(AuthError) as caught: asyncio.run(operation)
        assert caught.value.code == 'NOT_FOUND'
    query = str(module.received_versions(v.crew_id,v.actor))
    assert 'membership_joined_at' in query and 'member_id' in query


def test_call_sheet_routes_require_csrf_and_complete_publish_contract(crew_client):
    client, _, _, crew = crew_client
    client.app.state.call_sheets=Mock()
    path=f'/api/v1/crews/{crew.id}/call-sheets'
    response=client.post(path,json={'source_plan_version_id':str(uuid4()),'shooting_date':'2026-11-02'},headers={'Idempotency-Key':str(uuid4()),'X-CSRF-Token':'bad'})
    assert response.status_code == 403
    assert client.post(f'/api/v1/crews/{crew.id}/call-sheet-versions/{uuid4()}/publish',json={}).status_code == 422


def test_offline_migration_contains_same_plan_pointer_and_query_indexes(monkeypatch):
    monkeypatch.setattr(config_module,'load_settings',lambda:Settings(_env_file=None,database_url='postgresql+asyncpg://user:example@localhost:5432/test'))
    output=StringIO(); command.upgrade(Config('alembic.ini',output_buffer=output),'head',sql=True)
    sql=output.getvalue()
    assert '0017_call_sheets' in sql and 'CREATE TABLE call_sheet_published_recipients' in sql
    assert 'FOREIGN KEY(crew_id, id, current_published_version_id) REFERENCES call_sheet_versions (crew_id, call_sheet_id, id)' in sql
    assert 'FOREIGN KEY(crew_id, plan_id, source_plan_version_id) REFERENCES shooting_plan_versions (crew_id, plan_id, id)' in sql
    assert 'CREATE INDEX ix_call_sheets_crew_date ON call_sheets (crew_id, shooting_date, id)' in sql
    assert 'CREATE INDEX ix_call_sheet_versions_source ON call_sheet_versions (crew_id, source_plan_version_id, id)' in sql
