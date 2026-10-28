import asyncio
from datetime import UTC, datetime, timedelta
from email import policy
from email.parser import BytesParser
from io import StringIO
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

from alembic import command
from alembic.config import Config
from cryptography.fernet import Fernet
from fakeredis.aioredis import FakeRedis
from pydantic import ValidationError
import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import OperationalError

from silencio_set.accounts.cache import AuthCache
from silencio_set.accounts.errors import AuthError
from silencio_set.core import config as config_module
from silencio_set.core.config import Settings
from silencio_set.crews import invitations
from silencio_set.crews.invitations import InvitationRepository, InvitationService, token_digest
from silencio_set.crews.invitation_schemas import InvitationAccept, InvitationCreate, InvitationCreated, InvitationPage, InvitationRevoke, InvitationView
from silencio_set.crews.models import AuditLog, Crew, CrewInvitation, CrewMember, MemberRole
from silencio_set.jobs.models import BackgroundJob, OutboxEvent
from silencio_set.jobs.repository import INVITATION_JOB, INVITATION_TASK, DispatchClaim, MailJobs
from silencio_set.jobs.dispatcher import CeleryPublisher
from silencio_set.mail.invitation_payloads import InvitationMail, InvitationPayloads
from silencio_set.mail.invitation_service import InvitationConsumer
from silencio_set.mail.payloads import PayloadUnavailable
from silencio_set.mail.smtp import SmtpAdapter
from test_account_repository import fake_session_factory
from test_auth_api import auth_client
from test_crews import crew_client, ready_user
from test_mail import smtp_server, MemoryMailJobs, FakeSmtp

TOKEN = 't' * 43


def invitation_for(user, **changes):
    now = datetime.now(UTC)
    fields = dict(id=uuid4(), crew_id=uuid4(), email_normalized=user.email_normalized, token_digest=token_digest(TOKEN),
        invited_by_member_id=uuid4(), status="pending", expires_at=now + timedelta(days=7), created_at=now,
        lock_version=1, accepted_by_user_id=None, accepted_at=None)
    return CrewInvitation(**(fields | changes))


def payload(cache):
    now = int(datetime.now(UTC).timestamp())
    return InvitationMail(payload_id=uuid4(), invitation_id=uuid4(), crew_id=uuid4(), crew_name="测试剧组",
        email="Director@example.com", email_normalized="director@example.com", token=TOKEN,
        email_digest=cache.digest_identity("email", "director@example.com"), expires_at=now + 60, invitation_expires_at=now + 604800)


def test_invitation_credentials_are_validated_without_revealing_them_in_repr():
    assert TOKEN not in repr(InvitationAccept(token=TOKEN))
    for token in ("short", "a" * 42 + "/", "你好" * 22):
        with pytest.raises(ValidationError): InvitationAccept(token=token)
    with pytest.raises(ValidationError): InvitationCreate(email="director@example.com", role_codes=["admin"])


def test_ciphertext_is_short_lived_bound_and_not_plaintext():
    async def run():
        cache = AuthCache(FakeRedis(), Settings(_env_file=None, auth_secret="test-secret-" * 4))
        store = InvitationPayloads(cache, Fernet.generate_key().decode())
        item = payload(cache)
        await store.save(item)
        raw = await cache.redis.get(store.key(item.payload_id))
        assert TOKEN.encode() not in raw and b"Director@example.com" not in raw
        assert 0 < await cache.redis.ttl(store.key(item.payload_id)) <= 60
        assert await store.read(item.payload_id) == item
        assert TOKEN not in repr(item)
        other = InvitationPayloads(cache, Fernet.generate_key().decode())
        with pytest.raises(PayloadUnavailable): await other.read(item.payload_id)
        await cache.redis.set(store.key(uuid4()), raw)
        await cache.redis.aclose()
    asyncio.run(run())


def test_ciphertext_rejects_wrong_reference_expiry_and_tampering():
    async def run():
        cache = AuthCache(FakeRedis(), Settings(_env_file=None, auth_secret="test-secret-" * 4))
        store = InvitationPayloads(cache, Fernet.generate_key().decode())
        item = payload(cache); await store.save(item)
        wrong = uuid4(); raw = await cache.redis.get(store.key(item.payload_id)); await cache.redis.set(store.key(wrong), raw)
        with pytest.raises(PayloadUnavailable): await store.read(wrong)
        await cache.redis.set(store.key(item.payload_id), '{"key_version":"v1","ciphertext":"tampered"}')
        with pytest.raises(PayloadUnavailable): await store.read(item.payload_id)
        with pytest.raises(PayloadUnavailable): await store.save(item.model_copy(update={"expires_at": 1}))
        with pytest.raises(PayloadUnavailable): await store.save(item.model_copy(update={"expires_at": int(datetime.now(UTC).timestamp()) + 901}))
        await cache.redis.aclose()
    asyncio.run(run())


