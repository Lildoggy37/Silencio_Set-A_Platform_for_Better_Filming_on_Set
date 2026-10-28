import asyncio
from datetime import UTC, datetime, timedelta
from io import StringIO
from random import Random
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

from alembic import command
from alembic.config import Config
from pydantic import ValidationError
import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import OperationalError

from silencio_set.accounts.errors import AuthError
from silencio_set.availability import repository as module
from silencio_set.availability.models import AvailabilitySet, MemberAvailability
from silencio_set.availability.repository import AvailabilityRepository, calendar_query, calendar_view, replacement_fragments
from silencio_set.availability.schemas import AvailabilityInterval, QueryWindow, ReplaceAvailability
from silencio_set.availability.service import AvailabilityService
from silencio_set.core import config as config_module
from silencio_set.core.config import Settings
from silencio_set.crews.models import AuditLog, CrewMember
from silencio_set.tasks.repository import TaskActor
from test_account_repository import fake_session_factory
from test_auth_api import auth_client
from test_crews import crew_client

START = datetime(2026, 11, 1, tzinfo=UTC)
EPOCH = START - timedelta(days=10, microseconds=123456)
def hour(value): return START + timedelta(hours=value)
def interval(start, end, status="available"):
    return AvailabilityInterval(starts_at=hour(start), ends_at=hour(end), availability=status)
def body(**changes):
    return ReplaceAvailability(**({"range_start": hour(10), "range_end": hour(14), "expected_lock_version": 1,
        "expected_membership_joined_at": EPOCH, "intervals": [interval(11, 12)]} | changes))
def member():
    return CrewMember(id=uuid4(), crew_id=uuid4(), user_id=uuid4(), display_name="摄影", status="active", lock_version=1, joined_at=EPOCH)
def collection(person, **changes):
    return AvailabilitySet(**({"crew_id": person.crew_id, "member_id": person.id, "membership_joined_at": person.joined_at,
        "lock_version": 1, "updated_at": hour(0)} | changes))


@pytest.mark.parametrize("changes", [
    {"range_end": hour(10)}, {"range_end": hour(10) + timedelta(days=32)}, {"range_start": "2026-11-01T10:00:00"},
    {"range_start": 1_900_000_000}, {"range_start": "1900000000"}, {"range_start": hour(10) + timedelta(microseconds=1)},
    {"expected_lock_version": True}, {"expected_lock_version": "1"}, {"expected_lock_version": 0},
    {"expected_membership_joined_at": "2026-10-01T10:00:00"}, {"member_id": str(uuid4())},
    {"intervals": [interval(9, 11)]}, {"intervals": [interval(11, 15)]}, {"intervals": [interval(10, 12), interval(11, 13)]},
])
def test_replace_rejects_ambiguous_dates_bad_versions_spoofed_target_and_overlaps(changes):
    with pytest.raises(ValidationError): body(**changes)


def test_adjoining_cross_midnight_intervals_and_microsecond_membership_epoch_are_valid():
    item = body(range_start=hour(22), range_end=hour(27), intervals=[interval(24, 26, "unavailable"), interval(22, 24)])
    assert item.intervals[0].ends_at == item.intervals[1].starts_at
    assert item.expected_membership_joined_at == EPOCH
    with pytest.raises(ValidationError): interval(12, 12)
    with pytest.raises(ValidationError): AvailabilityInterval(starts_at=hour(1), ends_at=hour(2), availability="unknown")


def test_partial_replacement_preserves_both_external_fragments_and_leaves_inner_gaps_unknown():
    existing = [interval(8, 16)]
    result = replacement_fragments(existing, body())
    assert [(item.starts_at, item.ends_at) for item in result] == [(hour(8), hour(10)), (hour(11), hour(12)), (hour(14), hour(16))]
    person = member(); view = calendar_view(person, collection(person), body().intervals, body())
    assert [(item.starts_at, item.ends_at) for item in view.unregistered] == [(hour(10), hour(11)), (hour(12), hour(14))]
    assert view.plan_review_status == "not_implemented"


def test_range_replacement_invariants_hold_for_many_boundary_and_status_combinations():
    random = Random(37)
    def state(rows, point): return next((row.availability for row in rows if row.starts_at <= point < row.ends_at), None)
    for _ in range(150):
        rows = [interval(index, index + 1, random.choice(["available", "unavailable"])) for index in range(0, 24, 2)]
        left = random.randint(1, 14); right = random.randint(left + 2, 23)
        request = body(range_start=hour(left), range_end=hour(right), intervals=[interval(left, left + 1, "unavailable")])
        overlap = [row for row in rows if row.starts_at < request.range_end and row.ends_at > request.range_start]
        result = [row for row in rows if row not in overlap] + replacement_fragments(overlap, request)
        result.sort(key=lambda row: row.starts_at)
        assert all(a.ends_at <= b.starts_at for a, b in zip(result, result[1:]))
        for half_hour in range(48):
            point = hour(half_hour / 2)
            expected = state(request.intervals, point) if request.range_start <= point < request.range_end else state(rows, point)
            assert state(result, point) == expected


