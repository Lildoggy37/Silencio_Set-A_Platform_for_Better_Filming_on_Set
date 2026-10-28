import asyncio
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from io import StringIO
from random import Random
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

from alembic import command
from alembic.config import Config
from pydantic import ValidationError
import pytest
from sqlalchemy.dialects.postgresql import dialect
from sqlalchemy.exc import OperationalError

from silencio_set.accounts.errors import AuthError
from silencio_set.core import config as config_module
from silencio_set.core.config import Settings
from silencio_set.crews.models import AuditLog, CrewMember, MemberPermissionOverride
from silencio_set.crews.permissions import effective_permissions
from silencio_set.resources import repository as module
from silencio_set.resources.domain import peak_quantity, require_capacity
from silencio_set.resources.models import Resource, ResourceScopeGrant, Reservation, ResourceReceipt, ResourceChangeEvent
from silencio_set.resources.repository import ResourceRepository, ResourceActor, can_manage, can_review, check_resource_responsibilities, resource_views, reservation_view, visible_reservations
from silencio_set.resources.schemas import ResourceCreate, ResourcePatch, ResourceAuthorization, ResourceView, ReservationCreate, ReservationDecision, ReservationCancel
from silencio_set.resources.service import ResourceService
from silencio_set.tasks.repository import request_digest
from test_account_repository import fake_session_factory
from test_auth_api import auth_client
from test_crews import crew_client, ready_user

START = datetime(2026, 11, 1, tzinfo=UTC)
EPOCH = START - timedelta(days=5, microseconds=123456)
def hour(value): return START + timedelta(hours=value)


def test_peak_counts_simultaneous_use_instead_of_summing_every_intersecting_booking():
    rows = [(hour(1), hour(2), 2), (hour(2), hour(3), 2)]
    assert peak_quantity(rows, hour(1), hour(3)) == 2
    require_capacity(rows, hour(1), hour(3), 1, 3)
    with pytest.raises(AuthError) as caught: require_capacity(rows, hour(1), hour(3), 2, 3)
    assert caught.value.code == "RESOURCE_CAPACITY_CONFLICT"
    assert peak_quantity(rows, hour(3), hour(4)) == 0


def test_capacity_sweep_matches_independent_brute_force_across_random_boundaries():
    random = Random(37)
    for _ in range(200):
        rows = []
        for _ in range(20):
            left = random.randint(-3, 12); right = random.randint(left + 1, 16)
            rows.append((hour(left), hour(right), random.randint(1, 4)))
        expected = max(sum(q for left, right, q in rows if left <= hour(i / 2) < right) for i in range(2, 24))
        assert peak_quantity(rows, hour(1), hour(12)) == expected


@pytest.mark.parametrize("change", [{"resource_type": "consumable"}, {"name": "a\nb"}, {"name": " "},
    {"unit": "a" * 11}, {"total_quantity": True}, {"total_quantity": "1"}, {"total_quantity": 0},
    {"description": "x" * 2001}, {"crew_id": str(uuid4())}, {"status": "disabled"}])
def test_create_rejects_unimplemented_types_spoofing_and_invalid_fields(change):
    with pytest.raises(ValidationError): ResourceCreate(**({"name": "摄影机", "resource_type": "equipment",
        "unit": "台", "total_quantity": 1, "manager_member_id": uuid4()} | change))


@pytest.mark.parametrize("change", [{"quantity": True}, {"quantity": "1"}, {"starts_at": "2026-11-01T09:00:00"},
    {"ends_at": hour(1)}, {"ends_at": hour(1) + timedelta(days=32)}, {"starts_at": hour(1) + timedelta(microseconds=1)},
    {"scene_id": str(uuid4())}, {"applicant_member_id": str(uuid4())}, {"status": "confirmed"}])
def test_reservations_require_aware_whole_seconds_bounded_period_and_server_owned_identity(change):
    with pytest.raises(ValidationError): ReservationCreate(**({"resource_id": uuid4(), "starts_at": hour(1), "ends_at": hour(3), "quantity": 1} | change))