def creation_session(monkeypatch):
    factory, session, transaction = fake_session_factory()
    user = ready_user(); actor = CrewMember(id=uuid4(), crew_id=uuid4(), user_id=user.id)
    monkeypatch.setattr(invitations, "organization_actor", AsyncMock(return_value=actor))
    session.scalar.side_effect = [datetime.now(UTC), None, None]
    session.get.return_value = Crew(id=actor.crew_id, name="测试剧组")
    store = Mock(save=AsyncMock(), cache=Mock(digest_identity=Mock(return_value="a" * 64)))
    return factory, session, transaction, user, actor, store


def test_invitation_job_outbox_and_audit_commit_together_without_raw_credentials(monkeypatch):
    factory, session, transaction, user, actor, store = creation_session(monkeypatch)
    result = asyncio.run(InvitationRepository(factory, store).create(user, actor.crew_id, InvitationCreate(email="invitee@example.com"), "a" * 32))
    rows = [call.args[0] for call in session.add.call_args_list]
    record, job, outbox, audit = rows
    assert isinstance(record, CrewInvitation) and isinstance(job, BackgroundJob) and isinstance(outbox, OutboxEvent) and isinstance(audit, AuditLog)
    raw = store.save.call_args.args[0]
    assert record.token_digest == token_digest(raw.token) and raw.token not in repr(record.__dict__) + repr(job.payload_ref) + repr(outbox.payload_ref)
    assert raw.token not in result.model_dump_json()
    assert set(job.payload_ref) == {"payload_id", "invitation_id", "email_digest"}
    assert job.id == outbox.job_id and job.crew_id == record.crew_id
    assert record.expires_at - record.created_at == timedelta(days=7)
    assert job.expires_at < record.expires_at
    transaction.__aexit__.assert_awaited_once_with(None, None, None)


def test_invitation_commit_loss_cannot_be_reported_as_accepted(monkeypatch):
    factory, session, transaction, user, actor, store = creation_session(monkeypatch)
    transaction.__aexit__.side_effect = OperationalError("sensitive SQL", {}, Exception("lost commit"))
    with pytest.raises(OperationalError):
        asyncio.run(InvitationRepository(factory, store).create(user, actor.crew_id, InvitationCreate(email="invitee@example.com"), "a" * 32))


def test_pending_duplicate_does_not_prepare_another_mail(monkeypatch):
    factory, session, _, user, actor, store = creation_session(monkeypatch)
    session.scalar.side_effect = [datetime.now(UTC), invitation_for(user)]
    with pytest.raises(AuthError) as caught:
        asyncio.run(InvitationRepository(factory, store).create(user, actor.crew_id, InvitationCreate(email=user.email), "a" * 32))
    assert caught.value.code == "INVITATION_PENDING"
    store.save.assert_not_awaited(); session.add.assert_not_called()


def accept_session(monkeypatch, status="pending", existing_status=None):
    factory, session, transaction = fake_session_factory()
    user = ready_user(); record = invitation_for(user, status=status)
    if status == "accepted": record.accepted_by_user_id = user.id; record.accepted_at = datetime.now(UTC)
    existing = None if existing_status is None else CrewMember(id=uuid4(), crew_id=record.crew_id, user_id=user.id,
        status=existing_status, lock_version=3, display_name="旧昵称", ended_at=datetime.now(UTC))
    session.scalar.side_effect = [user, record.crew_id, Crew(id=record.crew_id), record, datetime.now(UTC), existing]
    monkeypatch.setattr(invitations, "inviter_valid", AsyncMock(return_value=True))
    async def no_roles(_session, members): return {member.id: [] for member in members}
    monkeypatch.setattr(invitations, "role_map", no_roles)
    return factory, session, transaction, user, record, existing


