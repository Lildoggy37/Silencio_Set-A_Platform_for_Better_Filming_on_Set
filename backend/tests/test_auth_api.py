from datetime import UTC, datetime
import hashlib
import hmac
from uuid import uuid4

from fakeredis.aioredis import FakeRedis
from fastapi.testclient import TestClient
from fastapi import Depends
import pytest
from sqlalchemy.exc import OperationalError

from silencio_set.accounts.cache import AuthCache
from silencio_set.accounts.errors import AuthError
from silencio_set.accounts.models import User
from silencio_set.accounts.passwords import PasswordCheck
from silencio_set.accounts.profile import ProfileVersionConflict
from silencio_set.accounts.repository import RegistrationOutcome
from silencio_set.accounts.schemas import ProfileView
from silencio_set.accounts.service import AuthService
from silencio_set.api.accounts import SESSION_COOKIE
from silencio_set.api.accounts import completed_user
from silencio_set.core.config import Settings
from silencio_set.main import create_app

ORIGIN = "http://127.0.0.1:5173"
PASSWORD = "correct horse sample battery"


class StubPasswords:
    """Test-only fast double. Real Argon2id is exercised in test_passwords.py."""
    def __init__(self):
        self.hash_calls = 0

    def hash(self, password):
        self.hash_calls += 1
        return hashlib.sha256(password.encode()).hexdigest()

    def verify(self, stored, password):
        return PasswordCheck(hmac.compare_digest(stored, hashlib.sha256(password.encode()).hexdigest()))


class MemoryAccounts:
    """Orchestration double, never imported or selected by the application."""
    def __init__(self):
        self.users = {}
        self.receipts = {}
        self.fail_before_commit = False
        self.lose_commit_response = False

    async def registration(self, digest):
        record = self.receipts.get(digest)
        if record is None:
            return None
        if record[1] <= datetime.now(UTC).timestamp():
            raise AuthError(400, "REGISTRATION_TOKEN_INVALID", "expired")
        return RegistrationOutcome(record[0], created=False)

    async def create_registration(self, digest, grant, hashed):
        if self.fail_before_commit:
            raise OperationalError("sensitive SQL", {}, Exception("sensitive password"))
        previous = await self.registration(digest)
        if previous:
            return previous
        if grant.email_normalized in self.users:
            raise AuthError(409, "REGISTRATION_NOT_COMPLETED", "unable to register")
        user_id = uuid4()
        user = User(id=user_id, email=grant.email, email_normalized=grant.email_normalized, password_hash=hashed,
                    status="active", lock_version=1, display_name=None, profile_completed_at=None,
                    email_verified_at=datetime.now(UTC))
        self.users[grant.email_normalized] = user
        self.receipts[digest] = (user_id, grant.expires_at)
        if self.lose_commit_response:
            self.lose_commit_response = False
            raise OperationalError("commit response lost", {}, Exception("test failure"))
        return RegistrationOutcome(user_id, created=True)

    async def login_user(self, normalized):
        return self.users.get(normalized)

    async def user(self, user_id):
        return next((u for u in self.users.values() if u.id == user_id and u.status == "active"), None)

    async def upgrade_hash(self, *args):
        raise AssertionError("Stub hashes do not request upgrades")

    async def profile(self, user_id, changes):
        user = await self.user(user_id)
        if user.lock_version != changes.expected_lock_version:
            raise ProfileVersionConflict()
        user.display_name = changes.display_name
        user.profile_completed_at = user.profile_completed_at or datetime.now(UTC)
        user.lock_version += 1
        return ProfileView.model_validate(user)


@pytest.fixture
def auth_client(settings):
    # The running application has no memory mode. Inject only within this test.
    app = create_app(settings)
    with TestClient(app) as client:
        redis = FakeRedis()
        configured = Settings(_env_file=None, app_env="test", auth_secret="test-only-not-real-secret-" * 2)
        service = AuthService(AuthCache(redis, configured), MemoryAccounts(), StubPasswords())
        app.state.auth = service
        client.headers["Origin"] = ORIGIN
        yield client, service
        client.portal.call(redis.aclose)


