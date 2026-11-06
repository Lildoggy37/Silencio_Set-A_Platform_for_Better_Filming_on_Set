import asyncio
from datetime import timedelta
from io import StringIO
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import OperationalError

from silencio_set.accounts.errors import AuthError
from silencio_set.call_sheets.models import CallSheetEvent, PublishedRecipient
from silencio_set.core import config as config_module
from silencio_set.core.config import Settings
from silencio_set.crews.models import Crew, CrewMember
from silencio_set.notifications import publications, repository as module
from silencio_set.notifications.models import Notification, NotificationRecipient
from silencio_set.notifications.queries import notification_query
from silencio_set.plans.models import PlanPublicationEvent, PlanVersion
from silencio_set.tasks.repository import TaskActor
from test_account_repository import fake_session_factory
from test_notifications import mark_fixture
from test_plans import ZERO, at


def fixture(monkeypatch, kind='plan', event_kind='published'):
    factory, session, tx = fake_session_factory()
    crew_id, version_id = uuid4(), uuid4()
    members = [CrewMember(id=uuid4(), crew_id=crew_id, joined_at=ZERO-timedelta(days=1), status='active') for _ in range(2)]
    candidates = [dict(member_id=str(member.id),membership_joined_at=member.joined_at.isoformat()) for member in members]
    if kind=='plan':
        event=PlanPublicationEvent(id=uuid4(),crew_id=crew_id,plan_version_id=version_id,previous_version_id=uuid4(),recipient_snapshots=candidates,created_at=ZERO,consumed_at=None)
        version=PlanVersion(id=version_id,crew_id=crew_id,publication_snapshot={'items':[{'members':[candidates[0]]}]})
    else:
        event=CallSheetEvent(id=uuid4(),crew_id=crew_id,call_sheet_version_id=version_id,kind=event_kind,object_lock_version=4,recipient_snapshots=candidates,created_at=ZERO,consumed_at=None)
        version=None
    published = [PublishedRecipient(crew_id=crew_id,call_sheet_version_id=version_id,member_id=member.id,membership_joined_at=member.joined_at,display_name='接收人') for member in members]
    state=NS(event=event,permissions={member.id:frozenset({'notification.read_self','plan.read_related','call_sheet.read_related'}) for member in members},published=published)
    async def scalar(query):
        entity=query.column_descriptions[0].get('entity')
        if entity is Crew: return Crew(id=crew_id)
        if entity in {PlanPublicationEvent,CallSheetEvent}: return state.event
        if entity is PlanVersion: return version
        return at(1)
    async def scalars(query):
        rows = members if query.column_descriptions[0].get('entity') is CrewMember else state.published
        return Mock(all=Mock(return_value=rows))
    session.scalar.side_effect=scalar; session.scalars.side_effect=scalars
    session.execute.return_value=Mock(one_or_none=Mock(return_value=(event.id,crew_id,kind)))
    monkeypatch.setattr(publications,'permissions_for',AsyncMock(side_effect=lambda *_:state.permissions))
    return NS(factory=factory,session=session,tx=tx,event=event,crew_id=crew_id,members=members,state=state)


def consume(v): return asyncio.run(module.NotificationConsumer(v.factory).consume_once())


def recipients(v):
    return [row for call in v.session.add_all.call_args_list for row in call.args[0] if isinstance(row,NotificationRecipient)]


def test_plan_publication_notifies_old_and_new_people_but_only_new_people_need_acknowledgement(monkeypatch):
    v=fixture(monkeypatch)
    assert consume(v)
    rows=recipients(v)
    assert {row.member_id for row in rows}=={member.id for member in v.members}
    assert {row.member_id for row in rows if row.requires_confirmation}=={v.members[0].id}
    notice=v.session.add.call_args.args[0]
    assert notice.plan_event_id==v.event.id and notice.source_event_id is None and notice.call_sheet_event_id is None
    assert notice.event_type=='plan.published' and notice.created_at==ZERO and v.event.consumed_at==at(1)
    assert '集合' not in notice.summary
    v.tx.__aexit__.assert_awaited_once_with(None,None,None)
    queries=[str(call.args[0].compile(dialect=postgresql.dialect())) for call in v.session.scalar.call_args_list]
    assert 'FOR UPDATE' in queries[0] and 'crews' in queries[0]
    assert 'SKIP LOCKED' in queries[1] and 'consumed_at IS NULL' in queries[1]
    candidate=str(v.session.execute.call_args.args[0])
    assert 'UNION ALL' in candidate and 'task_change_events' in candidate and 'call_sheet_events' in candidate


@pytest.mark.parametrize('kind',['published','review_required','withdrawn','superseded'])
def test_sheet_lifecycle_notices_preserve_published_receiver_scope(monkeypatch,kind):
    v=fixture(monkeypatch,'call_sheet',kind)
    assert consume(v)
    rows=recipients(v)
    assert len(rows)==2 and all(row.requires_confirmation==(kind=='published') for row in rows)
    notice=v.session.add.call_args.args[0]
    assert notice.call_sheet_event_id==v.event.id and notice.event_type==f'call_sheet.{kind}'
    assert v.event.consumed_at==at(1)