@pytest.mark.parametrize("existing_status", [None, "left", "removed"])
def test_accept_creates_or_restores_one_member_without_old_roles(monkeypatch, existing_status):
    factory, session, transaction, user, record, existing = accept_session(monkeypatch, existing_status=existing_status)
    result = asyncio.run(InvitationRepository(factory).accept(user, InvitationAccept(token=TOKEN), "a" * 32))
    assert result.accepted and result.crew_id == record.crew_id and result.membership.role_codes == []
    if existing is not None:
        assert result.membership.id == existing.id and existing.status == "active" and existing.ended_at is None
        assert existing.lock_version == 4 and existing.display_name == user.display_name
        sql = " ".join(str(call.args[0]) for call in session.execute.call_args_list)
        assert "DELETE FROM member_roles" in sql and "DELETE FROM member_permission_overrides" in sql
    assert record.status == "accepted" and record.accepted_by_user_id == user.id
    assert TOKEN not in result.model_dump_json()
    transaction.__aexit__.assert_awaited_once_with(None, None, None)


def test_repeat_accept_recovers_only_an_existing_active_membership(monkeypatch):
    factory, session, _, user, record, existing = accept_session(monkeypatch, status="accepted", existing_status="active")
    result = asyncio.run(InvitationRepository(factory).accept(user, InvitationAccept(token=TOKEN), "a" * 32))
    assert result.membership.id == existing.id
    session.add.assert_not_called()
    factory, session, _, user, record, existing = accept_session(monkeypatch, status="accepted", existing_status="removed")
    with pytest.raises(AuthError): asyncio.run(InvitationRepository(factory).accept(user, InvitationAccept(token=TOKEN), "a" * 32))
    assert existing.status == "removed"


@pytest.mark.parametrize("case", ["expired", "wrong_email", "revoked", "inviter_revoked"])
def test_invalid_invites_cannot_create_members(monkeypatch, case):
    factory, session, _, user, record, _ = accept_session(monkeypatch)
    if case == "expired": record.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    if case == "wrong_email": record.email_normalized = "somebody@example.com"
    if case == "revoked": record.status = "revoked"
    if case == "inviter_revoked": monkeypatch.setattr(invitations, "inviter_valid", AsyncMock(return_value=False))
    with pytest.raises(AuthError) as caught:
        asyncio.run(InvitationRepository(factory).accept(user, InvitationAccept(token=TOKEN), "a" * 32))
    assert caught.value.code == "INVITATION_INVALID" and TOKEN not in str(caught.value)
    session.add.assert_not_called()


@pytest.mark.parametrize("version,status,code", [(0, "pending", "VERSION_CONFLICT"), (1, "accepted", "INVITATION_NOT_PENDING")])
def test_revoke_rechecks_version_and_state(monkeypatch, version, status, code):
    factory, session, _ = fake_session_factory(); user = ready_user(); record = invitation_for(user, status=status)
    monkeypatch.setattr(invitations, "organization_actor", AsyncMock())
    session.scalar.side_effect = [record, datetime.now(UTC)]
    body = Mock(expected_lock_version=version)
    with pytest.raises(AuthError) as caught:
        asyncio.run(InvitationRepository(factory).revoke(user, record.crew_id, record.id, body, "a" * 32))
    assert caught.value.code == code
    session.add.assert_not_called()


def test_revoke_stops_unstarted_mail_with_same_crew_scope(monkeypatch):
    factory, session, _, user, actor, _ = creation_session(monkeypatch)
    record = invitation_for(user, crew_id=actor.crew_id)
    session.scalar.side_effect = [record, datetime.now(UTC)]
    result = asyncio.run(InvitationRepository(factory).revoke(user, record.crew_id, record.id, InvitationRevoke(expected_lock_version=1), "a" * 32))
    assert result.status == "revoked" and result.lock_version == 2
    sql = " ".join(str(call.args[0].compile(dialect=postgresql.dialect())) for call in session.execute.call_args_list)
    assert "background_jobs.crew_id =" in sql and "background_jobs.idempotency_key =" in sql
    assert "background_jobs.status IN" in sql


def test_delivery_check_revalidates_pending_record_recipient_and_digest(monkeypatch):
    factory, session, _ = fake_session_factory()
    session.scalar.return_value = None
    cache = AuthCache(Mock(), Settings(_env_file=None, auth_secret="test-secret-" * 4))
    assert not asyncio.run(InvitationRepository(factory).delivery_current(payload(cache)))
    sql = str(session.scalar.call_args.args[0].compile(dialect=postgresql.dialect()))
    assert "token_digest =" in sql and "email_normalized =" in sql and "crew_id =" in sql and "expires_at > clock_timestamp()" in sql


