import asyncio
from datetime import timedelta
from io import StringIO
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
from silencio_set.crews.models import AuditLog, MemberPermissionOverride
from silencio_set.resources import custody as module
from silencio_set.resources.custody import CustodyRepository, checkout_view, require_operator
from silencio_set.resources.custody_models import Checkout, ResourceReturn
from silencio_set.resources.custody_schemas import CheckoutInput, ReturnInput, CustodyAuthorization
from silencio_set.resources.models import ResourceReceipt, ResourceScopeGrant, ResourceChangeEvent
from silencio_set.resources.repository import ResourceActor, reservation_blockers
from silencio_set.resources.schemas import ResourcePatch, ReservationCancel
from silencio_set.tasks.repository import request_digest
from test_resources import setup_repository, hour, EPOCH, mutate
from test_auth_api import auth_client
from test_crews import crew_client


def fixture(monkeypatch):
    repo, session, transaction, member, actor, resource, booking = setup_repository(monkeypatch)
    actor = ResourceActor(member.id, actor.permissions | {'resource.checkout', 'resource.return'}, actor.roles, EPOCH)
    module.resources.resource_actor.return_value = actor
    module.resources.scope_codes.return_value = {'resource.checkout', 'resource.return'}
    booking.starts_at, booking.ends_at, booking.quantity, booking.status = hour(-1), hour(3), 2, 'confirmed'
    loan = Checkout(id=uuid4(), crew_id=member.crew_id, resource_id=resource.id, reservation_id=booking.id,
        borrower_member_id=member.id, borrower_membership_joined_at=EPOCH, quantity=2, returned_quantity=0,
        checked_out_at=hour(-1), expected_return_at=hour(1), handled_by=member.id, lock_version=1)
    original = session.scalar.side_effect
    async def scalar(statement):
        if 'sum(' in str(statement): return 0
        entity = statement.column_descriptions[0].get('entity')
        if entity is Checkout: return loan
        return await original(statement)
    session.scalar.side_effect = scalar
    monkeypatch.setattr(module, 'outstanding', AsyncMock(return_value=0))
    monkeypatch.setattr(module, 'permissions_for', AsyncMock(return_value={member.id: actor.permissions}))
    return CustodyRepository(repo.sessions), session, transaction, member, actor, resource, booking, loan


def run(repo, member, action, body, **options):
    return asyncio.run(repo.mutate(member.user_id, member.crew_id, action, body, 'a'*32, idempotency_key=str(uuid4()), **options))


@pytest.mark.parametrize('value', [0, -1, True, '1', 1000001])
def test_amount_is_strict_positive_integer(value):
    with pytest.raises(ValidationError): ReturnInput(expected_lock_version=1, quantity=value)


def test_server_owns_actual_times_borrower_and_return_quantities():
    with pytest.raises(ValidationError): CheckoutInput(expected_lock_version=1, quantity=1, expected_return_at='2026-11-01T10:00:00')
    with pytest.raises(ValidationError): CheckoutInput(expected_lock_version=1, quantity=1, expected_return_at=hour(1), borrower_member_id=uuid4())
    with pytest.raises(ValidationError): ReturnInput(expected_lock_version=1, quantity=1, returned_at=hour(0))
    with pytest.raises(ValidationError): CustodyAuthorization(expected_lock_version=1, member_id=uuid4(), expected_member_lock_version=1, permission_codes=['reservation.review'])


def test_checkout_uses_reservation_without_double_allocating_and_commits_ledger_audit_receipt_event(monkeypatch):
    repo, session, transaction, member, _, resource, booking, _ = fixture(monkeypatch)
    result = run(repo, member, 'checkout', CheckoutInput(expected_lock_version=1, quantity=1, expected_return_at=hour(1)), reservation_id=booking.id)
    assert result.outstanding_quantity == 1 and result.borrower_member_id == booking.applicant_member_id
    assert booking.lock_version == 2 and resource.lock_version == 1
    rows = [call.args[0] for call in session.add.call_args_list]
    assert {Checkout, ResourceReceipt, AuditLog, ResourceChangeEvent} <= {type(row) for row in rows}
    transaction.__aexit__.assert_awaited_once_with(None, None, None)
    sql = [str(call.args[0].compile(dialect=dialect())) for call in session.scalar.call_args_list]
    assert next(i for i,q in enumerate(sql) if 'FROM resources' in q and 'FOR UPDATE' in q) < next(i for i,q in enumerate(sql) if 'FROM resource_reservations' in q and 'FOR UPDATE' in q)


