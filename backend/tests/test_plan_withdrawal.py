import asyncio
from dataclasses import replace
from io import StringIO
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from pydantic import ValidationError
from sqlalchemy.exc import OperationalError
from sqlalchemy.dialects import postgresql

from silencio_set.accounts.errors import AuthError
from silencio_set.call_sheets import dependencies as sheets
from silencio_set.call_sheets.models import CallSheetVersion
from silencio_set.core import config as config_module
from silencio_set.core.config import Settings
from silencio_set.crews.models import AuditLog
from silencio_set.notifications import queries
from silencio_set.plans import withdrawal as module
from silencio_set.plans.models import PlanReceipt, PlanPublicationEvent
from silencio_set.plans.schemas import WithdrawInput
from test_plans import fixture as plan_fixture, ZERO, at
from test_publication_notifications import fixture as notice_fixture, consume, recipients
from test_auth_api import auth_client
from test_crews import crew_client


def fixture(monkeypatch):
    repo, session, tx, actor, plan, version, user = plan_fixture(monkeypatch)
    actor = replace(actor, permissions=actor.permissions | {'plan.publish'})
    monkeypatch.setattr(module, 'resource_actor', AsyncMock(return_value=actor))
    monkeypatch.setattr(module, 'withdraw_for_plan', AsyncMock(return_value=[uuid4()]))
    version.status = 'published'; version.published_at = ZERO
    person = dict(member_id=str(uuid4()), membership_joined_at=ZERO.isoformat(), display_name='陈同学')
    version.publication_snapshot = {'items':[{'members':[person]}]}
    plan.current_published_version_id = version.id
    return repo, session, tx, actor, plan, version, user


def withdraw(v, body=None):
    repo, _, _, _, plan, version, user = v
    return asyncio.run(module.withdraw(repo, user, plan.crew_id, version.id,
        body or WithdrawInput(expected_lock_version=1, reason='因场地关闭取消拍摄'), 'a'*32, 'same-key'))


def test_withdraw_clears_pointer_and_keeps_history_and_receipts_in_one_transaction(monkeypatch):
    v = fixture(monkeypatch)
    _, session, tx, actor, plan, version, _ = v
    original = version.publication_snapshot
    result = withdraw(v)
    assert plan.current_published_version_id is None and version.status == 'published'
    assert version.withdrawn_by == actor.member_id and version.withdrawn_at == ZERO and version.lock_version == 2
    assert version.publication_snapshot is original and result.notification_status == 'pending_dispatch'
    saved = [call.args[0] for call in session.add.call_args_list]
    assert {type(row) for row in saved} == {PlanPublicationEvent, PlanReceipt, AuditLog}
    event = next(row for row in saved if isinstance(row, PlanPublicationEvent))
    assert event.kind == 'withdrawn' and len(event.recipient_snapshots) == 1
    module.withdraw_for_plan.assert_awaited_once_with(session, plan.crew_id, plan.id, ZERO)
    session.execute.assert_not_awaited()
    tx.__aexit__.assert_awaited_once_with(None, None, None)


@pytest.mark.parametrize('change,code', [('draft','CURRENT_VERSION_CHANGED'), ('historical','CURRENT_VERSION_CHANGED'), ('withdrawn','CURRENT_VERSION_CHANGED'), ('lock','VERSION_CONFLICT'), ('permission','FORBIDDEN')])
def test_invalid_withdrawal_has_no_partial_cancellation(monkeypatch, change, code):
    v = fixture(monkeypatch)
    if change == 'draft': v[5].status = 'draft'
    elif change == 'historical': v[4].current_published_version_id = uuid4()
    elif change == 'withdrawn': v[5].withdrawn_at = ZERO
    elif change == 'lock': v[5].lock_version = 2
    else: monkeypatch.setattr(module, 'resource_actor', AsyncMock(return_value=replace(v[3], permissions=v[3].permissions - {'plan.publish'})))
    before = v[4].current_published_version_id
    with pytest.raises(AuthError) as caught: withdraw(v)
    assert caught.value.code == code and v[4].current_published_version_id == before
    v[1].add.assert_not_called(); module.withdraw_for_plan.assert_not_awaited()