def test_patch_cannot_replace_manager_or_type_and_rejection_requires_a_real_reason():
    for changes in [{}, {"name": None}, {"manager_member_id": uuid4()}, {"resource_type": "prop"}]:
        with pytest.raises(ValidationError): ResourcePatch(expected_lock_version=1, **changes)
    assert ResourcePatch(expected_lock_version=1, description="  ").description is None
    with pytest.raises(ValidationError): ReservationDecision(expected_lock_version=1, decision="reject", decision_note=" ")
    with pytest.raises(ValidationError): ResourceAuthorization(expected_lock_version=1, manager_member_id=uuid4(), expected_manager_lock_version=1, grant_review_permission="true")


def setup_repository(monkeypatch, *, permissions=None, roles=None, scope=None):
    factory, session, transaction = fake_session_factory()
    person = CrewMember(id=uuid4(), crew_id=uuid4(), user_id=uuid4(), display_name="制片", status="active", joined_at=EPOCH, lock_version=1)
    actor = ResourceActor(person.id, frozenset(permissions if permissions is not None else {
        "resource.read", "resource.manage", "reservation.apply", "reservation.review", "member.authorize", "member.remove"}),
        frozenset(roles if roles is not None else {"producer", "admin"}), EPOCH)
    resource = Resource(id=uuid4(), crew_id=person.crew_id, name="摄影机", resource_type="equipment", unit="台",
        total_quantity=2, manager_member_id=person.id, created_by_member_id=person.id, status="available", description=None,
        lock_version=1, created_at=hour(0), updated_at=hour(0))
    booking = Reservation(id=uuid4(), crew_id=person.crew_id, resource_id=resource.id, applicant_member_id=person.id,
        applicant_membership_joined_at=EPOCH, starts_at=hour(1), ends_at=hour(3), quantity=1, status="pending",
        lock_version=1, created_at=hour(0), updated_at=hour(0))
    monkeypatch.setattr(module, "resource_actor", AsyncMock(return_value=actor))
    monkeypatch.setattr(module, "scope_codes", AsyncMock(return_value=set(scope if scope is not None else {"reservation.review"})))
    monkeypatch.setattr(module, "active_member", AsyncMock(return_value=person))
    monkeypatch.setattr(module, "permissions_for", AsyncMock(return_value={person.id: actor.permissions}))
    async def views(_session, rows, _actor):
        return [ResourceView(**{field: getattr(row, field) for field in ResourceView.model_fields if field not in {
            "manager_display_name", "manager_lock_version", "review_ready", "allowed_actions"}},
            manager_display_name=person.display_name, manager_lock_version=person.lock_version, review_ready=True, allowed_actions=["edit"]) for row in rows]
    monkeypatch.setattr(module, "resource_views", views)
    async def scalar(statement):
        entity = statement.column_descriptions[0].get("entity")
        if entity is Resource: return resource
        if entity is Reservation: return booking
        if entity is CrewMember: return person
        if entity is ResourceReceipt or entity is MemberPermissionOverride: return None
        return hour(0)
    session.scalar.side_effect = scalar
    session.scalars.return_value = Mock(all=Mock(return_value=[]))
    return ResourceRepository(factory), session, transaction, person, actor, resource, booking


def mutate(repo, person, action, body, **options):
    return asyncio.run(repo.mutate(person.user_id, person.crew_id, action, body, "a" * 32,
        **({"idempotency_key": str(uuid4())} if action != "edit" else {}), **options))


def test_creation_persists_resource_audit_receipt_and_delivery_intent_together_without_granting_permission(monkeypatch):
    repo, session, transaction, person, _, _, _ = setup_repository(monkeypatch)
    result = mutate(repo, person, "create", ResourceCreate(name="收音杆", resource_type="equipment", unit="支", total_quantity=1, manager_member_id=person.id))
    assert result.name == "收音杆" and result.lock_version == 1
    added = [call.args[0] for call in session.add.call_args_list]
    assert {type(row) for row in added} == {Resource, AuditLog, ResourceReceipt, ResourceChangeEvent}
    assert next(row for row in added if isinstance(row, ResourceReceipt)).membership_joined_at == EPOCH
    transaction.__aexit__.assert_awaited_once_with(None, None, None)
    assert module.resource_actor.call_args.kwargs["writing"] is True


