import asyncio
from datetime import datetime, UTC, timedelta
from io import StringIO
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

from alembic import command
from alembic.config import Config
from pydantic import ValidationError
import pytest
from sqlalchemy.exc import OperationalError

from silencio_set.accounts.errors import AuthError
from silencio_set.core import config as config_module
from silencio_set.core.config import Settings
from silencio_set.creative.models import Breakdown, SceneRevision
from silencio_set.crews.models import CrewMember
from silencio_set.plans.domain import check_plan, missing_intervals
from silencio_set.plans import repository as module
from silencio_set.plans.models import ShootingPlan, PlanVersion, ShootingSession, PlanReceipt, SessionMember, SessionResource, PlanPreflight
from silencio_set.plans.schemas import PlanCreate, VersionCreate, SessionsSave, SessionInput
from silencio_set.resources.repository import ResourceActor
from silencio_set.resources.models import Resource, Reservation
from silencio_set.tasks.schemas import VersionInput
from test_account_repository import fake_session_factory
from test_crews import crew_client
from test_auth_api import auth_client

ZERO = datetime(2026, 11, 2, tzinfo=UTC)
def at(hour): return ZERO + timedelta(hours=hour)


def context():
    member, resource, booking, scene, session = [uuid4() for _ in range(5)]
    item = dict(id=session, scene_revision_id=scene, starts_at=at(1), ends_at=at(2), transition_minutes=0,
        location_resource_id=resource, location_reservation_id=booking, members=[dict(member_id=member, membership_joined_at=ZERO)], resources=[])
    data = dict(sources={scene: dict(status='confirmed', script_version_id='script-v1', current_version_id='script-v1')},
        members={member: dict(status='active', joined_at=ZERO)}, resources={resource: NS(resource_type='location', status='available', total_quantity=1)},
        reservations={booking: NS(id=booking, resource_id=resource, status='confirmed', starts_at=at(0), ends_at=at(4), quantity=1)},
        calendars={member: [NS(starts_at=at(0), ends_at=at(4), availability='available')]}, scene_needs={}, lock_version=1)
    return item, data


def codes(report): return {item.code for item in report.blockers}


def test_clear_local_check_never_grants_publication():
    item, data = context()
    report = check_plan([item], **data)
    assert report.blockers == report.unknown_items == []
    assert report.can_publish is False and report.limitations


def test_unknown_is_exact_gaps_instead_of_assuming_available():
    item, data = context(); member = item['members'][0]['member_id']
    data['calendars'][member] = [NS(starts_at=at(1), ends_at=at(1.25), availability='available'), NS(starts_at=at(1.75), ends_at=at(2), availability='available')]
    report = check_plan([item], **data)
    assert not report.blockers
    assert [(row.starts_at, row.ends_at) for row in report.unknown_items] == [(at(1.25), at(1.75))]
    assert missing_intervals(at(1), at(2), []) == [(at(1), at(2))]


def test_known_unavailability_remains_a_blocker():
    item, data = context(); member = item['members'][0]['member_id']
    data['calendars'][member] = [NS(starts_at=at(1), ends_at=at(2), availability='unavailable')]
    report = check_plan([item], **data)
    assert codes(report) == {'MEMBER_UNAVAILABLE'} and not report.unknown_items


@pytest.mark.parametrize('minutes,conflict', [(0, False), (15, True)])
def test_half_open_sessions_and_preparation_time(minutes, conflict):
    item, data = context(); item['transition_minutes'] = minutes
    second = dict(item, id=uuid4(), starts_at=at(2), ends_at=at(3), transition_minutes=0)
    report = check_plan([item, second], **data)
    assert ('MEMBER_SESSION_OVERLAP' in codes(report)) == conflict


def test_rejoined_members_are_not_silently_reassigned():
    item, data = context(); member = item['members'][0]['member_id']
    data['members'][member]['joined_at'] += timedelta(seconds=1)
    assert 'MEMBER_INACTIVE' in codes(check_plan([item], **data))


@pytest.mark.parametrize('status,end,quantity', [('pending', 4, 1), ('confirmed', 1.5, 1), ('confirmed', 4, 0)])
def test_pending_or_insufficient_bookings_do_not_count_as_covered(status, end, quantity):
    item, data = context(); booking = data['reservations'][item['location_reservation_id']]
    booking.status, booking.ends_at, booking.quantity = status, at(end), quantity
    assert 'RESERVATION_NOT_COVERED' in codes(check_plan([item], **data))


def test_shared_booking_is_checked_by_concurrent_allocations_without_double_occupancy():
    item, data = context(); resource = item['location_resource_id']
    data['resources'][resource].total_quantity = 2
    second = dict(item, id=uuid4(), members=[])
    report = check_plan([item, second], **data)
    assert 'RESERVATION_ALLOCATION_CONFLICT' in codes(report)
    assert 'RESOURCE_CAPACITY_CONFLICT' not in codes(report)


