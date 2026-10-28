from dataclasses import dataclass, field
from hashlib import sha256
import hmac
import json
import re
import secrets
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError, model_validator
from redis.exceptions import RedisError

from silencio_set.accounts.errors import AuthError, expired_session, invalid_grant, unavailable
from silencio_set.accounts.identity import normalize_email
from silencio_set.accounts import redis_scripts
from silencio_set.core.config import Settings

TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{43}")


def token_digest(token: str) -> str:
    return sha256(token.encode("ascii")).hexdigest()


class Grant(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", hide_input_in_errors=True)
    email: str = Field(repr=False)
    email_normalized: str = Field(repr=False)
    verified_at: int
    expires_at: int

    @model_validator(mode="after")
    def validate_binding(self):
        if normalize_email(self.email).normalized != self.email_normalized or self.expires_at <= self.verified_at:
            raise ValueError("Invalid registration binding")
        return self


@dataclass(frozen=True)
class IssuedVerification:
    verification_id: UUID
    code: SecretStr = field(repr=False)
    expires_at: int


@dataclass(frozen=True)
class ExchangedVerification:
    token: SecretStr = field(repr=False)
    grant: Grant = field(repr=False)


class SessionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    user_id: UUID | None


@dataclass(frozen=True)
class BrowserSession:
    token: SecretStr = field(repr=False)
    user_id: UUID | None
    max_age: int


class AuthCache:
    def __init__(self, redis, settings: Settings):
        self.redis = redis
        self.settings = settings
        self._secret = settings.auth_secret.get_secret_value().encode("utf-8")

    def digest_identity(self, purpose: str, identity: str) -> str:
        return hmac.new(self._secret, f"{purpose}|{identity}".encode("utf-8"), sha256).hexdigest()

    def session_key(self, token: str) -> str:
        return "auth:session:" + self.digest_identity("session", token)

    def grant_key(self, token: str) -> str:
        return "auth:registration:" + token_digest(token)

    def challenge_key(self, email: str) -> str:
        return "auth:verification:register:" + self.digest_identity("email", email)

    async def call(self, operation, *args, **kwargs):
        try:
            return await operation(*args, **kwargs)
        except (RedisError, OSError, TimeoutError):
            raise unavailable() from None

    async def limit(self, purpose: str, ip: str, identity: str, *, ip_limit: int, identity_limit: int):
        prefix = "auth:limit:" + purpose + ":"
        keys = [prefix + self.digest_identity("ip", ip), prefix + self.digest_identity("identity", identity)]
        accepted, retry = await self.call(self.redis.eval, redis_scripts.RATE_LIMIT, 2, *keys, 60, ip_limit, identity_limit)
        if not accepted:
            raise AuthError(429, "RATE_LIMITED", "请求过于频繁，请稍后再试", retry_after=int(retry))

    async def issue_verification(self, email: str, ip: str) -> IssuedVerification:
        """Internal primitive only. Not an email delivery or HTTP acceptance API.

        MailRequests must encrypt the short-lived payload and commit its durable
        job/outbox before returning 202. Never enqueue this raw code.
        """
        identity = normalize_email(email)
        await self.limit("issue", ip, identity.normalized, ip_limit=10, identity_limit=5)
        verification_id = uuid4()
        code = f"{secrets.randbelow(1_000_000):06d}"
        digest = self.digest_identity("code", f"{verification_id}|{identity.normalized}|{code}")
        record = json.dumps({"verification_id": str(verification_id), "email": identity.display,
                             "email_normalized": identity.normalized, "code_digest": digest, "failures": 0})
        key = self.challenge_key(identity.normalized)
        accepted = await self.call(
            self.redis.eval, redis_scripts.ISSUE_VERIFICATION, 2, key, key + ":cooldown", record,
            self.settings.auth_verification_ttl, self.settings.auth_resend_interval,
        )
        if not accepted:
            raise AuthError(429, "RATE_LIMITED", "请稍后再申请验证码", retry_after=self.settings.auth_resend_interval)
        return IssuedVerification(verification_id, SecretStr(code), int(accepted))

    async def exchange(self, email: str, verification_id: UUID, code: str, ip: str) -> ExchangedVerification:
        identity = normalize_email(email)
        await self.limit("verify", ip, identity.normalized, ip_limit=30, identity_limit=10)
        digest = self.digest_identity("code", f"{verification_id}|{identity.normalized}|{code}")
        token = secrets.token_urlsafe(32)
        raw = await self.call(
            self.redis.eval, redis_scripts.EXCHANGE_VERIFICATION, 2,
            self.challenge_key(identity.normalized), self.grant_key(token), str(verification_id), digest,
            self.settings.auth_verification_attempts, self.settings.auth_registration_ttl,
        )
        if raw is None:
            raise AuthError(400, "VERIFICATION_INVALID", "验证码无效、已过期或尝试次数过多，请重新申请")
        try:
            grant = Grant.model_validate_json(raw)
        except ValidationError:
            raise unavailable() from None
        return ExchangedVerification(SecretStr(token), grant)

    async def grant(self, token: str) -> Grant | None:
        if not TOKEN_PATTERN.fullmatch(token):
            raise invalid_grant()
        raw = await self.call(self.redis.get, self.grant_key(token))
        if raw is None:
            return None
        try:
            return Grant.model_validate_json(raw)
        except ValidationError:
            raise unavailable() from None

    async def cleanup_grant(self, token: str):
        try:
            await self.call(self.redis.delete, self.grant_key(token))
        except AuthError:
            # A committed database receipt remains the source of truth.
            pass

    def csrf(self, token: str) -> str:
        return self.digest_identity("csrf", token)

    async def read_session(self, token: str | None) -> BrowserSession | None:
        if token is None or not TOKEN_PATTERN.fullmatch(token):
            return None
        raw = await self.call(self.redis.get, self.session_key(token))
        if raw is None:
            return None
        try:
            record = SessionRecord.model_validate_json(raw)
        except ValidationError:
            raise unavailable() from None
        age = self.settings.auth_session_ttl if record.user_id else self.settings.auth_prelogin_ttl
        return BrowserSession(SecretStr(token), record.user_id, age)

    async def prelogin(self, existing: str | None) -> BrowserSession:
        current = await self.read_session(existing)
        if current is not None:
            return current
        token = secrets.token_urlsafe(32)
        age = self.settings.auth_prelogin_ttl
        accepted = await self.call(self.redis.set, self.session_key(token), SessionRecord(user_id=None).model_dump_json(), ex=age, nx=True)
        if not accepted:
            raise unavailable()
        return BrowserSession(SecretStr(token), None, age)

    async def validate_csrf(self, token: str | None, supplied: str | None) -> BrowserSession:
        session = await self.read_session(token)
        if session is None:
            raise expired_session()
        if supplied is None or re.fullmatch(r"[a-f0-9]{64}", supplied) is None or not hmac.compare_digest(self.csrf(session.token.get_secret_value()), supplied):
            raise AuthError(403, "CSRF_FAILED", "安全校验失败，请刷新页面后重试")
        return session

    async def rotate(self, old: BrowserSession, user_id: UUID) -> BrowserSession:
        token = secrets.token_urlsafe(32)
        age = self.settings.auth_session_ttl
        success = await self.call(self.redis.eval, redis_scripts.ROTATE_SESSION, 2,
                                  self.session_key(old.token.get_secret_value()), self.session_key(token),
                                  SessionRecord(user_id=user_id).model_dump_json(), age)
        if not success:
            raise expired_session()
        return BrowserSession(SecretStr(token), user_id, age)

    async def logout(self, session: BrowserSession):
        await self.call(self.redis.delete, self.session_key(session.token.get_secret_value()))
