from datetime import UTC, datetime
import secrets

import anyio

from silencio_set.accounts.cache import AuthCache, BrowserSession, TOKEN_PATTERN, token_digest
from silencio_set.accounts.errors import AuthError, expired_session, invalid_grant
from silencio_set.accounts.identity import normalize_email
from silencio_set.accounts.passwords import PasswordService
from silencio_set.accounts.profile import AccountUnavailable, ProfileVersionConflict
from silencio_set.accounts.repository import AccountRepository
from silencio_set.accounts.schemas import LoginRequest, ProfileUpdate, RegistrationRequest, UserView


class AuthService:
    def __init__(self, cache: AuthCache, repository: AccountRepository, passwords: PasswordService | None = None):
        self.cache = cache
        self.repository = repository
        self.passwords = passwords if passwords is not None else PasswordService()
        self._hash_limiter = anyio.CapacityLimiter(2)
        # Startup work, before accepting requests. Unknown accounts still run
        # the actual password verifier; no hardcoded account or login bypass.
        self._dummy_hash = self.passwords.hash(secrets.token_urlsafe(32))

    async def register(self, request: RegistrationRequest, ip: str):
        token = request.registration_token.get_secret_value()
        if not TOKEN_PATTERN.fullmatch(token):
            raise invalid_grant()
        digest = token_digest(token)
        await self.cache.limit("register", ip, digest, ip_limit=10, identity_limit=5)
        previous = await self.repository.registration(digest)
        if previous is not None:
            await self.cache.cleanup_grant(token)
            return previous
        grant = await self.cache.grant(token)
        if grant is None or grant.expires_at <= datetime.now(UTC).timestamp():
            raise invalid_grant()
        hashed = await anyio.to_thread.run_sync(
            self.passwords.hash, request.password.get_secret_value(), limiter=self._hash_limiter,
        )
        outcome = await self.repository.create_registration(digest, grant, hashed)
        await self.cache.cleanup_grant(token)
        return outcome

    async def login(self, request: LoginRequest, browser: BrowserSession, ip: str):
        identity = normalize_email(request.email)
        await self.cache.limit("login", ip, identity.normalized, ip_limit=20, identity_limit=10)
        user = await self.repository.login_user(identity.normalized)
        stored_hash = user.password_hash if user is not None else self._dummy_hash
        checked = await anyio.to_thread.run_sync(
            self.passwords.verify, stored_hash, request.password.get_secret_value(), limiter=self._hash_limiter,
        )
        if user is None or not checked.matched or user.status != "active":
            raise AuthError(401, "INVALID_CREDENTIALS", "邮箱或密码错误")
        if checked.replacement_hash is not None:
            await self.repository.upgrade_hash(user.id, stored_hash, checked.replacement_hash)
        # Recheck account availability after password work and before granting a session.
        current = await self.repository.user(user.id)
        if current is None:
            raise AuthError(401, "INVALID_CREDENTIALS", "邮箱或密码错误")
        new_session = await self.cache.rotate(browser, user.id)
        return UserView.model_validate(current), new_session

    async def current_user(self, token: str | None):
        browser = await self.cache.read_session(token)
        if browser is None or browser.user_id is None:
            raise expired_session()
        user = await self.repository.user(browser.user_id)
        if user is None:
            raise expired_session()
        return user

    async def update_profile(self, token: str | None, changes: ProfileUpdate):
        user = await self.current_user(token)
        try:
            return await self.repository.profile(user.id, changes)
        except AccountUnavailable:
            raise expired_session() from None
        except ProfileVersionConflict:
            raise AuthError(409, "VERSION_CONFLICT", "资料已更新，请刷新后重试") from None