@pytest.mark.parametrize("permissions,roles", [({"resource.read", "member.authorize"}, {"admin"}), ({"resource.read", "resource.manage"}, {"staff"}), ({"resource.read"}, {"producer"})])
def test_creation_does_not_turn_admin_or_arbitrary_capability_into_producer_authority(monkeypatch, permissions, roles):
    repo, session, _, person, _, _, _ = setup_repository(monkeypatch, permissions=permissions, roles=roles)
    with pytest.raises(AuthError) as caught: mutate(repo, person, "create", ResourceCreate(name="灯", resource_type="equipment", unit="盏", total_quantity=1, manager_member_id=person.id))
    assert caught.value.code == "FORBIDDEN"
    session.add.assert_not_called()


def test_management_and_review_require_distinct_capabilities_and_object_scope():
    actor = ResourceActor(uuid4(), frozenset({"resource.manage", "reservation.review"}), frozenset({"staff"}), EPOCH)
    resource = Resource(manager_member_id=actor.member_id)
    assert not can_manage(actor, set()) and can_manage(actor, {"resource.manage"})
    assert not can_review(resource, actor, set()) and can_review(resource, actor, {"reservation.review"})
    assert not can_review(Resource(manager_member_id=uuid4()), actor, {"reservation.review"})
    denied = ResourceActor(actor.member_id, effective_permissions(["producer"], [("resource.manage", "deny"), ("reservation.review", "deny")]), frozenset({"producer"}), EPOCH)
    assert not can_manage(denied, {"resource.manage"}) and not can_review(resource, denied, {"reservation.review"})


def test_apply_is_pending_and_does_not_reserve_capacity_before_approval(monkeypatch):
    repo, session, _, person, _, resource, _ = setup_repository(monkeypatch)
    result = mutate(repo, person, "apply", ReservationCreate(resource_id=resource.id, starts_at=hour(1), ends_at=hour(3), quantity=2))
    assert result.status == "pending" and result.reviewed_by is None
    session.scalars.assert_not_awaited()
    assert any(isinstance(call.args[0], ResourceChangeEvent) for call in session.add.call_args_list)


@pytest.mark.parametrize("change,code", [("disabled", "RESOURCE_UNAVAILABLE"), ("past", "INVALID_RESERVATION"), ("quantity", "INVALID_RESERVATION")])
def test_apply_checks_availability_future_start_and_total_quantity(monkeypatch, change, code):
    repo, session, _, person, _, resource, _ = setup_repository(monkeypatch)
    if change == "disabled": resource.status = "disabled"
    body = ReservationCreate(resource_id=resource.id, starts_at=hour(-1 if change == "past" else 1), ends_at=hour(3), quantity=3 if change == "quantity" else 1)
    with pytest.raises(AuthError) as caught: mutate(repo, person, "apply", body)
    assert caught.value.code == code
    session.add.assert_not_called()


def test_approval_rechecks_capacity_under_resource_then_reservation_lock_and_preserves_atomic_side_effects(monkeypatch):
    repo, session, transaction, person, _, resource, booking = setup_repository(monkeypatch)
    session.scalars.return_value = Mock(all=Mock(return_value=[Reservation(starts_at=hour(1), ends_at=hour(2), quantity=1), Reservation(starts_at=hour(2), ends_at=hour(3), quantity=1)]))
    result = mutate(repo, person, "decide", ReservationDecision(expected_lock_version=1, decision="accept"), reservation_id=booking.id)
    assert result.status == "confirmed" and result.lock_version == 2 and booking.reviewed_by == person.id
    sql = [str(call.args[0].compile(dialect=dialect())) for call in session.scalar.call_args_list]
    resource_lock = next(index for index, query in enumerate(sql) if 'FROM resources' in query and 'FOR UPDATE' in query)
    booking_lock = next(index for index, query in enumerate(sql) if 'FOR UPDATE OF resource_reservations' in query)
    assert resource_lock < booking_lock
    transaction.__aexit__.assert_awaited_once_with(None, None, None)
    assert {AuditLog, ResourceReceipt, ResourceChangeEvent} <= {type(call.args[0]) for call in session.add.call_args_list}


@pytest.mark.parametrize("change,code", [("scope", "FORBIDDEN"), ("owner", "FORBIDDEN"), ("permission", "FORBIDDEN"),
    ("capacity", "RESOURCE_CAPACITY_CONFLICT"), ("version", "VERSION_CONFLICT"), ("expired", "RESERVATION_EXPIRED"), ("epoch", "MEMBERSHIP_CHANGED")])
