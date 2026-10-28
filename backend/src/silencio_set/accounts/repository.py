from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from silencio_set.accounts.cache import Grant
from silencio_set.accounts.errors import AuthError, invalid_grant
from silencio_set.accounts.models import RegistrationReceipt, User
from silencio_set.accounts.profile import save_profile
from silencio_set.accounts.schemas import ProfileUpdate, ProfileView


@dataclass(frozen=True)
class RegistrationOutcome:
    user_id: UUID
    created: bool


def receipt_outcome(receipt: RegistrationReceipt | None) -> RegistrationOutcome | None:
    if receipt is None:
        return None
    if receipt.token_expires_at <= datetime.now(UTC):
        raise invalid_grant()
    return RegistrationOutcome(receipt.user_id, created=False)


class AccountRepository:
    def __init__(self, session_factory):
        self.sessions = session_factory

    async def registration(self, digest: str) -> RegistrationOutcome | None:
        async with self.sessions() as session, session.begin():
            receipt = await session.scalar(select(RegistrationReceipt).where(RegistrationReceipt.token_digest == digest))
            return receipt_outcome(receipt)

    async def create_registration(self, digest: str, grant: Grant, password_hash: str) -> RegistrationOutcome:
        try:
            async with self.sessions() as session, session.begin():
                receipt = await session.scalar(select(RegistrationReceipt).where(RegistrationReceipt.token_digest == digest))
                previous = receipt_outcome(receipt)
                if previous is not None:
                    return previous
                now = datetime.now(UTC)
                expires = datetime.fromtimestamp(grant.expires_at, UTC)
                if expires <= now:
                    raise invalid_grant()
                user_id = uuid4()
                session.add(User(
                    id=user_id, email=grant.email, email_normalized=grant.email_normalized,
                    password_hash=password_hash, email_verified_at=datetime.fromtimestamp(grant.verified_at, UTC),
                    status="active", display_name=None, profile_completed_at=None, lock_version=1,
                ))
                # Explicit flush orders the parent before the receipt's FK.
                await session.flush()
                session.add(RegistrationReceipt(
                    id=uuid4(), token_digest=digest, email_normalized=grant.email_normalized,
                    user_id=user_id, token_expires_at=expires, consumed_at=now,
                ))
                await session.flush()
                outcome = RegistrationOutcome(user_id, created=True)
            # Context exit committed both records before a success is returned.
            return outcome
        except IntegrityError:
            # The failed transaction was rolled back. A concurrent request may
            # have committed this same token; never overwrite its password.
            previous = await self.registration(digest)
            if previous is not None:
                return previous
            raise AuthError(409, "REGISTRATION_NOT_COMPLETED", "无法完成注册，请登录或重新验证邮箱") from None

    async def login_user(self, normalized_email: str) -> User | None:
        async with self.sessions() as session, session.begin():
            return await session.scalar(select(User).where(User.email_normalized == normalized_email))

    async def user(self, user_id: UUID) -> User | None:
        async with self.sessions() as session, session.begin():
            return await session.scalar(select(User).where(User.id == user_id, User.status == "active"))

    async def upgrade_hash(self, user_id: UUID, old_hash: str, replacement: str):
        async with self.sessions() as session, session.begin():
            await session.execute(update(User).where(
                User.id == user_id, User.status == "active", User.password_hash == old_hash,
            ).values(password_hash=replacement))

    async def profile(self, user_id: UUID, changes: ProfileUpdate) -> ProfileView:
        async with self.sessions() as session, session.begin():
            return await save_profile(session, user_id, changes)