@pytest.mark.parametrize('change,code', [('early','CHECKOUT_NOT_ALLOWED'), ('expired','CHECKOUT_NOT_ALLOWED'), ('disabled','CHECKOUT_NOT_ALLOWED'),
    ('pending','CHECKOUT_NOT_ALLOWED'), ('location','RESOURCE_TYPE_UNSUPPORTED'), ('return_time','INVALID_RETURN_TIME'), ('capacity','PHYSICAL_CAPACITY_CONFLICT'),
    ('scope','FORBIDDEN'), ('epoch','MEMBERSHIP_CHANGED'), ('version','VERSION_CONFLICT')])
def test_invalid_checkout_never_writes(monkeypatch, change, code):
    repo, session, _, member, _, resource, booking, _ = fixture(monkeypatch)
    if change == 'early': booking.starts_at = hour(1)
    if change == 'expired': booking.ends_at = hour(-1)
    if change == 'disabled': resource.status = 'disabled'
    if change == 'pending': booking.status = 'pending'
    if change == 'location': resource.resource_type = 'location'
    if change == 'capacity': module.outstanding.return_value = 2
    if change == 'scope': module.resources.scope_codes.return_value = set()
    if change == 'epoch': member.joined_at += timedelta(days=1)
    if change == 'version': booking.lock_version = 2
    with pytest.raises(AuthError) as caught:
        run(repo, member, 'checkout', CheckoutInput(expected_lock_version=1, quantity=1, expected_return_at=hour(4 if change=='return_time' else 1)), reservation_id=booking.id)
    assert caught.value.code == code
    session.add.assert_not_called()


def test_returned_items_do_not_reset_lifetime_reservation_checkout_quota(monkeypatch):
    repo, session, _, member, _, _, booking, _ = fixture(monkeypatch)
    old = session.scalar.side_effect
    async def scalar(statement):
        if 'sum(' in str(statement): return 2
        return await old(statement)
    session.scalar.side_effect = scalar
    with pytest.raises(AuthError) as caught: run(repo, member, 'checkout', CheckoutInput(expected_lock_version=1, quantity=1, expected_return_at=hour(1)), reservation_id=booking.id)
    assert caught.value.code == 'PHYSICAL_CAPACITY_CONFLICT'


def test_partial_then_complete_return_is_allowed_after_expiry_and_resource_disable(monkeypatch):
    repo, session, _, member, _, resource, booking, loan = fixture(monkeypatch)
    resource.status = 'disabled'; booking.ends_at = hour(-1)
    first = run(repo, member, 'return_resource', ReturnInput(expected_lock_version=1, quantity=1, note='第一批'), checkout_id=loan.id)
    assert first.outstanding_quantity == 1 and first.lock_version == 2
    final = run(repo, member, 'return_resource', ReturnInput(expected_lock_version=2, quantity=1), checkout_id=loan.id)
    assert final.outstanding_quantity == 0 and final.status == 'returned' and booking.lock_version == 3
    rows = [call.args[0] for call in session.add.call_args_list if isinstance(call.args[0], ResourceReturn)]
    assert len(rows) == 2 and sum(row.quantity for row in rows) == 2


def test_over_return_is_rejected_without_clamping(monkeypatch):
    repo, session, _, member, _, _, _, loan = fixture(monkeypatch)
    loan.returned_quantity = 1
    with pytest.raises(AuthError) as caught: run(repo, member, 'return_resource', ReturnInput(expected_lock_version=1, quantity=2), checkout_id=loan.id)
    assert caught.value.code == 'RETURN_QUANTITY_CONFLICT' and loan.returned_quantity == 1
    session.add.assert_not_called()


def test_expiry_changes_display_not_outstanding_property(monkeypatch):
    _, _, _, _, _, _, _, loan = fixture(monkeypatch)
    assert checkout_view(loan, hour(2)).status == 'overdue'
    assert checkout_view(loan, hour(2)).outstanding_quantity == 2
    query = str(reservation_blockers(loan.crew_id, loan.borrower_member_id).compile(dialect=dialect()))
    assert ' OR (EXISTS' in query and 'resource_checkouts.quantity > resource_checkouts.returned_quantity' in query