def bootstrap(client):
    response = client.get("/api/v1/auth/csrf")
    assert response.status_code == 200
    client.headers["X-CSRF-Token"] = response.json()["data"]["csrf_token"]
    return response


def registration_token(client, service, email="Director@example.com"):
    bootstrap(client)
    issued = client.portal.call(service.cache.issue_verification, email, "unit-issuer")
    verified = client.post("/api/v1/auth/email-verifications/verify", json={
        "email": email, "verification_id": str(issued.verification_id), "code": issued.code.get_secret_value(),
    })
    assert verified.status_code == 200
    return verified.json()["data"]["registration_token"]


def register_user(client, service):
    token = registration_token(client, service)
    response = client.post("/api/v1/auth/register", json={"registration_token": token, "password": PASSWORD})
    assert response.status_code == 201
    return token, response


def login_user(client):
    response = client.post("/api/v1/auth/login", json={"email": "director@example.com", "password": PASSWORD})
    assert response.status_code == 200
    bootstrap(client)  # Cookie and CSRF were rotated; never reuse the old token.
    return response


def test_disabled_auth_is_explicitly_unavailable(settings):
    with TestClient(create_app(settings)) as client:
        response = client.get("/api/v1/auth/csrf")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "AUTH_SERVICE_UNAVAILABLE"


def test_csrf_cookie_attributes_no_store_and_stable_token(auth_client):
    client, service = auth_client
    first = bootstrap(client)
    cookie = first.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie and "Path=/api/v1" in cookie
    assert "max-age=900" in cookie.lower()
    assert first.headers["cache-control"] == "no-store"
    assert client.cookies.get(SESSION_COOKIE) not in first.text
    second = client.get("/api/v1/auth/csrf")
    assert first.json()["data"] == second.json()["data"]
    assert "set-cookie" not in second.headers


@pytest.mark.parametrize("origin", ["https://evil.example", "null", "http://127.0.0.1:5173.evil.example", None])
def test_origin_is_required_and_exact_on_mutations(auth_client, origin):
    client, service = auth_client
    bootstrap(client)
    if origin is None:
        del client.headers["Origin"]
    else:
        client.headers["Origin"] = origin
    response = client.post("/api/v1/auth/login", json={"email": "director@example.com", "password": PASSWORD})
    assert response.status_code == 403
    assert not service.repository.users


def test_prelogin_csrf_is_required_before_verification_or_registration(auth_client):
    client, service = auth_client
    response = client.post("/api/v1/auth/register", json={"registration_token": "a" * 43, "password": PASSWORD})
    assert response.status_code == 401
    bootstrap(client)
    client.headers["X-CSRF-Token"] = "wrong"
    response = client.post("/api/v1/auth/register", json={"registration_token": "a" * 43, "password": PASSWORD})
    assert response.status_code == 403
    assert not service.repository.users


def test_mail_request_fails_closed_without_returning_a_code_or_fake_acceptance(auth_client):
    client, service = auth_client
    bootstrap(client)
    response = client.post("/api/v1/auth/email-verifications", json={"email": "director@example.com"})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "EMAIL_SERVICE_UNAVAILABLE"
    assert not client.portal.call(service.cache.redis.keys, "auth:verification:*")


def test_registration_retry_recovers_receipt_without_password_change_or_login(auth_client):
    client, service = auth_client
    token, first = register_user(client, service)
    previous_hash = service.repository.users["director@example.com"].password_hash
    calls = service.passwords.hash_calls
    second = client.post("/api/v1/auth/register", json={"registration_token": token, "password": "a different sample password"})
    assert second.status_code == 200
    assert second.json()["data"]["user_id"] == first.json()["data"]["user_id"]
    assert service.repository.users["director@example.com"].password_hash == previous_hash
    assert service.passwords.hash_calls == calls
    assert "set-cookie" not in first.headers and "set-cookie" not in second.headers
    assert client.get("/api/v1/users/me").status_code == 401