def test_approval_fails_closed_for_stale_authority_capacity_state_and_membership(monkeypatch, change, code):
    repo, session, _, person, actor, resource, booking = setup_repository(monkeypatch)
    if change == "scope": module.scope_codes.return_value = set()
    if change == "owner": resource.manager_member_id = uuid4()
    if change == "permission": module.resource_actor.return_value = ResourceActor(person.id, actor.permissions - {"reservation.review"}, actor.roles, EPOCH)
    if change == "capacity": session.scalars.return_value = Mock(all=Mock(return_value=[Reservation(starts_at=hour(1), ends_at=hour(3), quantity=2)]))
    if change == "version": booking.lock_version = 2
    if change == "expired": booking.ends_at = hour(-1)
    if change == "epoch": person.joined_at = EPOCH + timedelta(days=1)
    with pytest.raises(AuthError) as caught: mutate(repo, person, "decide", ReservationDecision(expected_lock_version=1, decision="accept"), reservation_id=booking.id)
    assert caught.value.code == code and booking.status == "pending"
    session.add.assert_not_called()


def test_rejection_can_close_expired_request_without_capacity_allocation(monkeypatch):
    repo, session, _, person, _, _, booking = setup_repository(monkeypatch)
    booking.ends_at = hour(-1)
    result = mutate(repo, person, "decide", ReservationDecision(expected_lock_version=1, decision="reject", decision_note="时段已经结束"), reservation_id=booking.id)
    assert result.status == "rejected" and result.decision_note == "时段已经结束"
    session.scalars.assert_not_awaited()


def test_cancellation_keeps_original_review_evidence_and_records_cancel_separately(monkeypatch):
    repo, _, _, person, _, _, booking = setup_repository(monkeypatch)
    booking.status = "confirmed"; booking.reviewed_by = uuid4(); booking.reviewed_at = hour(-1); booking.decision_note = "同意"
    previous = booking.reviewed_by
    result = mutate(repo, person, "cancel", ReservationCancel(expected_lock_version=1, reason="改期"), reservation_id=booking.id)
    assert result.status == "cancelled" and result.reviewed_by == previous and result.reviewed_at == hour(-1)
    assert result.cancelled_by == person.id and result.cancel_reason == "改期"
    assert result.dependency_check_status == "not_implemented"


@pytest.mark.parametrize("changes,code", [({"total_quantity": 1}, "RESOURCE_CAPACITY_CONFLICT"), ({"status": "disabled"}, "RESOURCE_IN_USE")])
def test_cannot_shrink_or_disable_resource_with_unfinished_confirmed_use(monkeypatch, changes, code):
    repo, session, _, person, _, resource, _ = setup_repository(monkeypatch)
    session.scalars.return_value = Mock(all=Mock(return_value=[Reservation(starts_at=hour(1), ends_at=hour(3), quantity=2)]))
    with pytest.raises(AuthError) as caught: mutate(repo, person, "edit", ResourcePatch(expected_lock_version=1, **changes), resource_id=resource.id)
    assert caught.value.code == code and resource.lock_version == 1
    session.add.assert_not_called()


def test_transfer_requires_explicit_admin_grant_and_audits_replacement_of_deny(monkeypatch):
    repo, session, _, person, _, resource, _ = setup_repository(monkeypatch)
    module.permissions_for.return_value = {person.id: frozenset({"resource.read"})}
    request = ResourceAuthorization(manager_member_id=person.id, expected_lock_version=1, expected_manager_lock_version=1)
    with pytest.raises(AuthError) as caught: mutate(repo, person, "authorize", request, resource_id=resource.id)
    assert caught.value.code == "MEMBER_NOT_ELIGIBLE"
    session.add.assert_not_called()
    old_scalar = session.scalar.side_effect
    override = MemberPermissionOverride(crew_id=person.crew_id, member_id=person.id, permission_code="reservation.review", effect="deny")
    async def scalar(statement):
        if statement.column_descriptions[0].get("entity") is MemberPermissionOverride: return override
        return await old_scalar(statement)
    session.scalar.side_effect = scalar
    request.grant_review_permission = True
    result = mutate(repo, person, "authorize", request, resource_id=resource.id)
    assert result.lock_version == 2 and person.lock_version == 2 and override.effect == "allow"
    rows = [call.args[0] for call in session.add.call_args_list]
    grant = next(row for row in rows if isinstance(row, ResourceScopeGrant))
    assert grant.membership_joined_at == EPOCH and grant.member_id == person.id
    assert len([row for row in rows if isinstance(row, AuditLog)]) == 2
    deletion = str(session.execute.call_args.args[0])
    assert 'resource_id =' in deletion and 'permission_code =' in deletion