def test_consumables_sum_across_sequential_sessions_and_do_not_need_bookings():
    item, data = context(); consumable = uuid4()
    data['resources'][consumable] = NS(resource_type='consumable', status='available', stock_quantity=3)
    item['resources'] = [dict(resource_id=consumable, quantity=2, reservation_id=None)]
    second = dict(item, id=uuid4(), starts_at=at(2), ends_at=at(3))
    report = check_plan([item, second], **data)
    assert codes(report) == {'CONSUMABLE_SHORTAGE'}


def test_source_changes_and_unlinked_scene_requirements_are_visible():
    item, data = context(); scene = item['scene_revision_id']
    data['sources'][scene]['current_version_id'] = 'new-script'
    data['scene_needs'][scene] = [NS(resource_id=None, quantity=1, name='收音设备')]
    assert codes(check_plan([item], **data)) == {'SOURCE_REVIEW_REQUIRED', 'SCENE_RESOURCE_UNLINKED'}


@pytest.mark.parametrize('change', [dict(ends_at=at(0)), dict(starts_at='2026-11-02T09:00:00'), dict(transition_minutes=-1), dict(ends_at=at(24*32))])
def test_session_bounds_and_timezones(change):
    value = dict(scene_revision_id=uuid4(), starts_at=at(1), ends_at=at(2)) | change
    with pytest.raises(ValidationError): SessionInput(**value)


def fixture(monkeypatch):
    factory, session, tx = fake_session_factory()
    crew, member, user, plan_id, version_id = [uuid4() for _ in range(5)]
    actor = ResourceActor(member, frozenset({'plan.edit', 'plan.read_all', 'resource.read', 'member.read', 'availability.read_all'}), frozenset({'producer'}), ZERO)
    monkeypatch.setattr(module, 'resource_actor', AsyncMock(return_value=actor))
    plan = ShootingPlan(id=plan_id, crew_id=crew, name='拍摄计划', created_by=member, created_at=ZERO)
    version = PlanVersion(id=version_id, crew_id=crew, plan_id=plan_id, source_version_id=None, version_no=1, lock_version=1, status='draft', review_reasons=[], created_by=member, created_at=ZERO, updated_at=ZERO)
    async def scalar(query):
        if 'max(' in str(query): return 1
        return {ShootingPlan:plan, PlanVersion:version, PlanReceipt:None}.get(query.column_descriptions[0].get('entity'), ZERO)
    session.scalar.side_effect = scalar
    session.scalars.return_value = Mock(all=Mock(return_value=[]))
    return module.PlanRepository(factory), session, tx, actor, plan, version, user


def mutate(repo, plan, user, action, body, target=None):
    return asyncio.run(repo.mutate(user, plan.crew_id, action, body, 'a'*32, object_id=target, idempotency_key=str(uuid4())))


def test_create_and_version_copy_do_not_create_bookings(monkeypatch):
    repo, session, tx, _, plan, version, user = fixture(monkeypatch)
    result = mutate(repo, plan, user, 'create', PlanCreate(name='首日计划'))
    assert result.name == '首日计划'
    result = mutate(repo, plan, user, 'version', VersionCreate(copy_from=version.id), plan.id)
    assert result.version_no == 2 and result.source_version_id == version.id and result.sessions == []
    assert any(isinstance(call.args[0], PlanReceipt) for call in session.add.call_args_list)
    tx.__aexit__.assert_awaited_with(None, None, None)


def test_save_version_conflict_keeps_existing_sessions(monkeypatch):
    repo, session, _, _, plan, version, user = fixture(monkeypatch)
    with pytest.raises(AuthError) as caught:
        mutate(repo, plan, user, 'save', SessionsSave(expected_lock_version=2, sessions=[]), version.id)
    assert caught.value.code == 'VERSION_CONFLICT'
    session.execute.assert_not_awaited()