def test_existing_cancellation_and_resource_reduction_guard_unreturned_property(monkeypatch):
    repo, session, _, member, _, resource, booking = setup_repository(monkeypatch)
    module.resources.outstanding.return_value = 2
    with pytest.raises(AuthError) as caught: mutate(repo, member, 'cancel', ReservationCancel(expected_lock_version=1, reason='不拍了'), reservation_id=booking.id)
    assert caught.value.code == 'RESOURCE_NOT_RETURNED'
    with pytest.raises(AuthError) as caught: mutate(repo, member, 'edit', ResourcePatch(expected_lock_version=1, total_quantity=1), resource_id=resource.id)
    assert caught.value.code == 'PHYSICAL_CAPACITY_CONFLICT'
    session.add.assert_not_called()


def test_deny_and_object_scope_are_both_required_even_for_manager(monkeypatch):
    _, _, _, _, actor, _, _, _ = fixture(monkeypatch)
    denied = ResourceActor(actor.member_id, actor.permissions - {'resource.return'}, actor.roles, EPOCH)
    with pytest.raises(AuthError): require_operator(denied, {'resource.return'}, 'resource.return')
    with pytest.raises(AuthError): require_operator(actor, set(), 'resource.return')


def test_authorization_explicitly_grants_capability_and_can_revoke_resource_scope(monkeypatch):
    repo, session, _, member, _, resource, _, _ = fixture(monkeypatch)
    module.permissions_for.return_value = {member.id: frozenset()}
    body = CustodyAuthorization(expected_lock_version=1, member_id=member.id, expected_member_lock_version=1, permission_codes=['resource.return'])
    with pytest.raises(AuthError): run(repo, member, 'custody_authorize', body, resource_id=resource.id)
    session.add.assert_not_called()
    body.grant_capabilities = True
    result = run(repo, member, 'custody_authorize', body, resource_id=resource.id)
    assert result.member_lock_version == 2 and result.resource_lock_version == 2
    assert any(isinstance(call.args[0], MemberPermissionOverride) for call in session.add.call_args_list)
    result = run(repo, member, 'custody_authorize', CustodyAuthorization(expected_lock_version=2, member_id=member.id,
        expected_member_lock_version=2, permission_codes=[]), resource_id=resource.id)
    assert result.permission_codes == [] and member.lock_version == 2


def test_replay_retains_original_result_without_creating_another_return_and_rechecks_scope(monkeypatch):
    repo, session, _, member, _, resource, booking, loan = fixture(monkeypatch)
    body = ReturnInput(expected_lock_version=1, quantity=1)
    loan.returned_quantity = 1; loan.lock_version = 2
    snapshot = checkout_view(loan, hour(0)).model_dump(mode='json')
    receipt = ResourceReceipt(resource_id=resource.id, reservation_id=booking.id, membership_joined_at=EPOCH,
        request_digest=request_digest('return_resource', loan.id, body), response_snapshot=snapshot)
    old = session.scalar.side_effect
    async def scalar(statement):
        if statement.column_descriptions[0].get('entity') is ResourceReceipt: return receipt
        return await old(statement)
    session.scalar.side_effect = scalar
    result = run(repo, member, 'return_resource', body, checkout_id=loan.id)
    assert result.returned_quantity == 1
    session.add.assert_not_called()
    module.resources.scope_codes.return_value = set()
    with pytest.raises(AuthError): run(repo, member, 'return_resource', body, checkout_id=loan.id)


def test_commit_failure_does_not_report_return_success(monkeypatch):
    repo, _, transaction, member, _, _, _, loan = fixture(monkeypatch)
    transaction.__aexit__.side_effect = OperationalError('private SQL', {}, Exception('lost commit'))
    with pytest.raises(OperationalError): run(repo, member, 'return_resource', ReturnInput(expected_lock_version=1, quantity=1), checkout_id=loan.id)


def test_custody_routes_reject_missing_keys_spoofed_borrower_and_bad_csrf(crew_client):
    client, _, _, crew = crew_client
    client.app.state.resources = Mock()
    url = f'/api/v1/crews/{crew.id}/resource-reservations/{uuid4()}/checkouts'
    body = {'quantity':1, 'expected_lock_version':1, 'expected_return_at':hour(1).isoformat()}
    assert client.post(url, json=body).status_code == 422
    headers = {'Idempotency-Key':str(uuid4())}
    assert client.post(url, json=body | {'borrower_member_id':str(uuid4())}, headers=headers).status_code == 422
    assert client.post(url, json=body, headers=headers | {'X-CSRF-Token':'bad'}).status_code == 403


