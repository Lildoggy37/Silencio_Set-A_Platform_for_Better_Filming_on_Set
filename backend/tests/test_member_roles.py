import asyncio
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

from pydantic import ValidationError
import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import OperationalError

from silencio_set.accounts.errors import AuthError
from silencio_set.crews.models import AuditLog, Crew, CrewMember, MemberRole
from silencio_set.crews import organization
from silencio_set.crews.organization import MAX_VERSION, OrganizationRepository, check_roles_update, organization_actor, other_admins_query
from silencio_set.crews.schemas import MemberRolesUpdate
from test_account_repository import fake_session_factory
from test_crews import crew_client, ready_user
from test_auth_api import auth_client


def member(**changes):
    return CrewMember(id=uuid4(), crew_id=uuid4(), user_id=uuid4(), display_name="导演", status="active", lock_version=1, **changes)


@pytest.mark.parametrize("codes,version", [(["root"], 1), (["admin", "admin"], 1), (["producer", 12], 1),
    (["admin"], True), (["admin"], "1"), (["admin"], 0), (["admin"], MAX_VERSION + 1)])
def test_only_unique_catalog_roles_and_strict_versions_are_accepted(codes, version):
    with pytest.raises(ValidationError):
        MemberRolesUpdate(role_codes=codes, expected_lock_version=version)


def test_role_replacement_allows_multiple_or_zero_roles_but_no_client_actor():
    assert MemberRolesUpdate(role_codes=["producer", "admin"], expected_lock_version=1).role_codes == ["admin", "producer"]
    assert MemberRolesUpdate(role_codes=[], expected_lock_version=1).role_codes == []
    with pytest.raises(ValidationError):
        MemberRolesUpdate(role_codes=["admin"], expected_lock_version=1, actor_id=str(uuid4()))


def test_last_admin_cannot_be_removed_but_a_real_handover_can_proceed():
    target = member()
    body = MemberRolesUpdate(role_codes=["director"], expected_lock_version=1)
    with pytest.raises(AuthError) as caught:
        check_roles_update(target, ["admin"], body, 0)
    assert caught.value.code == "LAST_ADMIN_REQUIRED"
    check_roles_update(target, ["admin"], body, 1)
    check_roles_update(target, ["director"], body, 0)


@pytest.mark.parametrize("status,version,expected,code", [("left", 1, 1, "MEMBER_INACTIVE"),
    ("removed", 1, 1, "MEMBER_INACTIVE"), ("active", 2, 1, "VERSION_CONFLICT"),
    ("active", MAX_VERSION, MAX_VERSION, "VERSION_EXHAUSTED")])
def test_stale_inactive_or_exhausted_members_cannot_be_updated(status, version, expected, code):
    target = member(); target.status = status; target.lock_version = version
    with pytest.raises(AuthError) as caught:
        check_roles_update(target, [], MemberRolesUpdate(role_codes=[], expected_lock_version=expected), 0)
    assert caught.value.code == code


def test_admin_count_excludes_target_other_crews_inactive_members_and_disabled_accounts():
    crew_id, target_id = uuid4(), uuid4()
    compiled = other_admins_query(crew_id, target_id).compile(dialect=postgresql.dialect())
    sql = str(compiled)
    assert "crew_members.crew_id =" in sql and "crew_members.id !=" in sql
    assert "crew_members.status =" in sql and "users.status =" in sql
    assert "member_roles.crew_id = crew_members.crew_id" in sql
    assert crew_id in compiled.params.values() and target_id in compiled.params.values()
    assert "admin" in compiled.params.values() and list(compiled.params.values()).count("active") == 2


def test_authority_is_read_again_after_the_crew_lock_is_acquired():
    _, session, _ = fake_session_factory()
    user = ready_user(); target = member(); target.user_id = user.id
    session.scalar.side_effect = [user, Crew(id=target.crew_id), target]
    # Former admin was demoted by the transaction that held the coordinator.
    session.execute.side_effect = [Mock(all=Mock(return_value=[(target.id, "director")])), Mock(all=Mock(return_value=[]))]
    with pytest.raises(AuthError) as caught:
        asyncio.run(organization_actor(session, user.id, target.crew_id, "member.authorize"))
    assert caught.value.code == "FORBIDDEN"
    statements = [str(call.args[0].compile(dialect=postgresql.dialect())) for call in session.scalar.call_args_list]
    assert "FOR SHARE" in statements[0]
    assert "crews" in statements[1] and "FOR UPDATE" in statements[1]
    assert "crew_members" in statements[2] and "FOR UPDATE" in statements[2]


def test_creator_provenance_does_not_restore_admin_authority():
    _, session, _ = fake_session_factory()
    user = ready_user(); actor = member(); actor.user_id = user.id
    session.scalar.side_effect = [user, Crew(id=actor.crew_id, created_by=user.id), actor]
    session.execute.side_effect = [Mock(all=Mock(return_value=[])), Mock(all=Mock(return_value=[("member.authorize", "allow")]))]
    with pytest.raises(AuthError) as caught:
        asyncio.run(organization_actor(session, user.id, actor.crew_id, "member.authorize"))
    assert caught.value.status == 403


