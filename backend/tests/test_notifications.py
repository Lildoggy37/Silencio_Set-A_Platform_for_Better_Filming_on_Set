import asyncio
from datetime import UTC, datetime, timedelta
from io import StringIO
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

from alembic import command
from alembic.config import Config
from pydantic import ValidationError
import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import OperationalError

from silencio_set.accounts.errors import AuthError
from silencio_set.core import config as config_module
from silencio_set.core.config import Settings
from silencio_set.crews.models import AuditLog, Crew, CrewMember
from silencio_set.crews.permissions import effective_permissions
from silencio_set.notifications import repository as module
from silencio_set.notifications.models import Notification, NotificationRecipient
from silencio_set.notifications.repository import NotificationConsumer, NotificationRepository, can_receive, notification_query, view
from silencio_set.notifications.schemas import AcknowledgeInput, NotificationPage
from silencio_set.notifications.service import NotificationService
from silencio_set.tasks.models import TaskChangeEvent
from silencio_set.tasks.repository import TaskActor
from test_account_repository import fake_session_factory
from test_auth_api import auth_client
from test_crews import crew_client, ready_user
from test_tasks import task_row


def notice_row(*, current=True, requires=True, confirmed=False):
    task = task_row(lock_version=1 if current else 2)
    now = datetime.now(UTC)
    event = TaskChangeEvent(id=uuid4(), crew_id=task.crew_id, task_id=task.id, task_version=1, kind="create", created_at=now)
    notice = Notification(id=uuid4(), crew_id=task.crew_id, source_event_id=event.id, event_type="task.create", title="新任务", summary="请核对任务", created_at=now)
    recipient = NotificationRecipient(crew_id=task.crew_id, notification_id=notice.id, member_id=task.owner_member_id,
        membership_joined_at=now - timedelta(days=1), requires_confirmation=requires,
        read_at=now if confirmed else None, confirmed_at=now if confirmed else None)
    return notice, recipient, event, task


def test_notification_scope_requires_receiver_epoch_current_task_visibility_and_permission():
    crew, member = uuid4(), uuid4()
    actor = TaskActor(member, effective_permissions([], []))
    compiled = notification_query(crew, actor).compile(dialect=postgresql.dialect())
    sql = str(compiled)
    assert "notification_recipients.member_id =" in sql and member in compiled.params.values()
    assert "notification_recipients.membership_joined_at = crew_members.joined_at" in sql
    assert "crew_members.status =" in sql and "tasks.id IN (SELECT tasks.id" in sql
    assert "task_collaborators" in sql and crew in compiled.params.values()
    with pytest.raises(AuthError): notification_query(crew, TaskActor(member, frozenset({"task.read"})))


@pytest.mark.parametrize("role,related,read_permission,expected", [
    ("director", False, True, True), ("staff", False, True, False), ("staff", True, True, True), ("staff", True, False, False),
])
def test_consumer_rechecks_current_role_object_relationship_and_notification_denial(role, related, read_permission, expected):
    task = task_row(); member_id = task.owner_member_id if related else uuid4()
    permissions = effective_permissions([role], [] if read_permission else [("notification.read_self", "deny")])
    assert can_receive(task, member_id, permissions, []) is expected


def consumer_fixture(monkeypatch):
    factory, session, transaction = fake_session_factory()
    task = task_row()
    now = datetime.now(UTC)
    member = CrewMember(id=task.owner_member_id, crew_id=task.crew_id, status="active", joined_at=now - timedelta(days=1))
    event = TaskChangeEvent(id=uuid4(), crew_id=task.crew_id, task_id=task.id, task_version=1, kind="create",
        actor_member_id=task.created_by_member_id, created_at=now, recipient_member_ids=[str(member.id), str(member.id)], dispatch_status="pending")
    session.execute.return_value = Mock(one_or_none=Mock(return_value=(event.id, task.crew_id)))
    session.scalar.side_effect = [Crew(id=task.crew_id), event, None, task]
    session.scalars.return_value = Mock(all=Mock(return_value=[member]))
    monkeypatch.setattr(module, "collaborators_for", AsyncMock(return_value={task.id: []}))
    monkeypatch.setattr(module, "permissions_for", AsyncMock(return_value={member.id: effective_permissions([], [])}))
    return factory, session, transaction, task, event, member


