import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

from fastapi.testclient import TestClient
from pydantic import ValidationError
import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import OperationalError

from silencio_set.accounts.errors import AuthError
from silencio_set.accounts.models import User
from silencio_set.crews.models import AuditLog, Crew, CrewMember, MemberRole
from silencio_set.crews.permissions import BASE_PERMISSIONS, PERMISSIONS, ROLE_NAMES, effective_permissions
from silencio_set.crews.repository import CrewRepository, visible_crews
from silencio_set.crews.schemas import CrewCreate, CrewPage, CrewView, MemberPage, MemberView
from silencio_set.crews.service import CrewService
from silencio_set.main import create_app
from test_account_repository import fake_session_factory
from test_auth_api import auth_client, bootstrap, register_user, login_user


def ready_user():
    return User(id=uuid4(), email="director@example.com", email_normalized="director@example.com", status="active",
                display_name="导演", profile_completed_at=datetime.now(UTC), lock_version=1, password_hash="not-real")


def view(user):
    return CrewView(id=uuid4(), name="学生剧组", project_name="雨停之前", description=None, created_at=datetime.now(UTC),
                    membership=MemberView(id=uuid4(), display_name=user.display_name, status="active", role_codes=["admin"], lock_version=1),
                    permissions=sorted(effective_permissions(["admin"], [])))


def test_admin_is_organization_authority_without_implicit_business_publish():
    permissions = effective_permissions(["admin"], [])
    assert "member.authorize" in permissions and "member.invite" in permissions
    assert "plan.publish" not in permissions and "script.read" not in permissions
    assert "plan.publish" in effective_permissions(["admin", "producer"], [])


def test_deny_wins_and_unknown_or_organization_overrides_cannot_grant_authority():
    permissions = effective_permissions(["producer"], [("plan.publish", "allow"), ("plan.publish", "deny"),
        ("member.authorize", "allow"), ("root.*", "allow")])
    assert "plan.publish" not in permissions and "member.authorize" not in permissions and "root.*" not in permissions
    assert "member.authorize" in effective_permissions(["admin"], [("member.authorize", "deny")])
    assert effective_permissions(["root"], []) == BASE_PERMISSIONS


@pytest.mark.parametrize("role", list(ROLE_NAMES))
def test_role_union_stays_inside_fixed_catalog(role):
    assert effective_permissions([role], []) <= PERMISSIONS


@pytest.mark.parametrize("field,value", [("name", " "), ("name", "a" * 51), ("name", "a\nb"),
    ("project_name", "🎬" * 101), ("project_name", "bad\0name"), ("description", "x" * 1001), ("description", "bad\0text")])
def test_creation_rejects_invalid_fields(field, value):
    with pytest.raises(ValidationError):
        CrewCreate(**({"name": "剧组", "project_name": "影片"} | {field: value}))


def test_names_are_unicode_lengths_and_never_imply_unique_crew_identity():
    item = CrewCreate(name="  " + "🎬" * 50 + "  ", project_name="  影片  ", description=" \n ")
    assert item.name == "🎬" * 50 and item.project_name == "影片" and item.description is None
    with pytest.raises(ValidationError):
        CrewCreate(name="组", project_name="片", created_by=str(uuid4()), role_codes=["director"])


def test_visibility_query_uses_active_membership_not_creator_provenance():
    user_id = uuid4()
    compiled = visible_crews(user_id).compile(dialect=postgresql.dialect())
    sql = str(compiled)
    assert "crew_members.user_id =" in sql and "crew_members.status =" in sql
    assert "member_permission_overrides.member_id = crew_members.id" in sql
    assert "member_permission_overrides.crew_id = crew_members.crew_id" in sql
    assert "crews.created_by =" not in sql
    assert user_id in compiled.params.values() and "active" in compiled.params.values()


def test_creation_and_success_audit_share_one_transaction_and_snapshot_nickname():
    factory, session, transaction = fake_session_factory()
    user = ready_user()
    session.scalar.return_value = user
    async def populated_flush():
        for call in session.add.call_args_list:
            if isinstance(call.args[0], Crew):
                call.args[0].created_at = datetime.now(UTC)
    session.flush.side_effect = populated_flush
    result = asyncio.run(CrewRepository(factory).create(user.id, CrewCreate(name="剧组", project_name="影片"), "a" * 32))
    crew, member, role, audit = [call.args[0] for call in session.add.call_args_list]
    assert isinstance(crew, Crew) and isinstance(member, CrewMember) and isinstance(role, MemberRole) and isinstance(audit, AuditLog)
    assert crew.id == member.crew_id == role.crew_id == audit.crew_id == result.id
    assert member.id == role.member_id == result.membership.id
    assert role.role_code == "admin" and member.display_name == "导演"
    assert audit.result == "success" and audit.actor_id == user.id
    assert "password" not in str(audit.after_summary)
    transaction.__aexit__.assert_awaited_once_with(None, None, None)
    assert "FOR UPDATE" in str(session.scalar.call_args.args[0])


def test_commit_failure_does_not_return_a_successful_crew():
    factory, session, transaction = fake_session_factory()
    user = ready_user(); session.scalar.return_value = user
    async def populate():
        for call in session.add.call_args_list:
            if isinstance(call.args[0], Crew): call.args[0].created_at = datetime.now(UTC)
    session.flush.side_effect = populate
    transaction.__aexit__.side_effect = OperationalError("private SQL", {}, Exception("commit unknown"))
    with pytest.raises(OperationalError):
        asyncio.run(CrewRepository(factory).create(user.id, CrewCreate(name="组", project_name="片"), "a" * 32))