def test_rejoin_hides_old_calendar_but_never_resets_collection_version():
    person = member(); head = collection(person, membership_joined_at=EPOCH - timedelta(days=1), lock_version=9)
    view = calendar_view(person, head, [interval(10, 14)], body())
    assert view.intervals == [] and view.lock_version == 9 and view.updated_at is None
    assert len(view.unregistered) == 1 and view.membership_joined_at == EPOCH


def replace_fixture(monkeypatch, *, legacy=False, absent=False):
    factory, session, transaction = fake_session_factory(); person = member()
    actor = TaskActor(person.id, frozenset({"availability.self"}))
    monkeypatch.setattr(module, "task_actor", AsyncMock(return_value=actor))
    head = None if absent else collection(person, membership_joined_at=EPOCH - timedelta(days=1) if legacy else EPOCH)
    session.scalar.side_effect = [person, head, hour(0)] + ([] if legacy or absent else [1])
    row = MemberAvailability(id=uuid4(), crew_id=person.crew_id, member_id=person.id,
        starts_at=hour(8), ends_at=hour(16), availability="available", lock_version=1)
    session.scalars.return_value = Mock(all=Mock(return_value=[row]))
    return factory, session, transaction, person, head, row


def test_replace_keeps_membership_version_audit_and_fragments_in_one_commit(monkeypatch):
    factory, session, transaction, person, head, row = replace_fixture(monkeypatch)
    result = asyncio.run(AvailabilityRepository(factory).replace(person.user_id, person.crew_id, body(), "a" * 32))
    assert result.lock_version == 2 and result.intervals == body().intervals
    assert head.lock_version == 2 and head.membership_joined_at == person.joined_at
    added = [call.args[0] for call in session.add.call_args_list]
    saved = [row for row in added if isinstance(row, MemberAvailability)]
    assert len(saved) == 3 and any(row.starts_at == hour(8) and row.ends_at == hour(10) for row in saved)
    assert all(row.member_id == person.id and row.crew_id == person.crew_id for row in saved)
    assert any(isinstance(row, AuditLog) for row in added)
    deletion = session.execute.call_args.args[0].compile(dialect=postgresql.dialect())
    assert "starts_at <" in str(deletion) and "ends_at >" in str(deletion) and person.id in deletion.params.values()
    assert module.task_actor.call_args.kwargs["writing"] is True
    transaction.__aexit__.assert_awaited_once_with(None, None, None)


@pytest.mark.parametrize("legacy,absent", [(True, False), (False, True)])
def test_first_write_or_new_membership_clears_only_its_own_old_rows(monkeypatch, legacy, absent):
    factory, session, _, person, _, _ = replace_fixture(monkeypatch, legacy=legacy, absent=absent)
    result = asyncio.run(AvailabilityRepository(factory).replace(person.user_id, person.crew_id, body(), "a" * 32))
    assert result.lock_version == 2
    deletion = str(session.execute.call_args.args[0])
    assert "member_id =" in deletion and "crew_id =" in deletion and "starts_at <" not in deletion
    session.scalars.assert_not_awaited()


@pytest.mark.parametrize("change,code", [("epoch", "MEMBERSHIP_CHANGED"), ("version", "VERSION_CONFLICT"), ("exhausted", "VERSION_EXHAUSTED")])
def test_stale_membership_or_collection_version_blocks_before_deletion(monkeypatch, change, code):
    factory, session, _, person, head, _ = replace_fixture(monkeypatch)
    request = body()
    if change == "epoch": request.expected_membership_joined_at -= timedelta(days=1)
    if change == "version": head.lock_version = 3
    if change == "exhausted": head.lock_version = request.expected_lock_version = 2_147_483_647
    with pytest.raises(AuthError) as caught: asyncio.run(AvailabilityRepository(factory).replace(person.user_id, person.crew_id, request, "a" * 32))
    assert caught.value.code == code
    session.execute.assert_not_awaited(); session.add.assert_not_called()


def test_capacity_rejection_does_not_erase_existing_rows(monkeypatch):
    factory, session, _, person, head, _ = replace_fixture(monkeypatch)
    session.scalar.side_effect = [person, head, hour(0), 2000]
    with pytest.raises(AuthError) as caught: asyncio.run(AvailabilityRepository(factory).replace(person.user_id, person.crew_id, body(), "a" * 32))
    assert caught.value.code == "AVAILABILITY_CAPACITY"
    session.execute.assert_not_awaited()


def test_commit_failure_does_not_return_save_success(monkeypatch):
    factory, _, transaction, person, _, _ = replace_fixture(monkeypatch)
    transaction.__aexit__.side_effect = OperationalError("private SQL", {}, Exception("unknown commit"))
    with pytest.raises(OperationalError): asyncio.run(AvailabilityRepository(factory).replace(person.user_id, person.crew_id, body(), "a" * 32))