def test_consumer_commits_notice_receivers_and_event_marker_together(monkeypatch):
    factory, session, transaction, task, event, member = consumer_fixture(monkeypatch)
    assert asyncio.run(NotificationConsumer(factory).consume_once())
    added = [call.args[0] for call in session.add.call_args_list]
    notice = next(item for item in added if isinstance(item, Notification))
    receivers = [item for item in added if isinstance(item, NotificationRecipient)]
    assert len(receivers) == 1 and receivers[0].member_id == member.id and receivers[0].requires_confirmation
    assert receivers[0].membership_joined_at == member.joined_at
    assert notice.source_event_id == event.id and event.dispatch_status == "dispatched"
    assert "分镜" not in notice.summary
    transaction.__aexit__.assert_awaited_once_with(None, None, None)
    statements = [str(call.args[0].compile(dialect=postgresql.dialect())) for call in session.scalar.call_args_list]
    assert "crews" in statements[0] and "FOR UPDATE" in statements[0]
    assert "SKIP LOCKED" in statements[1]
    eligibility = str(session.scalars.call_args.args[0])
    assert "crew_members.joined_at <=" in eligibility and "users.status" in eligibility and "crew_members.status" in eligibility


def test_recovered_existing_notice_never_adds_receivers_or_resets_receipts(monkeypatch):
    factory, session, _, _, event, _ = consumer_fixture(monkeypatch)
    session.scalar.side_effect = [Crew(id=event.crew_id), event, uuid4()]
    assert asyncio.run(NotificationConsumer(factory).consume_once())
    assert event.dispatch_status == "dispatched"
    session.add.assert_not_called(); session.scalars.assert_not_awaited()


@pytest.mark.parametrize("payload", [["../invalid"], {}, [str(uuid4())] * 46, [None]])
def test_malformed_event_isolated_without_partial_delivery(monkeypatch, payload):
    factory, session, _, _, event, _ = consumer_fixture(monkeypatch)
    event.recipient_member_ids = payload
    session.scalar.side_effect = [Crew(id=event.crew_id), event]
    assert asyncio.run(NotificationConsumer(factory).consume_once())
    assert event.dispatch_status == "failed"
    session.add.assert_not_called()


def test_receiver_who_lost_current_access_is_not_delivered_a_notice(monkeypatch):
    factory, session, _, _, event, member = consumer_fixture(monkeypatch)
    monkeypatch.setattr(module, "permissions_for", AsyncMock(return_value={member.id: frozenset()}))
    assert asyncio.run(NotificationConsumer(factory).consume_once())
    assert event.dispatch_status == "dispatched"
    assert not any(isinstance(call.args[0], NotificationRecipient) for call in session.add.call_args_list)


def test_no_candidate_or_another_consumers_claim_has_no_side_effect(monkeypatch):
    factory, session, _, _, event, _ = consumer_fixture(monkeypatch)
    session.execute.return_value = Mock(one_or_none=Mock(return_value=None))
    assert not asyncio.run(NotificationConsumer(factory).consume_once())
    session.scalar.assert_not_awaited()
    session.execute.return_value = Mock(one_or_none=Mock(return_value=(event.id, event.crew_id)))
    session.scalar.side_effect = [Crew(id=event.crew_id), None]
    assert not asyncio.run(NotificationConsumer(factory).consume_once())
    session.add.assert_not_called()


def test_consumer_commit_failure_never_reports_success(monkeypatch):
    factory, _, transaction, _, _, _ = consumer_fixture(monkeypatch)
    transaction.__aexit__.side_effect = OperationalError("private SQL", {}, Exception("commit unknown"))
    with pytest.raises(OperationalError): asyncio.run(NotificationConsumer(factory).consume_once())


def mark_fixture(monkeypatch, **options):
    factory, session, transaction = fake_session_factory()
    row = notice_row(**options)
    actor = TaskActor(row[1].member_id, effective_permissions([], []))
    monkeypatch.setattr(module, "task_actor", AsyncMock(return_value=actor))
    session.execute.return_value = Mock(one_or_none=Mock(return_value=row))
    session.scalar.return_value = datetime.now(UTC)
    return factory, session, transaction, row


def test_read_is_idempotent_and_does_not_confirm_knowledge(monkeypatch):
    factory, session, _, row = mark_fixture(monkeypatch)
    repo = NotificationRepository(factory)
    args = (uuid4(), row[0].crew_id, row[0].id, "read", "a" * 32)
    first = asyncio.run(repo.mark(*args)); second = asyncio.run(repo.mark(*args))
    assert first.read_at == second.read_at and first.confirmed_at is None
    assert session.add.call_count == 1 and isinstance(session.add.call_args.args[0], AuditLog)
    assert "FOR UPDATE OF notification_recipients" in str(session.execute.call_args.args[0].compile(dialect=postgresql.dialect()))