@pytest.mark.parametrize("current,matching,sends", [(True, True, 1), (False, True, 0), (True, False, 0)])
def test_invitation_consumer_checks_binding_and_current_authority(current, matching, sends):
    async def run():
        cache = AuthCache(FakeRedis(), Settings(_env_file=None, auth_secret="test-secret-" * 4))
        store = InvitationPayloads(cache, Fernet.generate_key().decode()); item = payload(cache); await store.save(item)
        jobs = MemoryMailJobs(); job_id = uuid4()
        jobs.jobs[job_id] = {"state": "pending_dispatch", "token": None, "ref": {"payload_id": str(item.payload_id),
            "invitation_id": str(item.invitation_id if matching else uuid4()), "email_digest": item.email_digest}}
        smtp = FakeSmtp(); repo = Mock(delivery_current=AsyncMock(return_value=current))
        await InvitationConsumer(jobs, store, repo, smtp, 3).run(job_id, "test-worker")
        assert smtp.calls == sends
        await cache.redis.aclose()
    asyncio.run(run())


def test_invitation_smtp_uses_fixed_template_with_fragment_token(smtp_server):
    port, messages, _ = smtp_server
    cache = AuthCache(Mock(), Settings(_env_file=None, auth_secret="test-secret-" * 4))
    result = SmtpAdapter(Settings(_env_file=None, mail_port=port)).send(uuid4(), payload(cache))
    assert result.status == "accepted" and len(messages) == 1
    message = BytesParser(policy=policy.default).parsebytes(messages[0])
    text = message.get_body(preferencelist=("plain",)).get_content()
    assert message["Subject"] == "Silencio Set 剧组邀请"
    assert f"http://127.0.0.1:5173/#invitation={TOKEN}" in text and "?token=" not in text


def test_dispatch_routes_only_uuid_to_the_invitation_task():
    app = Mock(); claim = DispatchClaim(uuid4(), uuid4(), uuid4(), 1, INVITATION_TASK)
    CeleryPublisher(app).publish(claim)
    assert app.send_task.call_args.args[0] == INVITATION_TASK
    assert app.send_task.call_args.kwargs["args"] == [str(claim.job_id)]
    assert TOKEN not in repr(app.send_task.call_args)
    with pytest.raises(ValueError): CeleryPublisher(app).publish(DispatchClaim(uuid4(), uuid4(), uuid4(), 1, "untrusted.task"))


def test_invitation_job_cannot_be_claimed_by_verification_consumer():
    factory, session, _ = fake_session_factory(); session.scalar.return_value = None
    asyncio.run(MailJobs(factory, INVITATION_JOB).claim_job(uuid4(), "test-worker"))
    compiled = session.scalar.call_args.args[0].compile(dialect=postgresql.dialect())
    assert INVITATION_JOB in compiled.params.values()


@pytest.mark.parametrize("origin", ["https://example.com/path", "javascript:alert(1)", "http://remote.example", "https://u:p@example.com", "https://example.com/#secret"])
def test_mail_link_origin_rejects_paths_credentials_and_unsafe_origins(origin):
    with pytest.raises(ValidationError): Settings(_env_file=None, invitation_public_origin=origin)


def test_invitation_migration_has_one_pending_email_and_consistent_member_reference(monkeypatch):
    settings = Settings(_env_file=None, database_url="postgresql+asyncpg://test:example@localhost:5432/test")
    monkeypatch.setattr(config_module, "load_settings", lambda: settings)
    output = StringIO(); command.upgrade(Config("alembic.ini", output_buffer=output), "head", sql=True)
    sql = output.getvalue()
    assert "0005_invitations" in sql and "CREATE TABLE crew_invitations" in sql
    assert "CREATE UNIQUE INDEX uq_crew_invitations_pending_email" in sql and "WHERE status = 'pending'" in sql
    assert "FOREIGN KEY(crew_id, invited_by_member_id) REFERENCES crew_members (crew_id, id)" in sql


def configure_api(crew_client):
    client, audits, user, crew = crew_client
    now = datetime.now(UTC)
    row = InvitationView(id=uuid4(), email="invitee@example.com", status="pending", expires_at=now + timedelta(days=7), created_at=now, lock_version=1)
    repo = Mock(create=AsyncMock(return_value=InvitationCreated(invitation=row)),
        list=AsyncMock(return_value=InvitationPage(items=[row], page=1, page_size=20, total=1)))
    client.app.state.invitations = InvitationService(repo, audits)
    return client, repo, crew, row


