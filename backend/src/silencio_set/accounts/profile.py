from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from silencio_set.accounts.models import User
from silencio_set.accounts.schemas import ProfileUpdate, ProfileView


class AccountUnavailable(Exception):
    """Use a uniform session failure, never disclose a different account."""


class ProfileVersionConflict(Exception):
    pass


class ProfileIncomplete(Exception):
    pass


def require_completed_profile(user: User) -> None:
    if user.status != "active":
        raise AccountUnavailable()
    if not user.profile_completed:
        raise ProfileIncomplete()


def profile_update_statement(user_id: UUID, changes: ProfileUpdate, now: datetime):
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("资料修改时间必须包含时区")
    return (
        update(User)
        .where(
            User.id == user_id,
            User.status == "active",
            User.lock_version == changes.expected_lock_version,
            User.lock_version < 2_147_483_647,
        )
        .values(
            display_name=changes.display_name,
            profile_completed_at=func.coalesce(User.profile_completed_at, now.astimezone(UTC)),
            updated_at=now.astimezone(UTC),
            lock_version=User.lock_version + 1,
        )
        .returning(User)
        .execution_options(populate_existing=True, synchronize_session=False)
    )


async def save_profile(session: AsyncSession, user_id: UUID, changes: ProfileUpdate) -> ProfileView:
    """Caller supplies the authenticated ID and owns commit/rollback.

    This is deliberately not an HTTP endpoint. Session authentication and CSRF
    must be implemented before exposing it. No external calls happen here.
    """
    result = await session.execute(profile_update_statement(user_id, changes, datetime.now(UTC)))
    user = result.scalar_one_or_none()
    if user is None:
        active_version = await session.scalar(select(User.lock_version).where(User.id == user_id, User.status == "active"))
        if active_version is None:
            raise AccountUnavailable()
        raise ProfileVersionConflict()
    return ProfileView.model_validate(user)