def test_missing_or_inactive_membership_has_same_hidden_not_found():
    factory, session, _ = fake_session_factory()
    session.execute.return_value = Mock(one_or_none=Mock(return_value=None))
    for method in ("get", "members"):
        args = (uuid4(), uuid4()) + ((1, 20) if method == "members" else ())
        with pytest.raises(AuthError) as caught:
            asyncio.run(getattr(CrewRepository(factory), method)(*args))
        assert caught.value.status == 404 and caught.value.code == "NOT_FOUND"


def test_failed_creation_records_failure_separately_without_leaking_sql(caplog):
    repository = Mock()
    repository.create = AsyncMock(side_effect=OperationalError("private SQL", {}, Exception("private-db-password")))
    repository.record_failure = AsyncMock(side_effect=RuntimeError("private-audit-error"))
    with pytest.raises(AuthError) as caught:
        asyncio.run(CrewService(repository).execute(ready_user(), "crew.create", "a" * 32, body=CrewCreate(name="组", project_name="片")))
    assert caught.value.code == "SERVICE_UNAVAILABLE" and caught.value.status == 503
    assert "private" not in str(caught.value) + caplog.text
    repository.record_failure.assert_awaited_once()


@pytest.fixture
def crew_client(auth_client):
    client, auth = auth_client
    register_user(client, auth); login_user(client)
    response = client.patch("/api/v1/users/me/profile", json={"display_name": "导演", "expected_lock_version": 1})
    assert response.status_code == 200
    user = next(iter(auth.repository.users.values()))
    item = view(user)
    repository = Mock()
    repository.create = AsyncMock(return_value=item)
    repository.list = AsyncMock(return_value=CrewPage(items=[item], page=1, page_size=20, total=1))
    repository.get = AsyncMock(return_value=item)
    repository.members = AsyncMock(return_value=MemberPage(items=[item.membership], page=1, page_size=20, total=1))
    repository.record_failure = AsyncMock()
    client.app.state.crews = CrewService(repository)
    return client, repository, user, item


def test_http_create_forces_actor_and_initial_role_from_server_context(crew_client):
    client, repository, user, item = crew_client
    response = client.post("/api/v1/crews", json={"name": " 新剧组 ", "project_name": " 新电影 "})
    assert response.status_code == 201 and response.json()["data"]["id"] == str(item.id)
    args = repository.create.call_args.args
    assert args[0] == user.id and args[1].name == "新剧组"
    assert response.headers["cache-control"] == "no-store"
    assert "email" not in response.text and "password_hash" not in response.text
    spoofed = client.post("/api/v1/crews", json={"name": "组", "project_name": "片", "created_by": str(uuid4()), "role_codes": ["director"]})
    assert spoofed.status_code == 422
    assert repository.create.await_count == 1


@pytest.mark.parametrize("change", ["origin", "csrf"])
def test_crew_creation_requires_origin_and_csrf(crew_client, change):
    client, repository, _, _ = crew_client
    if change == "origin": client.headers["Origin"] = "https://evil.example"
    else: client.headers["X-CSRF-Token"] = "wrong"
    response = client.post("/api/v1/crews", json={"name": "组", "project_name": "片"})
    assert response.status_code == 403
    repository.create.assert_not_awaited()


def test_profile_gate_blocks_business_even_if_api_is_called_directly(crew_client):
    client, repository, user, _ = crew_client
    user.profile_completed_at = None; user.display_name = None
    assert client.get("/api/v1/crews").json()["error"]["code"] == "PROFILE_INCOMPLETE"
    assert client.post("/api/v1/crews", json={"name": "组", "project_name": "片"}).status_code == 403
    repository.list.assert_not_awaited(); repository.create.assert_not_awaited()


@pytest.mark.parametrize("suffix", ["?page=0", "?page_size=101", "?page_size=-1", "/not-a-uuid", "/00000000-0000-4000-8000-000000000000/members?status=removed"])
def test_pagination_ids_and_history_filter_are_bounded(crew_client, suffix):
    client, _, _, _ = crew_client
    assert client.get("/api/v1/crews" + suffix).status_code == 422


def test_list_and_directory_return_only_authorized_public_fields(crew_client):
    client, repository, user, item = crew_client
    response = client.get("/api/v1/crews")
    assert response.status_code == 200 and response.json()["data"]["total"] == 1
    assert repository.list.call_args.args == (user.id, 1, 20)
    response = client.get(f"/api/v1/crews/{item.id}/members")
    assert response.status_code == 200
    assert set(response.json()["data"]["items"][0]) == {"id", "display_name", "status", "role_codes", "lock_version"}
    assert repository.members.call_args.args == (user.id, item.id, 1, 20)


def test_http_hidden_404_is_audited_without_revealing_a_crew(crew_client):
    client, repository, user, _ = crew_client
    repository.get.side_effect = AuthError(404, "NOT_FOUND", "剧组不存在或无权访问")
    crew_id = uuid4()
    response = client.get(f"/api/v1/crews/{crew_id}")
    assert response.status_code == 404
    repository.record_failure.assert_awaited_once_with(user.id, "crew.read", crew_id, "denied", "NOT_FOUND", response.headers["x-request-id"])


def test_crew_body_limit_and_format_are_enforced_before_services(crew_client):
    client, repository, _, _ = crew_client
    assert client.post("/api/v1/crews", content="x" * 16_385, headers={"Content-Type": "application/json"}).status_code == 413
    assert client.post("/api/v1/crews", content="not-json", headers={"Content-Type": "text/plain"}).status_code == 415
    repository.create.assert_not_awaited()


def test_disabled_auth_does_not_open_crew_routes(settings):
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/v1/crews").status_code == 503