@pytest.mark.parametrize('change',['rejoined','notification-denied','source-denied','not-published-recipient'])
def test_delivery_does_not_restore_lost_identity_or_permissions(monkeypatch,change):
    v=fixture(monkeypatch,'call_sheet')
    person=v.members[0]
    if change=='rejoined': person.joined_at=ZERO
    elif change=='notification-denied': v.state.permissions[person.id]=frozenset({'call_sheet.read_related'})
    elif change=='source-denied': v.state.permissions[person.id]=frozenset({'notification.read_self'})
    else: v.state.published=v.state.published[1:]
    assert consume(v)
    assert {row.member_id for row in recipients(v)}=={v.members[1].id}
    eligibility=str(v.session.scalars.call_args_list[0].args[0])
    assert 'crew_members.status' in eligibility and 'users.status' in eligibility


def test_already_consumed_publication_has_no_second_notification_or_receipt_reset(monkeypatch):
    v=fixture(monkeypatch)
    consume(v); v.session.add.reset_mock(); v.session.add_all.reset_mock()
    v.state.event=None
    assert not consume(v)
    v.session.add.assert_not_called(); v.session.add_all.assert_not_called()


def test_publication_commit_failure_is_not_reported_as_success(monkeypatch):
    v=fixture(monkeypatch)
    v.tx.__aexit__.side_effect=OperationalError('private SQL',{},Exception('commit lost'))
    with pytest.raises(OperationalError): consume(v)


@pytest.mark.parametrize('source_type,status',[('plan','updated'),('plan','review_required'),('call_sheet','review_required'),('call_sheet','withdrawn'),('call_sheet','superseded')])
def test_invalidation_prevents_new_acknowledgement_but_keeps_existing_history(monkeypatch,source_type,status):
    factory,session,_,row=mark_fixture(monkeypatch)
    source=row[2]; source.source_type=source_type; source.is_current=False; source.source_status=status
    # Invalidation can keep the same public version number. Equality alone is
    # insufficient: the current pointer and validity must also be checked.
    source.current_object_version=source.object_version
    args=(uuid4(),row[0].crew_id,row[0].id,'acknowledge','a'*32)
    repo=module.NotificationRepository(factory)
    with pytest.raises(AuthError) as caught: asyncio.run(repo.mark(*args,object_version=source.object_version))
    assert caught.value.code=='NOTIFICATION_OUTDATED' and row[1].confirmed_at is None
    row[1].read_at=ZERO; row[1].confirmed_at=ZERO
    result=asyncio.run(repo.mark(*args,object_version=source.object_version))
    assert result.confirmed_at==ZERO and not result.is_current
    session.add.assert_not_called()


def test_unified_read_query_rechecks_source_access_and_recipient_epoch_without_requiring_task_permission():
    actor=TaskActor(uuid4(),frozenset({'notification.read_self','plan.read_related','call_sheet.read_related'}))
    query=notification_query(uuid4(),actor)
    sql=str(query.compile(dialect=postgresql.dialect()))
    assert 'notification_recipients.membership_joined_at = crew_members.joined_at' in sql
    assert 'call_sheet_published_recipients.membership_joined_at = crew_members.joined_at' in sql
    assert 'session_members.membership_joined_at = crew_members.joined_at' in sql
    assert 'jsonb_array_length' in sql and 'current_published_version_id' in sql and 'validity_status' in sql
    assert 'tasks.id IN (SELECT' not in sql
    # Projection contains source labels, not entire script/plan JSON snapshots.
    select_clause=sql[:sql.index('FROM notifications')]
    assert 'publication_snapshot' not in select_clause and 'contact_note' not in select_clause


def test_needs_acknowledgement_filter_uses_source_validity_and_current_pointer(monkeypatch):
    factory,session,_,_=mark_fixture(monkeypatch)
    session.scalar.return_value=0; session.execute.return_value=Mock(all=Mock(return_value=[]))
    asyncio.run(module.NotificationRepository(factory).list(uuid4(),uuid4(),1,20,needs_acknowledgement=True))
    sql=str(session.execute.call_args.args[0])
    assert 'requires_confirmation IS true' in sql and 'confirmed_at IS NULL' in sql
    assert 'validity_status' in sql and 'current_published_version_id' in sql and 'jsonb_array_length' in sql


def test_migration_preserves_task_data_and_adds_exclusive_same_crew_sources_and_pending_indexes(monkeypatch):
    monkeypatch.setattr(config_module,'load_settings',lambda:Settings(_env_file=None,database_url='postgresql+asyncpg://user:example@localhost:5432/test'))
    output=StringIO(); command.upgrade(Config('alembic.ini',output_buffer=output),'head',sql=True)
    sql=output.getvalue()
    assert '0018_publication_notices' in sql and 'ALTER COLUMN source_event_id DROP NOT NULL' in sql
    assert 'num_nonnulls(source_event_id, plan_event_id, call_sheet_event_id) = 1' in sql
    assert 'FOREIGN KEY(crew_id, plan_event_id) REFERENCES plan_publication_events (crew_id, id)' in sql
    assert 'FOREIGN KEY(crew_id, call_sheet_event_id) REFERENCES call_sheet_events (crew_id, id)' in sql
    assert 'UNIQUE (plan_event_id)' in sql and 'UNIQUE (call_sheet_event_id)' in sql
    assert 'CREATE INDEX ix_plan_events_pending' in sql and 'CREATE INDEX ix_task_events_pending' in sql
    assert 'DELETE FROM notifications' not in sql and 'TRUNCATE' not in sql