def test_read_uses_single_snapshot_with_current_membership_scope_and_clips_intervals():
    person = member(); window = body()
    query = calendar_query(person.crew_id, [person.id], window).compile(dialect=postgresql.dialect())
    assert "member_availability_sets.membership_joined_at = crew_members.joined_at" in str(query)
    assert "users.status =" in str(query) and "crew_members.status =" in str(query)
    assert person.id in next(value for value in query.params.values() if isinstance(value, list))
    view = calendar_view(person, collection(person), [interval(8, 16)], window)
    assert view.intervals == [interval(10, 14)] and view.unregistered == []


def test_oversized_read_never_returns_truncated_registration_as_complete(monkeypatch):
    factory, session, _ = fake_session_factory(); person = member()
    monkeypatch.setattr(module, "task_actor", AsyncMock(return_value=TaskActor(person.id, frozenset({"availability.self"}))))
    session.execute.return_value = Mock(all=Mock(return_value=[(person, None, None)] * 501))
    with pytest.raises(AuthError) as caught: asyncio.run(AvailabilityRepository(factory).own(person.user_id, person.crew_id, body()))
    assert caught.value.code == "AVAILABILITY_WINDOW_TOO_DENSE"


def test_group_read_requires_planning_permission_even_for_admin(monkeypatch):
    factory, session, _ = fake_session_factory()
    monkeypatch.setattr(module, "task_actor", AsyncMock(return_value=TaskActor(uuid4(), frozenset({"member.authorize", "availability.self"}))))
    with pytest.raises(AuthError) as caught: asyncio.run(AvailabilityRepository(factory).list(uuid4(), uuid4(), body(), 1, 20))
    assert caught.value.code == "FORBIDDEN"
    session.execute.assert_not_awaited(); session.scalar.assert_not_awaited()


def configure_api(crew_client):
    client, audits, user, crew = crew_client
    person = member(); person.id = crew.membership.id; person.crew_id = crew.id
    result = calendar_view(person, None, [], body())
    repo = Mock(own=AsyncMock(return_value=result), replace=AsyncMock(return_value=result))
    client.app.state.availability = AvailabilityService(repo, audits)
    return client, repo, user, crew


def test_put_only_targets_authenticated_users_own_membership(crew_client):
    client, repo, user, crew = configure_api(crew_client)
    url = f"/api/v1/crews/{crew.id}/members/me/availability"
    response = client.put(url, json=body().model_dump(mode="json"))
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    assert repo.replace.call_args.args[0] == user.id
    assert client.put(f"/api/v1/crews/{crew.id}/members/{uuid4()}/availability", json=body().model_dump(mode="json")).status_code == 404
    assert client.put(url, json=body().model_dump(mode="json") | {"member_id": str(uuid4())}).status_code == 422
    assert repo.replace.await_count == 1


@pytest.mark.parametrize("header,value", [("Origin", "https://evil.example"), ("X-CSRF-Token", "wrong")])
def test_put_requires_origin_and_csrf(crew_client, header, value):
    client, repo, _, crew = configure_api(crew_client)
    response = client.put(f"/api/v1/crews/{crew.id}/members/me/availability", json=body().model_dump(mode="json"), headers={header: value})
    assert response.status_code == 403
    repo.replace.assert_not_awaited()


def test_queries_require_bounded_aware_range_and_do_not_echo_bad_input(crew_client):
    client, repo, _, crew = configure_api(crew_client)
    url = f"/api/v1/crews/{crew.id}/members/me/availability"
    assert client.get(url).status_code == 422
    assert client.get(url, params={"range_start": "2026-11-01T10:00:00", "range_end": "2026-11-01T14:00:00Z"}).status_code == 422
    response = client.get(url, params={"range_start": "private-value", "range_end": "2026-11-01T14:00:00Z"})
    assert response.status_code == 422 and "private-value" not in response.text
    repo.own.assert_not_awaited()


def test_migration_has_same_crew_keys_half_open_exclusion_and_required_extension(monkeypatch):
    settings = Settings(_env_file=None, database_url="postgresql+asyncpg://user:example@localhost:5432/test")
    monkeypatch.setattr(config_module, "load_settings", lambda: settings)
    output = StringIO(); command.upgrade(Config("alembic.ini", output_buffer=output), "head", sql=True)
    sql = output.getvalue()
    assert "0008_availability" in sql and "CREATE EXTENSION IF NOT EXISTS btree_gist" in sql
    assert "EXCLUDE USING gist" in sql and "tstzrange(starts_at, ends_at, '[)') WITH &&" in sql
    assert "FOREIGN KEY(crew_id, member_id) REFERENCES member_availability_sets (crew_id, member_id)" in sql
    assert "membership_joined_at TIMESTAMP WITH TIME ZONE NOT NULL" in sql