def test_committed_withdrawal_replays_original_result_without_reemitting_events(monkeypatch):
    v = fixture(monkeypatch); first = withdraw(v)
    receipt = next(call.args[0] for call in v[1].add.call_args_list if isinstance(call.args[0], PlanReceipt))
    v[1].scalar.side_effect = lambda q: receipt
    v[1].add.reset_mock(); module.withdraw_for_plan.reset_mock()
    assert withdraw(v) == first
    v[1].add.assert_not_called(); module.withdraw_for_plan.assert_not_awaited()
    with pytest.raises(AuthError) as caught:
        withdraw(v, WithdrawInput(expected_lock_version=1, reason='另一个原因'))
    assert caught.value.code == 'IDEMPOTENCY_KEY_REUSED'


def test_withdrawal_commit_failure_is_not_reported_as_success(monkeypatch):
    v = fixture(monkeypatch)
    v[2].__aexit__.side_effect = OperationalError('private SQL', {}, Exception('commit failed'))
    with pytest.raises(OperationalError): withdraw(v)


@pytest.mark.parametrize('reason', ['', ' ', 'x'*501, 42])
def test_withdraw_reason_is_required_bounded_text(reason):
    with pytest.raises(ValidationError): WithdrawInput(expected_lock_version=1, reason=reason)


def test_withdrawal_invalidates_current_call_sheets_and_keeps_arrangements(monkeypatch):
    v = fixture(monkeypatch)
    version = CallSheetVersion(id=uuid4(), lock_version=4, validity_status='review_required', arrangement_snapshot={'sessions':[]})
    v[1].scalars.return_value = Mock(all=Mock(return_value=[version]))
    monkeypatch.setattr(sheets, 'event', AsyncMock())
    result = asyncio.run(sheets.withdraw_for_plan(v[1], v[4].crew_id, v[4].id, ZERO))
    assert result == [version.id] and version.validity_status == 'withdrawn' and version.lock_version == 5
    assert version.arrangement_snapshot == {'sessions':[]}
    sheets.event.assert_awaited_once_with(v[1], version, 'withdrawn', ZERO)
    sql = str(v[1].scalars.call_args.args[0])
    assert 'current_published_version_id' in sql and 'validity_status !=' in sql


def test_cancellation_notice_never_requires_confirmation(monkeypatch):
    v = notice_fixture(monkeypatch)
    v.event.kind = 'withdrawn'
    assert consume(v)
    assert recipients(v) and all(not row.requires_confirmation for row in recipients(v))
    assert v.session.add.call_args.args[0].event_type == 'plan.withdrawn'


def test_source_query_marks_null_pointer_and_withdrawal_without_claiming_current():
    from types import SimpleNamespace as NS
    actor = NS(member_id=uuid4(), permissions={'notification.read_self','plan.read_related'})
    sql = str(queries.notification_query(uuid4(), actor).compile(dialect=postgresql.dialect(), compile_kwargs={'literal_binds':True}))
    assert 'IS DISTINCT FROM shooting_plan_versions.id' in sql
    assert 'withdrawn_at IS NOT NULL' in sql and "plan_publication_events.kind = 'published'" in sql


def test_migration_preserves_original_events_and_allows_one_cancellation_event(monkeypatch):
    monkeypatch.setattr(config_module, 'load_settings', lambda: Settings(_env_file=None, database_url='postgresql+asyncpg://user:example@localhost:5432/test'))
    out = StringIO(); command.upgrade(Config('alembic.ini', output_buffer=out), 'head', sql=True)
    sql = out.getvalue()
    assert '0022_plan_withdrawal' in sql and 'DROP CONSTRAINT uq_plan_publication_event' in sql
    assert 'UNIQUE (plan_version_id, kind)' in sql and "DEFAULT 'published' NOT NULL" in sql
    assert 'DELETE FROM' not in sql


def test_withdraw_http_keeps_authentication_csrf_and_uuid_key(crew_client):
    client, _, _, crew = crew_client
    service = Mock(execute=AsyncMock(return_value=dict(withdrawn_version_id=str(uuid4()), lock_version=2,
        withdrawn_at=ZERO.isoformat(), withdrawal_reason='取消', affected_call_sheet_version_ids=[])))
    client.app.state.plans = service
    path = f'/api/v1/crews/{crew.id}/plan-versions/{uuid4()}/withdraw'
    body = dict(expected_lock_version=1, reason='取消')
    assert client.post(path, json=body).status_code == 422
    headers = {'Idempotency-Key':str(uuid4())}
    assert client.post(path, json=body, headers=headers).status_code == 200
    assert service.execute.call_args.args[1] == 'withdraw'
    client.headers.pop('X-CSRF-Token', None)
    assert client.post(path, json=body, headers=headers).status_code == 403