def test_http_creation_is_202_and_never_returns_token_or_job_payload(crew_client):
    client, repo, crew, row = configure_api(crew_client)
    response = client.post(f"/api/v1/crews/{crew.id}/invitations", json={"email": row.email})
    assert response.status_code == 202 and response.json()["data"]["delivery_status"] == "pending_dispatch"
    assert TOKEN not in response.text and "payload_id" not in response.text
    assert response.headers["cache-control"] == "no-store"
    assert repo.create.call_args.args[1] == crew.id


def test_mail_disabled_does_not_create_invitation():
    repo = InvitationRepository(Mock())
    with pytest.raises(AuthError) as caught:
        asyncio.run(repo.create(ready_user(), uuid4(), InvitationCreate(email="test@example.com"), "a" * 32))
    assert caught.value.code == "EMAIL_SERVICE_UNAVAILABLE"


def test_accept_is_csrf_protected_and_request_errors_do_not_echo_token(crew_client):
    client, repo, crew, row = configure_api(crew_client)
    client.headers["X-CSRF-Token"] = "bad"
    response = client.post("/api/v1/invitations/accept", json={"token": TOKEN})
    assert response.status_code == 403 and TOKEN not in response.text
    assert client.post("/api/v1/invitations/accept", content="x" * 16385, headers={"Content-Type": "application/json"}).status_code == 413


def test_http_accept_passes_only_the_credential_and_authenticated_user_to_service(crew_client):
    from silencio_set.crews.invitation_schemas import InvitationJoined
    client, repo, crew, _ = configure_api(crew_client)
    repo.accept = AsyncMock(return_value=InvitationJoined(crew_id=crew.id, membership=crew.membership))
    response = client.post("/api/v1/invitations/accept", json={"token": TOKEN})
    assert response.status_code == 200 and response.json()["data"]["accepted"]
    assert repo.accept.call_args.args[0].id == next(iter(client.app.state.auth.repository.users.values())).id
    assert repo.accept.call_args.args[1].token.get_secret_value() == TOKEN
    assert TOKEN not in response.text


def test_http_list_does_not_return_credential_and_revoke_checks_version_contract(crew_client):
    client, repo, crew, row = configure_api(crew_client)
    response = client.get(f"/api/v1/crews/{crew.id}/invitations")
    assert response.status_code == 200 and set(response.json()["data"]["items"][0]) == {"id", "email", "status", "expires_at", "created_at", "lock_version"}
    repo.revoke = AsyncMock(return_value=row.model_copy(update={"status": "revoked", "lock_version": 2}))
    response = client.post(f"/api/v1/crews/{crew.id}/invitations/{row.id}/revoke", json={"expected_lock_version": 1})
    assert response.status_code == 200 and response.json()["data"]["status"] == "revoked"
    assert repo.revoke.call_args.args[2] == row.id


def test_inviter_without_current_organization_authority_cannot_deliver(monkeypatch):
    factory, session, _ = fake_session_factory(); user = ready_user(); record = invitation_for(user)
    actor = CrewMember(id=record.invited_by_member_id, crew_id=record.crew_id, status="active")
    session.scalar.return_value = actor
    monkeypatch.setattr(invitations, "role_map", AsyncMock(return_value={actor.id: ["director"]}))
    session.execute.return_value = Mock(all=Mock(return_value=[]))
    assert not asyncio.run(invitations.inviter_valid(session, record))


def test_expired_invitation_is_visible_as_expired_without_waiting_for_cleanup():
    user = ready_user(); record = invitation_for(user, expires_at=datetime.now(UTC) - timedelta(seconds=1))
    assert invitations.view(record, datetime.now(UTC)).status == "expired"


def test_failure_audit_cannot_leak_credentials_on_database_errors(caplog):
    repo = Mock(accept=AsyncMock(side_effect=OperationalError("private SQL", {}, Exception("private-db-detail"))))
    audits = Mock(record_failure=AsyncMock(side_effect=RuntimeError("private-audit-detail")))
    with pytest.raises(AuthError) as caught:
        asyncio.run(InvitationService(repo, audits).execute(ready_user(), "invitation.accept", "a" * 32, body=InvitationAccept(token=TOKEN)))
    assert caught.value.status == 503
    assert TOKEN not in caplog.text and "private" not in caplog.text