def test_login_rotates_session_and_profile_completion_survives_rename(auth_client):
    client, service = auth_client
    register_user(client, service)
    old_cookie, old_csrf = client.cookies.get(SESSION_COOKIE), client.headers["X-CSRF-Token"]
    logged = login_user(client)
    assert client.cookies.get(SESSION_COOKIE) != old_cookie
    assert logged.json()["data"]["profile_completed"] is False
    assert logged.json()["data"]["missing_fields"] == ["display_name"]
    assert "password_hash" not in logged.text
    assert client.portal.call(service.cache.read_session, old_cookie) is None
    old_token_response = client.patch("/api/v1/users/me/profile", headers={"X-CSRF-Token": old_csrf}, json={"display_name": "导演", "expected_lock_version": 1})
    assert old_token_response.status_code == 403
    updated = client.patch("/api/v1/users/me/profile", json={"display_name": "  导演  ", "expected_lock_version": 1})
    assert updated.status_code == 200 and updated.json()["data"]["profile_completed"]
    user = service.repository.users["director@example.com"]
    completed_at = user.profile_completed_at
    renamed = client.patch("/api/v1/users/me/profile", json={"display_name": "制片", "expected_lock_version": 2})
    assert renamed.status_code == 200 and user.profile_completed_at == completed_at
    stale = client.patch("/api/v1/users/me/profile", json={"display_name": "旧昵称", "expected_lock_version": 1})
    assert stale.status_code == 409 and user.display_name == "制片"
    me = client.get("/api/v1/users/me")
    assert me.status_code == 200 and me.json()["data"]["display_name"] == "制片"


@pytest.mark.parametrize("kind", ["unknown", "wrong", "disabled"])
def test_login_errors_do_not_disclose_account_state(auth_client, kind):
    client, service = auth_client
    register_user(client, service)
    email, password = "director@example.com", PASSWORD
    if kind == "unknown":
        email = "unknown@example.com"
    elif kind == "disabled":
        service.repository.users[email].status = "disabled"
    else:
        password = "wrong sample password"
    response = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "INVALID_CREDENTIALS"
    assert response.json()["error"]["message"] == "邮箱或密码错误"


def test_logout_revokes_server_session_and_clears_cookie(auth_client):
    client, service = auth_client
    register_user(client, service)
    login_user(client)
    cookie = client.cookies.get(SESSION_COOKIE)
    response = client.post("/api/v1/auth/logout")
    assert response.status_code == 204 and response.content == b""
    assert "max-age=0" in response.headers["set-cookie"].lower()
    assert client.portal.call(service.cache.read_session, cookie) is None
    assert client.get("/api/v1/users/me").status_code == 401


@pytest.mark.parametrize("failure", ["before_commit", "response_lost"])
def test_database_failure_never_returns_registration_success_but_retry_can_recover(auth_client, failure):
    client, service = auth_client
    token = registration_token(client, service)
    service.repository.fail_before_commit = failure == "before_commit"
    service.repository.lose_commit_response = failure == "response_lost"
    first = client.post("/api/v1/auth/register", json={"registration_token": token, "password": PASSWORD})
    assert first.status_code == 503
    assert "sensitive" not in first.text
    service.repository.fail_before_commit = False
    second = client.post("/api/v1/auth/register", json={"registration_token": token, "password": PASSWORD})
    assert second.status_code == (201 if failure == "before_commit" else 200)
    assert len(service.repository.users) == len(service.repository.receipts) == 1