def test_transfer_audit_and_event_preserve_both_old_and_new_manager_without_extra_global_grant(monkeypatch):
    repo, session, _, person, _, resource, _ = setup_repository(monkeypatch)
    old_manager = resource.manager_member_id = uuid4()
    result = mutate(repo, person, 'authorize', ResourceAuthorization(expected_lock_version=1,
        manager_member_id=person.id, expected_manager_lock_version=1), resource_id=resource.id)
    assert result.manager_member_id == person.id and person.lock_version == 1
    added = [call.args[0] for call in session.add.call_args_list]
    audit = next(row for row in added if isinstance(row, AuditLog))
    assert audit.before_summary['manager_member_id'] == str(old_manager)
    assert audit.after_summary['manager_member_id'] == str(person.id)
    assert {str(old_manager), str(person.id)} <= set(next(row for row in added if isinstance(row, ResourceChangeEvent)).recipient_member_ids)
    assert not any(isinstance(row, MemberPermissionOverride) for row in added)


@pytest.mark.parametrize("change,code", [("epoch", "MEMBERSHIP_CHANGED"), ("body", "IDEMPOTENCY_KEY_REUSED"), ("authority", "FORBIDDEN")])
def test_receipts_recheck_current_membership_payload_and_authority_before_replay(monkeypatch, change, code):
    repo, session, _, person, actor, resource, booking = setup_repository(monkeypatch)
    request = ReservationDecision(expected_lock_version=1, decision="accept")
    receipt = ResourceReceipt(resource_id=resource.id, reservation_id=booking.id, membership_joined_at=EPOCH,
        request_digest=request_digest("decide", booking.id, request), response_snapshot={})
    if change == "epoch": receipt.membership_joined_at -= timedelta(days=1)
    if change == "body": receipt.request_digest = 'f' * 64
    if change == "authority": module.scope_codes.return_value = set()
    old = session.scalar.side_effect
    async def scalar(statement):
        if statement.column_descriptions[0].get("entity") is ResourceReceipt: return receipt
        return await old(statement)
    session.scalar.side_effect = scalar
    with pytest.raises(AuthError) as caught: mutate(repo, person, "decide", request, reservation_id=booking.id)
    assert caught.value.code == code
    session.add.assert_not_called()


def test_receipt_returns_historical_success_without_stale_action_hints_or_duplicate_effects(monkeypatch):
    repo, session, _, person, actor, resource, booking = setup_repository(monkeypatch)
    request = ReservationDecision(expected_lock_version=1, decision="accept")
    booking.status = 'confirmed'; booking.lock_version = 2; booking.reviewed_by = person.id; booking.reviewed_at = hour(0)
    snapshot = reservation_view(booking, actor, True, hour(0)).model_dump(mode="json")
    booking.status = 'cancelled'; booking.lock_version = 3
    receipt = ResourceReceipt(resource_id=resource.id, reservation_id=booking.id, membership_joined_at=EPOCH,
        request_digest=request_digest("decide", booking.id, request), response_snapshot=snapshot)
    old = session.scalar.side_effect
    async def scalar(statement):
        if statement.column_descriptions[0].get("entity") is ResourceReceipt: return receipt
        return await old(statement)
    session.scalar.side_effect = scalar
    result = mutate(repo, person, "decide", request, reservation_id=booking.id)
    assert result.status == 'confirmed' and result.lock_version == 2 and result.allowed_actions == []
    session.add.assert_not_called(); session.flush.assert_not_awaited()