def test_acknowledgement_binds_version_marks_read_and_preserves_prior_confirmation(monkeypatch):
    factory, session, _, row = mark_fixture(monkeypatch)
    repo = NotificationRepository(factory)
    args = (uuid4(), row[0].crew_id, row[0].id, "acknowledge", "a" * 32)
    first = asyncio.run(repo.mark(*args, object_version=1))
    assert first.read_at == first.confirmed_at
    row[3].lock_version = 2
    repeated = asyncio.run(repo.mark(*args, object_version=1))
    assert repeated.confirmed_at == first.confirmed_at and not repeated.is_current
    assert session.add.call_count == 1


@pytest.mark.parametrize("current,requires,version,code", [(False, True, 1, "NOTIFICATION_OUTDATED"),
    (True, False, 1, "CONFIRMATION_NOT_REQUIRED"), (True, True, 2, "VERSION_CONFLICT")])
def test_stale_or_unneeded_or_wrong_version_confirmation_is_rejected(monkeypatch, current, requires, version, code):
    factory, session, _, row = mark_fixture(monkeypatch, current=current, requires=requires)
    with pytest.raises(AuthError) as caught:
        asyncio.run(NotificationRepository(factory).mark(uuid4(), row[0].crew_id, row[0].id, "acknowledge", "a" * 32, object_version=version))
    assert caught.value.code == code and row[1].confirmed_at is None
    session.add.assert_not_called(); session.flush.assert_not_awaited()


def test_other_receiver_or_lost_access_uses_hidden_404(monkeypatch):
    factory, session, _, row = mark_fixture(monkeypatch)
    session.execute.return_value = Mock(one_or_none=Mock(return_value=None))
    with pytest.raises(AuthError) as caught:
        asyncio.run(NotificationRepository(factory).mark(uuid4(), row[0].crew_id, row[0].id, "read", "a" * 32))
    assert caught.value.status == 404
    session.add.assert_not_called()


def test_receipt_commit_failure_never_returns_success(monkeypatch):
    factory, _, transaction, row = mark_fixture(monkeypatch)
    transaction.__aexit__.side_effect = OperationalError("private SQL", {}, Exception("commit unknown"))
    with pytest.raises(OperationalError):
        asyncio.run(NotificationRepository(factory).mark(uuid4(), row[0].crew_id, row[0].id, "read", "a" * 32))


def configure_api(crew_client):
    client, audits, user, crew = crew_client
    row = notice_row(); row[0].crew_id = crew.id
    result = view(row)
    repo = Mock(list=AsyncMock(return_value=NotificationPage(items=[result], page=1, page_size=20, total=1)),
        get=AsyncMock(return_value=result), mark=AsyncMock(return_value=result))
    client.app.state.notifications = NotificationService(repo, audits)
    return client, repo, user, crew, row


def test_api_returns_only_current_receivers_notice_fields(crew_client):
    client, repo, user, crew, row = configure_api(crew_client)
    response = client.get(f"/api/v1/crews/{crew.id}/notifications")
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    assert repo.list.call_args.args[:2] == (user.id, crew.id)
    assert "member_id" not in response.text and "email" not in response.text


@pytest.mark.parametrize("header,value", [("Origin", "https://evil.example"), ("X-CSRF-Token", "wrong")])
def test_receipt_writes_require_csrf_and_origin(crew_client, header, value):
    client, repo, _, crew, row = configure_api(crew_client)
    response = client.post(f"/api/v1/crews/{crew.id}/notifications/{row[0].id}/read", json={}, headers={header: value})
    assert response.status_code == 403
    repo.mark.assert_not_awaited()


def test_api_never_accepts_a_client_receiver_or_coerced_version(crew_client):
    client, repo, _, crew, row = configure_api(crew_client)
    base = f"/api/v1/crews/{crew.id}/notifications/{row[0].id}"
    assert client.post(base + "/read", json={"member_id": str(uuid4())}).status_code == 422
    for version in (True, "1", 0):
        assert client.post(base + "/acknowledge", json={"object_version": version}).status_code == 422
    repo.mark.assert_not_awaited()


def test_notification_migration_has_same_crew_keys_unique_delivery_and_epoch(monkeypatch):
    settings = Settings(_env_file=None, database_url="postgresql+asyncpg://user:example@localhost:5432/test")
    monkeypatch.setattr(config_module, "load_settings", lambda: settings)
    output = StringIO(); command.upgrade(Config("alembic.ini", output_buffer=output), "head", sql=True)
    sql = output.getvalue()
    assert "0007_notifications" in sql and "CREATE TABLE notification_recipients" in sql
    assert "UNIQUE (source_event_id)" in sql and "membership_joined_at TIMESTAMP WITH TIME ZONE NOT NULL" in sql
    assert "FOREIGN KEY(crew_id, source_event_id) REFERENCES task_change_events (crew_id, id)" in sql
    assert "FOREIGN KEY(crew_id, member_id) REFERENCES crew_members (crew_id, id)" in sql
    assert "confirmed_at >= read_at" in sql
