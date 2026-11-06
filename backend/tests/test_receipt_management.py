import asyncio
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy.exc import OperationalError

from silencio_set.accounts.errors import AuthError
from silencio_set.accounts.models import User
from silencio_set.crews.models import CrewMember
from silencio_set.notifications import receipts as module
from silencio_set.notifications.models import NotificationRecipient
from silencio_set.notifications.repository import NotificationRepository
from silencio_set.notifications.service import NotificationService
from silencio_set.plans.models import PlanVersion, PlanPublicationEvent, ShootingPlan
from silencio_set.call_sheets.models import CallSheetVersion, CallSheet, CallSheetEvent, PublishedRecipient
from silencio_set.resources.repository import ResourceActor
from test_account_repository import fake_session_factory
from test_crews import ready_user, crew_client
from test_auth_api import auth_client
from test_plans import ZERO, at


def sample(*, processed=True, status='current'):
    ids = [uuid4() for _ in range(3)]
    keys = [(member,ZERO) for member in ids]
    event = NS(recipient_snapshots=[{'member_id':str(member),'membership_joined_at':ZERO.isoformat()} for member in ids], consumed_at=at(1) if processed else None)
    names = {key:name for key,name in zip(keys,['演员甲','演员乙','演员丙'])}
    rows = [NotificationRecipient(member_id=ids[0], membership_joined_at=ZERO, requires_confirmation=True, read_at=at(1), confirmed_at=at(1)),
            NotificationRecipient(member_id=ids[1], membership_joined_at=ZERO, requires_confirmation=True, read_at=None, confirmed_at=None)] if processed else []
    people = {member:(CrewMember(id=member, joined_at=ZERO, status='active'), 'active') for member in ids}
    return dict(kind='plan', version=NS(id=uuid4(), version_no=2), source_status=status, event=event, names=names,
                required=set(keys), receipts=rows, people=people, now=at(2), page=1, page_size=20)


def test_counts_do_not_equate_generated_read_and_confirmed_and_show_skips():
    result = module.build_page(**sample())
    assert result.counts.model_dump() == dict(intended=3, generated=2, pending_dispatch=0, skipped=1, read=1, confirmed=1, unconfirmed=1)
    assert {row.notification_status for row in result.items} == {'generated','skipped'}


def test_pending_dispatch_never_looks_like_nonread_delivery():
    result = module.build_page(**sample(processed=False))
    assert result.dispatch_status == 'pending_dispatch' and result.counts.pending_dispatch == 3
    assert result.counts.generated == result.counts.skipped == result.counts.unconfirmed == 0


@pytest.mark.parametrize('change,expected', [('epoch','rejoined'),('left','left'),('user','disabled')])
def test_historical_epoch_and_identity_are_separate_from_receipt_history(change, expected):
    data = sample(status='withdrawn'); key = next(iter(data['people']))
    member, _ = data['people'][key]
    if change == 'epoch': member.joined_at = at(2)
    elif change == 'left': member.status = 'removed'
    else: data['people'][key] = (member,'disabled')
    result = module.build_page(**data)
    entry = next(row for row in result.items if row.member_id == key)
    assert entry.identity_status == expected and entry.confirmed_at == at(1)
    assert result.source_status == 'withdrawn' and result.counts.confirmed == 1


def test_stable_page_order_and_empty_page_keep_full_summary():
    data = sample(); data.update(page=2, page_size=2)
    result = module.build_page(**data)
    assert len(result.items) == 1 and result.total == result.counts.intended == 3
    data['page'] = 3
    assert module.build_page(**data).items == []


@pytest.mark.parametrize('kind,permissions,allowed', [
    ('plan', {'plan.edit','plan.publish','member.read'},True),
    ('plan', {'plan.read_all','notification.receipts','member.read'},True),
    ('plan', {'plan.read_related','notification.receipts','member.read'},False),
    ('plan', {'plan.read_all','member.read'},False),
    ('call_sheet', {'call_sheet.publish','member.read'},True),
    ('call_sheet', {'call_sheet.review','notification.receipts','member.read'},True),
    ('call_sheet', {'call_sheet.read_related','notification.receipts','member.read'},False),
    ('plan', {'plan.edit','plan.publish'},False),
])
def test_manager_needs_both_source_access_and_receipt_authority(kind, permissions, allowed):
    actor = NS(permissions=permissions)
    if allowed: module.authorize(actor, kind)
    else:
        with pytest.raises(AuthError) as caught: module.authorize(actor, kind)
        assert caught.value.code == 'FORBIDDEN'


