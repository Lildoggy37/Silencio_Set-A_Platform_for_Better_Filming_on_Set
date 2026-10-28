import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import OperationalError

from silencio_set.accounts.errors import AuthError
from silencio_set.crews import organization
from silencio_set.crews.models import AuditLog, Crew
from silencio_set.crews.organization import MAX_VERSION, OrganizationRepository, organization_actor
from silencio_set.crews.schemas import MemberExitResult, MemberLeave, MemberRemove
from test_account_repository import fake_session_factory
from test_crews import crew_client, ready_user
from test_auth_api import auth_client
from test_member_roles import member


@pytest.mark.parametrize("body", [
    {"expected_lock_version": True}, {"expected_lock_version": "1"}, {"expected_lock_version": 0},
    {"expected_lock_version": MAX_VERSION + 1}, {"expected_lock_version": 1, "status": "left"},
    {"expected_lock_version": 1, "member_id": str(uuid4())},
])
def test_leave_accepts_only_strict_version_without_client_target(body):
    with pytest.raises(ValidationError):
        MemberLeave(**body)


@pytest.mark.parametrize("reason", ["", " ", "x" * 501, "private\ntext", "bad\0text", 123])
def test_remove_requires_bounded_plain_text_reason(reason):
    with pytest.raises(ValidationError):
        MemberRemove(expected_lock_version=1, reason=reason)


def exit_fixture(monkeypatch, *, removing=False, roles=()):
    factory, session, transaction = fake_session_factory()
    target = member()
    actor = member() if removing else target
    actor.crew_id = target.crew_id
    monkeypatch.setattr(organization, "organization_actor", AsyncMock(return_value=actor))
    clock = datetime.now(UTC)
    session.scalar.side_effect = ([target] if removing else []) + ([1] if "admin" in roles else []) + [clock]
    session.execute.side_effect = [Mock(all=Mock(return_value=[(target.id, code) for code in roles])), Mock(), Mock()]
    body = MemberRemove(expected_lock_version=1, reason="  工作调整  ") if removing else MemberLeave(expected_lock_version=1)
    return factory, session, transaction, actor, target, body, clock


@pytest.mark.parametrize("removing", [False, True])
def test_exit_retains_identity_revokes_grants_and_commits_audit_together(monkeypatch, removing):
    factory, session, transaction, actor, target, body, clock = exit_fixture(monkeypatch, removing=removing, roles=["admin", "producer"])
    result = asyncio.run(OrganizationRepository(factory).end_membership(actor.user_id, target.crew_id,
        target.id if removing else None, body, "a" * 32))
    assert result.membership.id == target.id and result.membership.status == ("removed" if removing else "left")
    assert result.membership.role_codes == [] and result.membership.lock_version == 2
    assert target.ended_at == result.ended_at == clock
    assert organization.organization_actor.call_args.args[-1] == ("member.remove" if removing else None)
    statements = [str(call.args[0].compile(dialect=postgresql.dialect())) for call in session.execute.call_args_list]
    for table in ("member_roles", "member_permission_overrides"):
        sql = next(sql for sql in statements if f"DELETE FROM {table}" in sql)
        assert "crew_id =" in sql and "member_id =" in sql
    assert not any("DELETE FROM crew_members" in sql for sql in statements)
    audit = session.add.call_args.args[0]
    assert isinstance(audit, AuditLog) and audit.target_id == target.id and audit.actor_id == actor.user_id
    assert audit.action == ("member.remove" if removing else "member.leave")
    assert audit.before_summary["role_codes"] == ["admin", "producer"]
    assert (audit.after_summary.get("reason") == "工作调整") is removing
    assert "reason" not in result.model_dump_json()
    transaction.__aexit__.assert_awaited_once_with(None, None, None)


def test_self_exit_requires_current_membership_but_no_read_or_admin_permission():
    _, session, _ = fake_session_factory()
    user = ready_user(); target = member(); target.user_id = user.id
    session.scalar.side_effect = [user, Crew(id=target.crew_id), target]
    assert asyncio.run(organization_actor(session, user.id, target.crew_id, None)) is target
    statements = [str(call.args[0].compile(dialect=postgresql.dialect())) for call in session.scalar.call_args_list]
    assert "FOR SHARE" in statements[0] and "FOR UPDATE" in statements[1] and "FOR UPDATE" in statements[2]
    assert "active" in session.scalar.call_args.args[0].compile().params.values()


def test_missing_current_membership_cannot_leave():
    _, session, _ = fake_session_factory()
    user = ready_user()
    session.scalar.side_effect = [user, Crew(id=uuid4()), None]
    with pytest.raises(AuthError) as caught:
        asyncio.run(organization_actor(session, user.id, uuid4(), None))
    assert caught.value.status == 404


@pytest.mark.parametrize("status,version,admins,code", [
    ("active", 1, 0, "LAST_ADMIN_REQUIRED"), ("active", 2, 1, "VERSION_CONFLICT"),
    ("left", 1, 1, "MEMBER_INACTIVE"), ("removed", 1, 1, "MEMBER_INACTIVE"),
    ("active", MAX_VERSION, 1, "VERSION_EXHAUSTED"),
])
def test_rejected_exit_never_changes_state_or_deletes_grants(monkeypatch, status, version, admins, code):
    factory, session, _, actor, target, body, _ = exit_fixture(monkeypatch, roles=["admin"])
    target.status = status; target.lock_version = version
    body.expected_lock_version = version if version == MAX_VERSION else 1
    session.scalar.side_effect = [admins]
    with pytest.raises(AuthError) as caught:
        asyncio.run(OrganizationRepository(factory).end_membership(actor.user_id, target.crew_id, None, body, "a" * 32))
    assert caught.value.code == code and target.status == status and target.lock_version == version
    assert session.execute.await_count == 1
    session.add.assert_not_called(); session.flush.assert_not_awaited()