@pytest.mark.parametrize('change,code', [('epoch','MEMBERSHIP_CHANGED'), ('body','IDEMPOTENCY_KEY_REUSED')])
def test_receipt_rejects_old_membership_and_different_payload(monkeypatch, change, code):
    repo, session, _, member, _, resource, booking, loan = fixture(monkeypatch)
    body = ReturnInput(expected_lock_version=1, quantity=1)
    receipt = ResourceReceipt(resource_id=resource.id, reservation_id=booking.id,
        membership_joined_at=EPOCH - timedelta(days=1) if change=='epoch' else EPOCH,
        request_digest='f'*64 if change=='body' else request_digest('return_resource', loan.id, body))
    old = session.scalar.side_effect
    async def scalar(statement):
        if statement.column_descriptions[0].get('entity') is ResourceReceipt: return receipt
        return await old(statement)
    session.scalar.side_effect = scalar
    with pytest.raises(AuthError) as caught: run(repo, member, 'return_resource', body, checkout_id=loan.id)
    assert caught.value.code == code
    session.add.assert_not_called()


def test_custody_list_only_exposes_current_borrower_epoch_to_non_operators_including_admin(monkeypatch):
    repo, session, _, member, actor, resource, _, loan = fixture(monkeypatch)
    module.resources.scope_codes.return_value = set()
    old = session.scalar.side_effect
    async def scalar(statement):
        if 'count(' in str(statement): return 1
        return await old(statement)
    session.scalar.side_effect = scalar
    session.scalars.return_value = Mock(all=Mock(return_value=[loan]))
    result = asyncio.run(repo.list(member.user_id, member.crew_id, resource.id, 1, 20))
    assert result.total == 1 and result.items[0].id == loan.id
    query = str(session.scalars.call_args.args[0].compile(dialect=dialect()))
    assert 'borrower_member_id =' in query and 'borrower_membership_joined_at =' in query


def test_migration_adds_ledger_with_same_crew_keys_and_quantity_bounds(monkeypatch):
    monkeypatch.setattr(config_module, 'load_settings', lambda: Settings(_env_file=None, database_url='postgresql+asyncpg://user:example@localhost:5432/test'))
    output=StringIO(); command.upgrade(Config('alembic.ini',output_buffer=output), 'head', sql=True)
    sql=output.getvalue()
    assert '0010_custody' in sql and 'returned_quantity BETWEEN 0 AND quantity' in sql
    assert 'FOREIGN KEY(crew_id, checkout_id) REFERENCES resource_checkouts (crew_id, id)' in sql


def test_checkout_operator_can_read_confirmed_reservations_only_with_current_resource_scope():
    from silencio_set.resources.repository import visible_reservations
    actor = ResourceActor(uuid4(), frozenset({'resource.read', 'resource.checkout'}), frozenset(), EPOCH)
    query = visible_reservations(uuid4(), actor).compile(dialect=dialect())
    assert 'confirmed' in query.params.values() and 'resource.checkout' in query.params.values()
    assert 'resource_scope_grants.membership_joined_at =' in str(query)


def test_checkout_detail_hides_other_borrowers_after_scope_revocation(monkeypatch):
    repo, _, _, member, _, _, _, loan = fixture(monkeypatch)
    module.resources.scope_codes.return_value = set()
    loan.borrower_member_id = uuid4()
    with pytest.raises(AuthError) as caught: asyncio.run(repo.get(member.user_id, member.crew_id, loan.id))
    assert caught.value.code == 'NOT_FOUND'


def test_borrower_can_read_current_loan_detail_but_not_after_rejoin(monkeypatch):
    repo, _, _, member, _, _, _, loan = fixture(monkeypatch)
    module.resources.scope_codes.return_value = set()
    assert asyncio.run(repo.get(member.user_id, member.crew_id, loan.id)).id == loan.id
    loan.borrower_membership_joined_at -= timedelta(days=1)
    with pytest.raises(AuthError): asyncio.run(repo.get(member.user_id, member.crew_id, loan.id))