def test_private_reservations_query_limits_own_epoch_or_current_manager_scope():
    actor = ResourceActor(uuid4(), frozenset({"resource.read", "reservation.review"}), frozenset(), EPOCH)
    sql = str(visible_reservations(uuid4(), actor).compile(dialect=dialect()))
    assert 'applicant_membership_joined_at =' in sql and 'resource_scope_grants.membership_joined_at =' in sql
    assert 'resources.manager_member_id =' in sql and 'resource_scope_grants.permission_code =' in sql
    admin = ResourceActor(actor.member_id, frozenset({"resource.read", "member.authorize", "resource.manage"}), frozenset({"admin"}), EPOCH)
    compiled = str(visible_reservations(uuid4(), admin).compile(dialect=dialect()))
    assert 'applicant_membership_joined_at =' in compiled and 'EXISTS' not in compiled


def test_scope_epoch_does_not_restore_review_when_member_rejoins(monkeypatch):
    factory, session, _ = fake_session_factory()
    person = CrewMember(id=uuid4(), crew_id=uuid4(), display_name='摄影', status='active', joined_at=EPOCH, lock_version=1)
    row = Resource(id=uuid4(), crew_id=person.crew_id, name='灯', resource_type='equipment', unit='盏', total_quantity=1,
        status='available', description=None, manager_member_id=person.id, lock_version=1, created_at=hour(0), updated_at=hour(0))
    grant = ResourceScopeGrant(resource_id=row.id, member_id=person.id, permission_code='reservation.review', membership_joined_at=EPOCH - timedelta(days=1))
    session.scalars.side_effect = [Mock(all=Mock(return_value=[person])), Mock(all=Mock(return_value=[grant]))]
    monkeypatch.setattr(module, 'permissions_for', AsyncMock(return_value={person.id: frozenset({'reservation.review'})}))
    actor = ResourceActor(person.id, frozenset({'resource.read','reservation.review'}), frozenset(), EPOCH)
    result = asyncio.run(resource_views(session, [row], actor))[0]
    assert result.review_ready is False


def test_oversized_capacity_check_never_silently_truncates(monkeypatch):
    repo, session, _, _, _, resource, _ = setup_repository(monkeypatch)
    session.scalars.return_value = Mock(all=Mock(return_value=[object()] * 1001))
    with pytest.raises(AuthError) as caught: asyncio.run(module.capacity_rows(session, resource, hour(1), hour(3)))
    assert caught.value.code == 'CAPACITY_WINDOW_TOO_LARGE'


def test_exit_preview_paginates_visible_ownership_and_counts_hidden_private_bookings(monkeypatch):
    repo, session, _, person, _, resource, _ = setup_repository(monkeypatch)
    session.scalar.side_effect = [person, 3, 1]
    session.execute.return_value = Mock(mappings=Mock(return_value=Mock(all=Mock(return_value=[{
        'kind': 'resource', 'id': resource.id, 'resource_id': resource.id, 'title': resource.name,
        'status': 'available', 'lock_version': 1, 'ends_at': None}]))))
    result = asyncio.run(repo.responsibilities(person.user_id, person.crew_id, person.id, 1, 20))
    assert result.total == 1 and result.blocking_total == 3 and result.restricted_total == 2
    assert len(result.items) == 1 and result.items[0].kind == 'resource'
    compiled = str(session.execute.call_args.args[0].compile(dialect=dialect()))
    assert 'UNION ALL' in compiled and 'LIMIT' in compiled and 'applicant_membership_joined_at =' in compiled


def test_exit_preview_requires_remove_authority_for_other_members(monkeypatch):
    repo, session, _, person, _, _, _ = setup_repository(monkeypatch, permissions={'resource.read'})
    with pytest.raises(AuthError) as caught: asyncio.run(repo.responsibilities(person.user_id, person.crew_id, uuid4(), 1, 20))
    assert caught.value.code == 'FORBIDDEN'
    session.execute.assert_not_awaited()


@pytest.mark.parametrize('kind', ['resource', 'reservation', 'none'])
def test_exit_blocks_owned_resources_including_disabled_and_unfinished_bookings(kind):
    _, session, _ = fake_session_factory()
    session.scalar.side_effect = [uuid4()] if kind == 'resource' else [None, uuid4() if kind == 'reservation' else None]
    if kind == 'none': asyncio.run(check_resource_responsibilities(session, uuid4(), uuid4()))
    else:
        with pytest.raises(AuthError) as caught: asyncio.run(check_resource_responsibilities(session, uuid4(), uuid4()))
        assert caught.value.code == 'MEMBER_HAS_RESOURCE_RESPONSIBILITIES'