@pytest.mark.parametrize('invalid', [None, 'source', 'membership', 'booking'])
def test_session_save_checks_references_before_replacing_and_persists_membership(monkeypatch, invalid):
    repo, session, _, actor, plan, version, user = fixture(monkeypatch)
    source = SceneRevision(id=uuid4(), crew_id=plan.crew_id, breakdown_id=uuid4())
    breakdown = Breakdown(id=source.breakdown_id, crew_id=plan.crew_id, review_status='draft' if invalid == 'source' else 'confirmed')
    person = CrewMember(id=uuid4(), crew_id=plan.crew_id, status='active', joined_at=ZERO)
    location = Resource(id=uuid4(), crew_id=plan.crew_id, resource_type='location')
    booking = Reservation(id=uuid4(), crew_id=plan.crew_id, resource_id=uuid4() if invalid == 'booking' else location.id)
    async def scalars(query):
        values = {SceneRevision:[source], Breakdown:[breakdown], CrewMember:[person], Resource:[location], Reservation:[booking]}
        return Mock(all=Mock(return_value=values.get(query.column_descriptions[0].get('entity'), [])))
    session.scalars.side_effect = scalars
    body = SessionsSave(expected_lock_version=1, sessions=[SessionInput(scene_revision_id=source.id, starts_at=at(1), ends_at=at(2),
        location_resource_id=location.id, location_reservation_id=booking.id,
        members=[dict(member_id=person.id, membership_joined_at=ZERO+timedelta(seconds=1) if invalid == 'membership' else ZERO)])])
    if invalid:
        with pytest.raises(AuthError) as caught: mutate(repo, plan, user, 'save', body, version.id)
        assert caught.value.code == {'source':'SOURCE_NOT_CONFIRMED', 'membership':'MEMBER_INACTIVE', 'booking':'RESERVATION_RESOURCE_MISMATCH'}[invalid]
        session.execute.assert_not_awaited()
    else:
        result = mutate(repo, plan, user, 'save', body, version.id)
        assert result.lock_version == 2
        row = next(call.args[0] for call in session.add.call_args_list if isinstance(call.args[0], ShootingSession))
        links = [value for call in session.add_all.call_args_list for value in call.args[0]]
        member_link = next(value for value in links if isinstance(value, SessionMember))
        assert row.scene_revision_id == source.id and row.location_reservation_id == booking.id
        assert member_link.membership_joined_at == ZERO and member_link.session_id == row.id


def test_empty_preflight_records_evidence_but_cannot_be_published(monkeypatch):
    repo, session, _, _, plan, version, user = fixture(monkeypatch)
    report = asyncio.run(repo.preflight(user, plan.crew_id, version.id, VersionInput(expected_lock_version=1)))
    assert codes(report) == {'EMPTY_PLAN'} and report.can_publish is False
    assert report.checked_data_revision and any(isinstance(call.args[0], PlanPreflight) for call in session.add.call_args_list)


def test_related_read_permission_does_not_expose_unpublished_drafts(monkeypatch):
    repo, session, _, actor, plan, version, user = fixture(monkeypatch)
    monkeypatch.setattr(module, 'resource_actor', AsyncMock(return_value=ResourceActor(actor.member_id, frozenset({'plan.read_related'}), actor.roles, ZERO)))
    with pytest.raises(AuthError) as caught:
        asyncio.run(repo.read(user, plan.crew_id, version_id=version.id))
    assert caught.value.code == 'NOT_FOUND'
    session.scalars.assert_not_awaited()


def test_prior_membership_receipt_is_not_replayed(monkeypatch):
    repo, session, _, _, plan, _, user = fixture(monkeypatch)
    session.scalar.side_effect = None
    session.scalar.return_value = PlanReceipt(membership_joined_at=ZERO-timedelta(seconds=1))
    with pytest.raises(AuthError) as caught: mutate(repo, plan, user, 'create', PlanCreate(name='计划'))
    assert caught.value.code == 'MEMBERSHIP_CHANGED'


def test_failed_commit_is_not_reported_as_success(monkeypatch):
    repo, _, tx, _, plan, _, user = fixture(monkeypatch)
    tx.__aexit__.side_effect = OperationalError('private', {}, Exception('commit lost'))
    with pytest.raises(OperationalError): mutate(repo, plan, user, 'create', PlanCreate(name='计划'))


def test_plan_routes_enforce_csrf_and_publish_requires_complete_fields(crew_client):
    client, _, _, crew = crew_client
    client.app.state.plans = Mock()
    path = f'/api/v1/crews/{crew.id}/shooting-plans'
    response = client.post(path, json={'name':'计划'}, headers={'Idempotency-Key':str(uuid4()), 'X-CSRF-Token':'bad'})
    assert response.status_code == 403
    assert client.post(f'/api/v1/crews/{crew.id}/plan-versions/{uuid4()}/publish', json={}).status_code == 422


def test_offline_migration_preserves_same_crew_links_and_draft_only_status(monkeypatch):
    monkeypatch.setattr(config_module, 'load_settings', lambda: Settings(_env_file=None, database_url='postgresql+asyncpg://user:example@localhost:5432/test'))
    output = StringIO(); command.upgrade(Config('alembic.ini', output_buffer=output), 'head', sql=True)
    sql = output.getvalue()
    assert '0015_plan_drafts' in sql and 'CREATE TABLE shooting_sessions' in sql
    assert 'FOREIGN KEY(crew_id, reservation_id) REFERENCES resource_reservations (crew_id, id)' in sql
    assert "CHECK (status = 'draft')" in sql