def fixture(monkeypatch, kind='plan'):
    factory, session, tx = fake_session_factory()
    data = sample(); crew, user = uuid4(), uuid4()
    version = PlanVersion(id=data['version'].id, crew_id=crew, plan_id=uuid4(), version_no=2, status='published', review_reasons=[],
        publication_snapshot={'items':[{'members':[dict(member_id=str(key[0]),membership_joined_at=key[1].isoformat(),display_name=name) for key,name in data['names'].items()]}]})
    plan = ShootingPlan(id=version.plan_id, crew_id=crew, current_published_version_id=version.id)
    event = PlanPublicationEvent(id=uuid4(), crew_id=crew, kind='published', plan_version_id=version.id, previous_version_id=None,
        recipient_snapshots=data['event'].recipient_snapshots, consumed_at=at(1))
    sheet_version = CallSheetVersion(id=uuid4(), crew_id=crew, call_sheet_id=uuid4(), plan_id=plan.id, source_plan_version_id=version.id,
        version_no=1, status='published', validity_status='valid')
    sheet = CallSheet(id=sheet_version.call_sheet_id, crew_id=crew, current_published_version_id=sheet_version.id)
    sheet_event = CallSheetEvent(id=uuid4(), kind='published', recipient_snapshots=event.recipient_snapshots, consumed_at=at(1))
    actor = ResourceActor(uuid4(), frozenset({'member.read','plan.publish','plan.edit','call_sheet.publish'}), frozenset(),ZERO)
    monkeypatch.setattr(module,'resource_actor',AsyncMock(return_value=actor))
    async def scalar(q):
        return {PlanVersion:version, ShootingPlan:plan, PlanPublicationEvent:event,
                CallSheetVersion:sheet_version, CallSheet:sheet, CallSheetEvent:sheet_event}.get(q.column_descriptions[0].get('entity'),at(2))
    async def scalars(q):
        rows = data['receipts'] if q.column_descriptions[0].get('entity') is NotificationRecipient else [PublishedRecipient(member_id=key[0],membership_joined_at=key[1],display_name=name) for key,name in data['names'].items()]
        return Mock(all=Mock(return_value=rows))
    session.scalar.side_effect = scalar; session.scalars.side_effect = scalars
    session.execute.return_value = Mock(all=Mock(return_value=list(data['people'].values())))
    return NS(repo=NotificationRepository(factory),session=session,tx=tx,version=version,sheet=sheet_version,crew=crew,user=user,kind=kind)


@pytest.mark.parametrize('kind',['plan','call_sheet'])
def test_repository_uses_frozen_recipients_and_never_writes_receipts(monkeypatch, kind):
    v = fixture(monkeypatch,kind)
    result = asyncio.run(module.read(v.repo,v.user,v.crew,kind=kind,version_id=(v.version if kind=='plan' else v.sheet).id))
    assert result.source_status == 'current' and result.counts.confirmed == 1
    assert result.counts.skipped == 1
    v.session.add.assert_not_called(); v.session.flush.assert_not_awaited()
    query = str(v.session.scalars.call_args.args[0])
    assert 'notifications' in query and ('plan_event_id' if kind=='plan' else 'call_sheet_event_id') in query


def test_draft_has_no_receipts_and_other_crew_is_hidden(monkeypatch):
    v = fixture(monkeypatch); v.version.status = 'draft'
    with pytest.raises(AuthError) as caught: asyncio.run(module.read(v.repo,v.user,v.crew,kind='plan',version_id=v.version.id))
    assert caught.value.code == 'NOT_PUBLISHED'
    v.session.scalars.assert_not_awaited()
    v.session.scalar.return_value = None; v.session.scalar.side_effect = None
    with pytest.raises(AuthError) as caught: asyncio.run(module.read(v.repo,v.user,v.crew,kind='plan',version_id=uuid4()))
    assert caught.value.status == 404


def test_service_sanitizes_storage_failure_and_audits_without_recipient_details(monkeypatch):
    repo, audits = Mock(), Mock(record_failure=AsyncMock())
    monkeypatch.setattr(module,'read',AsyncMock(side_effect=OperationalError('private SQL',{},Exception('credentials'))))
    with pytest.raises(AuthError) as caught:
        asyncio.run(NotificationService(repo,audits).execute(ready_user(),'receipts','a'*32,crew_id=uuid4(),kind='plan',version_id=uuid4()))
    assert caught.value.status == 503 and 'credentials' not in caught.value.public_message
    audits.record_failure.assert_awaited_once()


def test_http_uses_get_without_csrf_or_side_effects(crew_client, monkeypatch):
    client, _, user, crew = crew_client
    v = fixture(monkeypatch)
    result = module.build_page(**sample())
    service = Mock(execute=AsyncMock(return_value=result))
    client.app.state.notifications = service
    client.headers.pop('X-CSRF-Token',None)
    path = f'/api/v1/crews/{crew.id}/notifications/receipts/plan/{v.version.id}'
    assert client.get(path).status_code == 200
    assert service.execute.call_args.args[:2] == (user,'receipts')
    assert client.get(path+'?page_size=21').status_code == 422
    assert client.post(path,json={}).status_code == 405