def test_role_replace_increments_shared_version_and_writes_diff_audit_in_one_transaction(monkeypatch):
    factory, session, transaction = fake_session_factory()
    target = member()
    monkeypatch.setattr(organization, "organization_actor", AsyncMock())
    session.scalar.return_value = target
    session.execute.side_effect = [Mock(all=Mock(return_value=[(target.id, "admin")])), Mock()]
    result = asyncio.run(OrganizationRepository(factory).replace_roles(target.user_id, target.crew_id, target.id,
        MemberRolesUpdate(role_codes=["admin", "producer"], expected_lock_version=1), "a" * 32))
    assert result.lock_version == 2 and result.role_codes == ["admin", "producer"]
    inserted = [call.args[0] for call in session.add.call_args_list]
    assert {row.role_code for row in inserted if isinstance(row, MemberRole)} == {"admin", "producer"}
    audit = next(row for row in inserted if isinstance(row, AuditLog))
    assert audit.crew_id == target.crew_id and audit.target_id == target.id
    assert audit.before_summary == {"role_codes": ["admin"], "lock_version": 1}
    assert audit.after_summary == {"role_codes": ["admin", "producer"], "lock_version": 2}
    transaction.__aexit__.assert_awaited_once_with(None, None, None)
    deletion = str(session.execute.call_args_list[-1].args[0].compile(dialect=postgresql.dialect()))
    assert "DELETE FROM member_roles" in deletion and "crew_id =" in deletion and "member_id =" in deletion


def test_last_admin_rejection_does_not_delete_roles_or_change_version(monkeypatch):
    factory, session, _ = fake_session_factory()
    target = member()
    monkeypatch.setattr(organization, "organization_actor", AsyncMock())
    session.scalar.side_effect = [target, 0]
    session.execute.return_value = Mock(all=Mock(return_value=[(target.id, "admin")]))
    with pytest.raises(AuthError) as caught:
        asyncio.run(OrganizationRepository(factory).replace_roles(target.user_id, target.crew_id, target.id,
            MemberRolesUpdate(role_codes=[], expected_lock_version=1), "a" * 32))
    assert caught.value.code == "LAST_ADMIN_REQUIRED" and target.lock_version == 1
    session.add.assert_not_called(); session.flush.assert_not_awaited()
    assert session.execute.await_count == 1


def test_cross_crew_target_is_hidden_and_not_modified(monkeypatch):
    factory, session, _ = fake_session_factory()
    monkeypatch.setattr(organization, "organization_actor", AsyncMock())
    session.scalar.return_value = None
    with pytest.raises(AuthError) as caught:
        asyncio.run(OrganizationRepository(factory).replace_roles(uuid4(), uuid4(), uuid4(),
            MemberRolesUpdate(role_codes=["producer"], expected_lock_version=1), "a" * 32))
    assert caught.value.status == 404
    session.execute.assert_not_awaited(); session.add.assert_not_called()


def test_role_commit_failure_does_not_return_success(monkeypatch):
    factory, session, transaction = fake_session_factory()
    target = member()
    monkeypatch.setattr(organization, "organization_actor", AsyncMock())
    session.scalar.return_value = target
    session.execute.side_effect = [Mock(all=Mock(return_value=[])), Mock()]
    transaction.__aexit__.side_effect = OperationalError("private SQL", {}, Exception("lost commit"))
    with pytest.raises(OperationalError):
        asyncio.run(OrganizationRepository(factory).replace_roles(target.user_id, target.crew_id, target.id,
            MemberRolesUpdate(role_codes=["director"], expected_lock_version=1), "a" * 32))


def configure_organization(crew_client):
    client, _, _, item = crew_client
    expected = item.membership.model_copy(update={"role_codes": ["admin", "producer"], "lock_version": 2})
    repo = Mock(replace_roles=AsyncMock(return_value=expected))
    client.app.state.crews.organization = repo
    return client, repo, item


def test_http_put_uses_authenticated_actor_and_body_version(crew_client):
    client, repo, item = configure_organization(crew_client)
    response = client.put(f"/api/v1/crews/{item.id}/members/{item.membership.id}/roles",
        json={"role_codes": ["producer", "admin"], "expected_lock_version": 1})
    assert response.status_code == 200 and response.json()["data"]["lock_version"] == 2
    assert repo.replace_roles.call_args.args[1:3] == (item.id, item.membership.id)
    assert repo.replace_roles.call_args.args[3].role_codes == ["admin", "producer"]
    assert response.headers["cache-control"] == "no-store"
    assert "email" not in response.text


@pytest.mark.parametrize("header,value", [("Origin", "https://evil.example"), ("X-CSRF-Token", "bad")])
def test_role_put_still_requires_origin_and_csrf(crew_client, header, value):
    client, repo, item = configure_organization(crew_client)
    client.headers[header] = value
    response = client.put(f"/api/v1/crews/{item.id}/members/{item.membership.id}/roles",
        json={"role_codes": ["producer"], "expected_lock_version": 1})
    assert response.status_code == 403
    repo.replace_roles.assert_not_awaited()


def test_denied_role_change_records_member_target_without_echoing_internal_error(crew_client):
    client, repo, item = configure_organization(crew_client)
    repo.replace_roles.side_effect = AuthError(409, "LAST_ADMIN_REQUIRED", "剧组至少需要一名有效管理员")
    response = client.put(f"/api/v1/crews/{item.id}/members/{item.membership.id}/roles",
        json={"role_codes": [], "expected_lock_version": 1})
    assert response.status_code == 409
    audit = client.app.state.crews.repository.record_failure.call_args
    assert audit.args[2] == item.membership.id and audit.kwargs["target_type"] == "crew_member"


def test_put_write_limits_are_not_classified_as_high_volume_reads(crew_client):
    client, repo, item = configure_organization(crew_client)
    url = f"/api/v1/crews/{item.id}/members/{item.membership.id}/roles"
    for _ in range(10):
        assert client.put(url, json={"role_codes": ["admin", "producer"], "expected_lock_version": 1}).status_code == 200
    response = client.put(url, json={"role_codes": ["admin", "producer"], "expected_lock_version": 1})
    assert response.status_code == 429 and repo.replace_roles.await_count == 10