def test_login_rate_limit_and_retry_after_are_exposed(auth_client):
    client, service = auth_client
    bootstrap(client)
    for _ in range(10):
        assert client.post("/api/v1/auth/login", json={"email": "unknown@example.com", "password": PASSWORD}).status_code == 401
    response = client.post("/api/v1/auth/login", json={"email": "unknown@example.com", "password": PASSWORD})
    assert response.status_code == 429 and int(response.headers["retry-after"]) > 0


def test_auth_body_limits_and_content_type_are_enforced_without_echo(auth_client):
    client, service = auth_client
    bootstrap(client)
    too_large = client.post("/api/v1/auth/login", content=b"x" * 17_000, headers={"Content-Type": "application/json"})
    assert too_large.status_code == 413
    assert too_large.json()["request_id"] == too_large.headers["x-request-id"]
    plain = client.post("/api/v1/auth/login", content="private payload", headers={"Content-Type": "text/plain"})
    assert plain.status_code == 415 and "private payload" not in plain.text
    assert too_large.headers["cache-control"] == plain.headers["cache-control"] == "no-store"


def test_malformed_verification_input_does_not_echo_secret(auth_client):
    client, service = auth_client
    bootstrap(client)
    response = client.post("/api/v1/auth/email-verifications/verify", json={
        "email": "director@example.com", "verification_id": str(uuid4()), "code": "private-code",
    })
    assert response.status_code == 422 and "private-code" not in response.text


def test_business_gate_is_enforced_on_the_server_not_only_by_frontend_redirect(auth_client):
    client, service = auth_client

    @client.app.get("/api/v1/test-business", include_in_schema=False)
    async def business(user=Depends(completed_user)):
        return {"allowed": True}

    register_user(client, service)
    login_user(client)
    before = client.get("/api/v1/test-business")
    assert before.status_code == 403 and before.json()["error"]["code"] == "PROFILE_INCOMPLETE"
    assert client.get("/api/v1/users/me").status_code == 200
    assert client.patch("/api/v1/users/me/profile", json={"display_name": "导演", "expected_lock_version": 1}).status_code == 200
    assert client.get("/api/v1/test-business").status_code == 200


def test_disabled_account_cannot_keep_access_using_an_existing_cookie(auth_client):
    client, service = auth_client
    register_user(client, service)
    login_user(client)
    service.repository.users["director@example.com"].status = "disabled"
    assert client.get("/api/v1/users/me").status_code == 401


def test_redis_outage_blocks_authentication_without_database_write(auth_client, monkeypatch):
    from redis.exceptions import ConnectionError

    client, service = auth_client
    bootstrap(client)

    async def broken(*args, **kwargs):
        raise ConnectionError("private connection URL")

    monkeypatch.setattr(service.cache.redis, "get", broken)
    response = client.post("/api/v1/auth/register", json={"registration_token": "a" * 43, "password": PASSWORD})
    assert response.status_code == 503
    assert "private connection" not in response.text
    assert not service.repository.users


def test_enabled_auth_factory_constructs_real_service_with_injected_redis(monkeypatch):
    from types import SimpleNamespace
    from silencio_set import main

    redis = FakeRedis()

    async def close():
        await redis.aclose()

    resources = SimpleNamespace(database=None, redis=redis, close=close)
    monkeypatch.setattr(main, "Dependencies", lambda _: resources)
    config = Settings(
        _env_file=None, app_env="test", auth_enabled=True, auth_secret="test-only-not-real-secret-" * 2,
        database_url="postgresql+asyncpg://user:example@localhost:5432/test", redis_url="redis://localhost:6379/0",
    )
    with TestClient(main.create_app(config)) as client:
        response = client.get("/api/v1/auth/csrf")
        assert response.status_code == 200
        assert len(response.json()["data"]["csrf_token"]) == 64


def test_chunked_auth_request_cannot_bypass_body_size_limit(auth_client):
    client, service = auth_client
    bootstrap(client)
    response = client.post("/api/v1/auth/login", content=(b"x" * 9_000 for _ in range(2)),
                           headers={"Content-Type": "application/json"})
    assert response.status_code == 413