def test_commit_failure_cannot_return_success(monkeypatch):
    repo, _, transaction, person, _, resource, _ = setup_repository(monkeypatch)
    transaction.__aexit__.side_effect = OperationalError('private SQL', {}, Exception('commit lost'))
    with pytest.raises(OperationalError): mutate(repo, person, 'edit', ResourcePatch(expected_lock_version=1, name='新名称'), resource_id=resource.id)


def test_service_masks_database_errors_and_records_failure_without_private_sql():
    repository = Mock(mutate=AsyncMock(side_effect=OperationalError('secret SQL', {}, Exception('sensitive'))))
    audits = Mock(record_failure=AsyncMock())
    with pytest.raises(AuthError) as caught: asyncio.run(ResourceService(repository, audits).execute(ready_user(), 'edit', 'a' * 32, crew_id=uuid4(), body=ResourcePatch(expected_lock_version=1, name='灯')))
    assert caught.value.code == 'SERVICE_UNAVAILABLE' and 'secret' not in caught.value.public_message
    audits.record_failure.assert_awaited_once()


def configure_api(crew_client):
    client, audits, user, crew = crew_client
    repo = Mock(mutate=AsyncMock())
    client.app.state.resources = ResourceService(repo, audits)
    return client, repo, user, crew


@pytest.mark.parametrize('bad', ['missing', 'non-uuid', 'csrf', 'origin', 'profile'])
def test_resource_writes_require_uuid_key_session_profile_and_csrf(crew_client, bad):
    client, repo, user, crew = configure_api(crew_client)
    headers = {'Idempotency-Key': str(uuid4())}
    if bad == 'missing': headers = {}
    if bad == 'non-uuid': headers['Idempotency-Key'] = 'private-bad-key'
    if bad == 'csrf': headers['X-CSRF-Token'] = 'wrong'
    if bad == 'origin': headers['Origin'] = 'https://evil.example'
    if bad == 'profile': user.profile_completed_at = None; user.display_name = None
    response = client.post(f'/api/v1/crews/{crew.id}/resources', headers=headers, json={
        'name':'灯', 'resource_type':'equipment', 'unit':'盏', 'total_quantity':1, 'manager_member_id':str(crew.membership.id)})
    assert response.status_code in {403,422} and 'private-bad-key' not in response.text
    repo.mutate.assert_not_awaited()


def test_resource_api_denies_oversized_invalid_format_and_unbounded_queries(crew_client):
    client, repo, _, crew = configure_api(crew_client)
    url = f'/api/v1/crews/{crew.id}/resources'
    assert client.post(url, content='x' * 16385, headers={'Content-Type':'application/json'}).status_code == 413
    assert client.post(url, content='invalid', headers={'Content-Type':'text/plain'}).status_code == 415
    assert client.get(url + '?page_size=21').status_code == 422
    assert client.get(url + f'/{uuid4()}/availability', params={'range_start':'2026-01-01T00:00:00','range_end':'2026-01-02T00:00:00Z'}).status_code == 422
    repo.mutate.assert_not_awaited()


def test_frozen_migration_has_scoped_foreign_keys_unique_receipts_and_capacity_indexes(monkeypatch):
    monkeypatch.setattr(config_module, 'load_settings', lambda: Settings(_env_file=None, database_url='postgresql+asyncpg://user:example@localhost:5432/test'))
    output = StringIO(); command.upgrade(Config('alembic.ini', output_buffer=output), 'head', sql=True)
    sql = output.getvalue()
    assert '0009_resources' in sql and 'CREATE TABLE resource_scope_grants' in sql
    assert 'UNIQUE (crew_id, user_id, key_digest)' in sql and 'CREATE INDEX ix_reservations_capacity' in sql
    assert 'FOREIGN KEY(crew_id, manager_member_id) REFERENCES crew_members (crew_id, id)' in sql
    assert 'FOREIGN KEY(crew_id, reservation_id) REFERENCES resource_reservations (crew_id, id)' in sql
    assert 'applicant_membership_joined_at TIMESTAMP WITH TIME ZONE NOT NULL' in sql
