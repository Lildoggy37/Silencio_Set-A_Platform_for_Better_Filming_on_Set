from uuid import UUID, uuid4

from sqlalchemy import exists, func, select

from silencio_set.accounts.errors import AuthError, expired_session
from silencio_set.accounts.models import User
from silencio_set.accounts.profile import require_completed_profile
from silencio_set.crews.models import AuditLog, Crew, CrewMember, MemberPermissionOverride, MemberRole
from silencio_set.crews.permissions import effective_permissions
from silencio_set.crews.schemas import CrewCreate, CrewPage, CrewView, MemberPage, MemberView


def not_found():
    return AuthError(404, "NOT_FOUND", "剧组不存在或无权访问")


def visible_crews(user_id: UUID):
    denied = exists(select(1).where(
        MemberPermissionOverride.crew_id == CrewMember.crew_id,
        MemberPermissionOverride.member_id == CrewMember.id,
        MemberPermissionOverride.permission_code == "crew.read", MemberPermissionOverride.effect == "deny",
    ))
    return select(Crew, CrewMember).join(CrewMember, CrewMember.crew_id == Crew.id).where(
        CrewMember.user_id == user_id, CrewMember.status == "active", ~denied,
    )


async def role_map(session, members):
    if not members:
        return {}
    ids = [member.id for member in members]
    rows = (await session.execute(select(MemberRole.member_id, MemberRole.role_code).where(MemberRole.member_id.in_(ids)))).all()
    result = {member_id: [] for member_id in ids}
    for member_id, code in rows:
        result[member_id].append(code)
    return {member_id: sorted(codes) for member_id, codes in result.items()}


def member_view(member, roles):
    return MemberView(id=member.id, display_name=member.display_name, status=member.status,
                      role_codes=roles, lock_version=member.lock_version)


async def crew_views(session, rows):
    members = [member for _, member in rows]
    roles = await role_map(session, members)
    overrides = {member.id: [] for member in members}
    if members:
        items = (await session.execute(select(
            MemberPermissionOverride.member_id, MemberPermissionOverride.permission_code, MemberPermissionOverride.effect,
        ).where(MemberPermissionOverride.member_id.in_(overrides)))).all()
        for member_id, code, effect in items:
            overrides[member_id].append((code, effect))
    return [CrewView(id=crew.id, name=crew.name, project_name=crew.project_name, description=crew.description,
                     created_at=crew.created_at, membership=member_view(member, roles[member.id]),
                     permissions=sorted(effective_permissions(roles[member.id], overrides[member.id]))) for crew, member in rows]


class CrewRepository:
    def __init__(self, sessions):
        self.sessions = sessions

    async def create(self, user_id: UUID, body: CrewCreate, request_id: str) -> CrewView:
        async with self.sessions() as session, session.begin():
            user = await session.scalar(select(User).where(User.id == user_id, User.status == "active").with_for_update())
            if user is None:
                raise expired_session()
            require_completed_profile(user)
            crew = Crew(id=uuid4(), created_by=user.id, **body.model_dump())
            session.add(crew)
            await session.flush()
            member = CrewMember(id=uuid4(), crew_id=crew.id, user_id=user.id, display_name=user.display_name,
                                status="active", lock_version=1)
            session.add(member)
            await session.flush()
            session.add(MemberRole(crew_id=crew.id, member_id=member.id, role_code="admin"))
            session.add(AuditLog(crew_id=crew.id, actor_type="user", actor_id=user.id, action="crew.create",
                                 target_type="crew", target_id=crew.id, result="success", request_id=request_id,
                                 after_summary={"initial_role": "admin", "member_id": str(member.id)}))
            await session.flush()
            view = CrewView(id=crew.id, name=crew.name, project_name=crew.project_name, description=crew.description,
                            created_at=crew.created_at, membership=member_view(member, ["admin"]),
                            permissions=sorted(effective_permissions(["admin"], [])))
        # Do not return success before the transaction exit has committed.
        return view

    async def list(self, user_id: UUID, page: int, page_size: int) -> CrewPage:
        async with self.sessions() as session:
            query = visible_crews(user_id)
            total = await session.scalar(select(func.count()).select_from(query.subquery()))
            rows = (await session.execute(query.order_by(Crew.created_at.desc(), Crew.id.desc())
                                          .offset((page - 1) * page_size).limit(page_size))).all()
            return CrewPage(items=await crew_views(session, rows), total=total, page=page, page_size=page_size)

    async def get(self, user_id: UUID, crew_id: UUID) -> CrewView:
        async with self.sessions() as session:
            row = (await session.execute(visible_crews(user_id).where(Crew.id == crew_id))).one_or_none()
            if row is None:
                raise not_found()
            return (await crew_views(session, [row]))[0]

    async def members(self, user_id: UUID, crew_id: UUID, page: int, page_size: int) -> MemberPage:
        async with self.sessions() as session:
            row = (await session.execute(visible_crews(user_id).where(Crew.id == crew_id))).one_or_none()
            if row is None:
                raise not_found()
            context = (await crew_views(session, [row]))[0]
            if "member.read" not in context.permissions:
                raise not_found()
            query = select(CrewMember).where(CrewMember.crew_id == crew_id, CrewMember.status == "active")
            total = await session.scalar(select(func.count()).select_from(query.subquery()))
            members = (await session.scalars(query.order_by(CrewMember.joined_at, CrewMember.id)
                                            .offset((page - 1) * page_size).limit(page_size))).all()
            roles = await role_map(session, members)
            return MemberPage(items=[member_view(member, roles[member.id]) for member in members],
                              total=total, page=page, page_size=page_size)

    async def record_failure(self, actor_id: UUID, action: str, target_id: UUID | None,
                             result: str, reason_code: str, request_id: str, *, target_type="crew"):
        async with self.sessions() as session, session.begin():
            # An untrusted target may not exist, so don't attach it as a crew FK.
            session.add(AuditLog(actor_type="user", actor_id=actor_id, crew_id=None, action=action,
                                 target_type=target_type, target_id=target_id, result=result,
                                 reason_code=reason_code, request_id=request_id))