def test_remove_cannot_target_self(monkeypatch):
    factory, session, _, actor, target, body, _ = exit_fixture(monkeypatch, removing=True)
    actor.id = target.id
    with pytest.raises(AuthError) as caught:
        asyncio.run(OrganizationRepository(factory).end_membership(actor.user_id, target.crew_id, target.id, body, "a" * 32))
    assert caught.value.code == "USE_LEAVE_ACTION"
    session.execute.assert_not_awaited(); session.add.assert_not_called()


def test_cross_crew_remove_target_is_hidden_and_query_is_scoped(monkeypatch):
    factory, session, _, actor, target, body, _ = exit_fixture(monkeypatch, removing=True)
    session.scalar.side_effect = [None]
    with pytest.raises(AuthError) as caught:
        asyncio.run(OrganizationRepository(factory).end_membership(actor.user_id, target.crew_id, target.id, body, "a" * 32))
    assert caught.value.status == 404
    compiled = session.scalar.call_args.args[0].compile(dialect=postgresql.dialect())
    assert target.crew_id in compiled.params.values() and target.id in compiled.params.values()
    assert "FOR UPDATE" in str(compiled)
    session.execute.assert_not_awaited()


@pytest.mark.parametrize("failure", [AuthError(409, "MEMBER_HAS_RESPONSIBILITIES", "请完成交接"), RuntimeError("check unavailable")])
def test_responsibility_check_blocks_or_fails_before_any_mutation(monkeypatch, failure):
    factory, session, _, actor, target, body, _ = exit_fixture(monkeypatch)
    check = AsyncMock(side_effect=failure)
    with pytest.raises(type(failure)):
        asyncio.run(OrganizationRepository(factory, exit_checks=[check]).end_membership(actor.user_id, target.crew_id, None, body, "a" * 32))
    check.assert_awaited_once_with(session, target.crew_id, target.id)
    assert target.status == "active" and target.lock_version == 1 and session.execute.await_count == 1
    session.add.assert_not_called(); session.flush.assert_not_awaited()


def test_commit_failure_does_not_return_success(monkeypatch):
    factory, _, transaction, actor, target, body, _ = exit_fixture(monkeypatch)
    transaction.__aexit__.side_effect = OperationalError("private SQL", {}, Exception("unknown commit"))
    with pytest.raises(OperationalError):
        asyncio.run(OrganizationRepository(factory).end_membership(actor.user_id, target.crew_id, None, body, "a" * 32))


def configure_exit(crew_client, *, removing=False):
    client, _, user, crew = crew_client
    view = crew.membership.model_copy(update={"status": "removed" if removing else "left", "role_codes": [], "lock_version": 2})
    repo = Mock(end_membership=AsyncMock(return_value=MemberExitResult(membership=view, ended_at=datetime.now(UTC))))
    client.app.state.crews.organization = repo
    url = f"/api/v1/crews/{crew.id}/members/" + (f"{crew.membership.id}/remove" if removing else "me/leave")
    return client, repo, user, crew, url


@pytest.mark.parametrize("removing", [False, True])
def test_http_exit_binds_actor_and_action_on_server_and_hides_private_fields(crew_client, removing):
    client, repo, user, crew, url = configure_exit(crew_client, removing=removing)
    response = client.post(url, json={"expected_lock_version": 1} | ({"reason": "工作调整"} if removing else {}))
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    assert repo.end_membership.call_args.args[:3] == (user.id, crew.id, crew.membership.id if removing else None)
    assert "reason" not in response.text and "email" not in response.text and "user_id" not in response.text


@pytest.mark.parametrize("header,value", [("Origin", "https://evil.example"), ("X-CSRF-Token", "bad")])
def test_exit_requires_origin_and_csrf(crew_client, header, value):
    client, repo, _, _, url = configure_exit(crew_client)
    client.headers[header] = value
    assert client.post(url, json={"expected_lock_version": 1}).status_code == 403
    repo.end_membership.assert_not_awaited()


def test_profile_and_spoofed_status_are_rejected_before_exit(crew_client):
    client, repo, user, _, url = configure_exit(crew_client)
    assert client.post(url, json={"expected_lock_version": 1, "status": "removed"}).status_code == 422
    user.profile_completed_at = None
    assert client.post(url, json={"expected_lock_version": 1}).status_code == 403
    repo.end_membership.assert_not_awaited()


def test_denied_remove_audits_member_target_without_reason_echo(crew_client):
    client, repo, _, crew, url = configure_exit(crew_client, removing=True)
    repo.end_membership.side_effect = AuthError(403, "FORBIDDEN", "没有移除权限")
    response = client.post(url, json={"expected_lock_version": 1, "reason": "private reason"})
    assert response.status_code == 403 and "private reason" not in response.text
    failure = client.app.state.crews.repository.record_failure.call_args
    assert failure.args[2] == crew.membership.id and failure.kwargs["target_type"] == "crew_member"
