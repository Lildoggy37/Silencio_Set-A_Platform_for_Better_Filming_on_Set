"""Writes serialize on the crew row; every organization mutation must use it."""
from uuid import UUID

from sqlalchemy import delete, func, select

from silencio_set.accounts.errors import AuthError, expired_session
from silencio_set.accounts.models import User
from silencio_set.accounts.profile import require_completed_profile
from silencio_set.crews.models import AuditLog, Crew, CrewMember, MemberPermissionOverride, MemberRole
from silencio_set.crews.permissions import effective_permissions
from silencio_set.crews.repository import member_view, not_found, role_map
from silencio_set.crews.schemas import MemberExitResult, MemberLeave, MemberRemove, MemberRolesUpdate, MemberView

MAX_VERSION = 2_147_483_647


def other_admins_query(crew_id: UUID, target_id: UUID):
    return select(func.count()).select_from(CrewMember).join(MemberRole,
        (MemberRole.crew_id == CrewMember.crew_id) & (MemberRole.member_id == CrewMember.id),
    ).join(User, User.id == CrewMember.user_id).where(
        CrewMember.crew_id == crew_id, CrewMember.id != target_id, CrewMember.status == "active",
        User.status == "active", MemberRole.role_code == "admin",
    )


async def organization_actor(session, user_id: UUID, crew_id: UUID, permission: str | None):
    # Global order for this module: acting account -> crew -> member. A shared
    # account lock lets distinct users contend on the crew coordinator safely.
    user = await session.scalar(select(User).where(User.id == user_id, User.status == "active").with_for_update(read=True))
    if user is None:
        raise expired_session()
    require_completed_profile(user)
    crew = await session.scalar(select(Crew).where(Crew.id == crew_id).with_for_update())
    if crew is None:
        raise not_found()
    # Recheck AFTER acquiring the coordinator. A waiting former admin must not
    # act with authority read before another transaction revoked it.
    actor = await session.scalar(select(CrewMember).where(
        CrewMember.crew_id == crew_id, CrewMember.user_id == user_id, CrewMember.status == "active",
    ).with_for_update())
    if actor is None:
        raise not_found()
    if permission is None:
        # Self-exit only requires current membership, even with crew.read denied.
        return actor
    roles = (await role_map(session, [actor]))[actor.id]
    overrides = (await session.execute(select(MemberPermissionOverride.permission_code, MemberPermissionOverride.effect).where(
        MemberPermissionOverride.crew_id == crew_id, MemberPermissionOverride.member_id == actor.id,
    ))).all()
    if permission is not None and permission not in effective_permissions(roles, overrides):
        raise AuthError(403, "FORBIDDEN", "当前没有执行该剧组管理操作的权限")
    return actor


def check_roles_update(target, old_roles, body: MemberRolesUpdate, other_admins: int):
    if target.status != "active":
        raise AuthError(409, "MEMBER_INACTIVE", "成员已不在组，不能修改成员资料")
    if target.lock_version != body.expected_lock_version:
        raise AuthError(409, "VERSION_CONFLICT", "成员资料已更新，请重新读取后确认")
    if target.lock_version >= MAX_VERSION:
        raise AuthError(409, "VERSION_EXHAUSTED", "成员版本达到上限，暂不能修改")
    if "admin" in old_roles and "admin" not in body.role_codes and other_admins < 1:
        raise AuthError(409, "LAST_ADMIN_REQUIRED", "剧组至少需要一名有效管理员，请先完成管理员交接")


class OrganizationRepository:
    def __init__(self, sessions, *, exit_checks=()):
        self.sessions = sessions
        # Business modules register async checks here when they are introduced.
        # Each runs under the crew coordinator, in the exit transaction, before
        # any mutation. A blocking check raises AuthError; failure must abort.
        # The application factory wires the current task responsibility check;
        # reservation/plan modules must register theirs when implemented.
        self.exit_checks = tuple(exit_checks)

    async def end_membership(self, user_id: UUID, crew_id: UUID, member_id: UUID | None,
                             body: MemberLeave, request_id: str) -> MemberExitResult:
        removing = member_id is not None
        if removing != isinstance(body, MemberRemove):
            raise ValueError("Invalid internal member exit operation")
        async with self.sessions() as session, session.begin():
            actor = await organization_actor(session, user_id, crew_id, "member.remove" if removing else None)
            target = await session.scalar(select(CrewMember).where(
                CrewMember.crew_id == crew_id, CrewMember.id == member_id,
            ).with_for_update()) if removing else actor
            if target is None:
                raise not_found()
            if removing and target.id == actor.id:
                raise AuthError(409, "USE_LEAVE_ACTION", "请使用本人退出入口离开剧组")
            previous = (await role_map(session, [target]))[target.id]
            other_admins = await session.scalar(other_admins_query(crew_id, target.id)) if "admin" in previous else 0
            # An exit removes all roles, so it has the same version/admin gate
            # as replacing the role set with an empty set.
            check_roles_update(target, previous, MemberRolesUpdate(role_codes=[],
                expected_lock_version=body.expected_lock_version), other_admins)
            for check in self.exit_checks:
                await check(session, crew_id, target.id)
            before = {"status": target.status, "role_codes": previous, "lock_version": target.lock_version}
            ended_at = await session.scalar(select(func.clock_timestamp()))
            await session.execute(delete(MemberRole).where(MemberRole.crew_id == crew_id, MemberRole.member_id == target.id))
            await session.execute(delete(MemberPermissionOverride).where(
                MemberPermissionOverride.crew_id == crew_id, MemberPermissionOverride.member_id == target.id))
            target.status = "removed" if removing else "left"
            target.ended_at = ended_at
            target.lock_version += 1
            after = {"status": target.status, "role_codes": [], "lock_version": target.lock_version}
            if removing:
                after["reason"] = body.reason  # Internal audit only; never echoed to directory/logs.
            session.add(AuditLog(crew_id=crew_id, actor_type="user", actor_id=user_id,
                action="member.remove" if removing else "member.leave", target_type="crew_member", target_id=target.id,
                result="success", request_id=request_id, before_summary=before, after_summary=after))
            await session.flush()
            result = MemberExitResult(membership=member_view(target, []), ended_at=ended_at)
        return result

    async def replace_roles(self, user_id: UUID, crew_id: UUID, member_id: UUID,
                            body: MemberRolesUpdate, request_id: str) -> MemberView:
        async with self.sessions() as session, session.begin():
            await organization_actor(session, user_id, crew_id, "member.authorize")
            target = await session.scalar(select(CrewMember).where(
                CrewMember.crew_id == crew_id, CrewMember.id == member_id,
            ).with_for_update())
            if target is None:
                raise not_found()
            previous = (await role_map(session, [target]))[target.id]
            other_admins = await session.scalar(other_admins_query(crew_id, member_id)) if "admin" in previous and "admin" not in body.role_codes else 0
            check_roles_update(target, previous, body, other_admins)
            before = {"role_codes": previous, "lock_version": target.lock_version}
            await session.execute(delete(MemberRole).where(MemberRole.crew_id == crew_id, MemberRole.member_id == member_id))
            for code in body.role_codes:
                session.add(MemberRole(crew_id=crew_id, member_id=member_id, role_code=code))
            target.lock_version += 1
            session.add(AuditLog(crew_id=crew_id, actor_type="user", actor_id=user_id, action="member.roles.replace",
                target_type="crew_member", target_id=member_id, result="success", request_id=request_id,
                before_summary=before, after_summary={"role_codes": body.role_codes, "lock_version": target.lock_version}))
            await session.flush()
            result = member_view(target, body.role_codes)
        return result
