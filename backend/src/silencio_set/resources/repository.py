"""Crew-coordinated resource writes, scoped authorization and booking privacy."""
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from uuid import UUID, uuid4

from sqlalchemy import delete, exists, func, literal, or_, select, union_all

from silencio_set.accounts.errors import AuthError
from silencio_set.accounts.models import User
from silencio_set.crews.models import AuditLog, CrewMember, MemberPermissionOverride
from silencio_set.crews.organization import MAX_VERSION
from silencio_set.crews.repository import not_found, role_map
from silencio_set.resources.domain import check_version, peak_quantity, require_capacity
from silencio_set.resources.models import Resource, ResourceScopeGrant, Reservation, ResourceReceipt, ResourceChangeEvent
from silencio_set.resources.schemas import ResourceAvailability, ResourcePage, ResourceView, ReservationPage, ReservationView, ResourceResponsibilities, ResourceResponsibilityItem
from silencio_set.tasks.repository import permissions_for, task_actor, request_digest

CAPACITY_LIMIT = 1000


@dataclass(frozen=True)
class ResourceActor:
    member_id: UUID
    permissions: frozenset[str]
    roles: frozenset[str]
    joined_at: datetime


def require(actor, permission):
    if permission not in actor.permissions:
        raise AuthError(403, "FORBIDDEN", "当前没有执行该资源操作的权限")


async def resource_actor(session, user_id, crew_id, *, writing=False):
    actor = await task_actor(session, user_id, crew_id, writing=writing)
    member = await session.scalar(select(CrewMember).where(CrewMember.crew_id == crew_id,
        CrewMember.id == actor.member_id, CrewMember.status == "active"))
    if member is None: raise not_found()
    roles = (await role_map(session, [member]))[member.id]
    return ResourceActor(member.id, actor.permissions, frozenset(roles), member.joined_at)


def review_scope(crew_id, actor):
    if "reservation.review" not in actor.permissions: return literal(False)
    return (Resource.manager_member_id == actor.member_id) & exists(select(1).where(
        ResourceScopeGrant.crew_id == crew_id, ResourceScopeGrant.resource_id == Resource.id,
        ResourceScopeGrant.member_id == actor.member_id,
        ResourceScopeGrant.permission_code == "reservation.review",
        ResourceScopeGrant.membership_joined_at == actor.joined_at))


def visible_reservations(crew_id, actor):
    require(actor, "resource.read")
    return select(Reservation).join(Resource, (Resource.id == Reservation.resource_id) &
        (Resource.crew_id == Reservation.crew_id)).where(Reservation.crew_id == crew_id,
        or_((Reservation.applicant_member_id == actor.member_id) &
            (Reservation.applicant_membership_joined_at == actor.joined_at), review_scope(crew_id, actor)))


async def active_member(session, crew_id, member_id):
    member = await session.scalar(select(CrewMember).join(User, User.id == CrewMember.user_id).where(
        CrewMember.crew_id == crew_id, CrewMember.id == member_id,
        CrewMember.status == "active", User.status == "active"))
    if member is None: raise AuthError(409, "MEMBER_INACTIVE", "资源负责人或申请人必须仍在当前剧组且账号有效")
    return member


async def scope_codes(session, resource, actor):
    return set((await session.scalars(select(ResourceScopeGrant.permission_code).where(
        ResourceScopeGrant.crew_id == resource.crew_id, ResourceScopeGrant.resource_id == resource.id,
        ResourceScopeGrant.member_id == actor.member_id,
        ResourceScopeGrant.membership_joined_at == actor.joined_at))).all())


def can_manage(actor, codes):
    return "resource.manage" in actor.permissions and ("producer" in actor.roles or "resource.manage" in codes)


def can_review(resource, actor, codes):
    return resource.manager_member_id == actor.member_id and "reservation.review" in actor.permissions and "reservation.review" in codes


async def resource_views(session, rows, actor):
    if not rows: return []
    ids = {row.id for row in rows}
    members = (await session.scalars(select(CrewMember).join(User, User.id == CrewMember.user_id).where(
        CrewMember.crew_id == rows[0].crew_id, CrewMember.id.in_({row.manager_member_id for row in rows}),
        CrewMember.status == "active", User.status == "active"))).all()
    member_map = {member.id: member for member in members}
    permissions = await permissions_for(session, members)
    grants = (await session.scalars(select(ResourceScopeGrant).where(
        ResourceScopeGrant.crew_id == rows[0].crew_id, ResourceScopeGrant.resource_id.in_(ids),
        ResourceScopeGrant.member_id.in_({actor.member_id, *member_map})))).all()
    codes = {row.id: set() for row in rows}
    ready = set()
    for grant in grants:
        if grant.member_id == actor.member_id and grant.membership_joined_at == actor.joined_at:
            codes[grant.resource_id].add(grant.permission_code)
        manager = member_map.get(grant.member_id)
        if manager and grant.membership_joined_at == manager.joined_at and grant.permission_code == "reservation.review" and "reservation.review" in permissions[manager.id]:
            ready.add((grant.resource_id, manager.id))
    result = []
    for row in rows:
        manager = member_map.get(row.manager_member_id)
        actions = []
        if can_manage(actor, codes[row.id]): actions.append("edit")
        if "member.authorize" in actor.permissions: actions.append("authorize")
        if "reservation.apply" in actor.permissions and row.status == "available": actions.append("apply")
        result.append(ResourceView(**{field: getattr(row, field) for field in ResourceView.model_fields if field not in {
            "manager_display_name", "manager_lock_version", "review_ready", "allowed_actions"}},
            manager_display_name=manager.display_name if manager else "负责人已失效",
            manager_lock_version=manager.lock_version if manager else 0,
            review_ready=(row.id, row.manager_member_id) in ready, allowed_actions=actions))
    return result


def reservation_view(row, actor, reviewer, now):
    actions = []
    own = row.applicant_member_id == actor.member_id and row.applicant_membership_joined_at == actor.joined_at
    if reviewer and row.status == "pending":
        actions.append("reject")
        if row.ends_at > now: actions.append("accept")
    if row.status in {"pending", "confirmed"} and row.ends_at > now and (reviewer or own and "reservation.apply" in actor.permissions): actions.append("cancel")
    return ReservationView(**{field: getattr(row, field) for field in ReservationView.model_fields if field not in {
        "allowed_actions", "dependency_check_status"}}, allowed_actions=actions)


async def capacity_rows(session, resource, start, end=None):
    query = select(Reservation).where(Reservation.crew_id == resource.crew_id,
        Reservation.resource_id == resource.id, Reservation.status == "confirmed", Reservation.ends_at > start)
    if end is not None: query = query.where(Reservation.starts_at < end)
    rows = (await session.scalars(query.order_by(Reservation.starts_at, Reservation.id).limit(CAPACITY_LIMIT + 1))).all()
    if len(rows) > CAPACITY_LIMIT:
        raise AuthError(409, "CAPACITY_WINDOW_TOO_LARGE", "预约数据超过单次检查上限，请缩小查询范围或联系管理员")
    return rows


def intervals(rows): return [(row.starts_at, row.ends_at, row.quantity) for row in rows]


def resource_blockers(crew_id, member_id):
    return select(Resource.id).where(Resource.crew_id == crew_id, Resource.manager_member_id == member_id)


def reservation_blockers(crew_id, member_id):
    return select(Reservation.id).where(Reservation.crew_id == crew_id,
        Reservation.applicant_member_id == member_id, Reservation.status.in_({"pending", "confirmed"}),
        Reservation.ends_at > func.clock_timestamp())


async def check_resource_responsibilities(session, crew_id, member_id):
    if await session.scalar(resource_blockers(crew_id, member_id).limit(1)) is not None or await session.scalar(reservation_blockers(crew_id, member_id).limit(1)) is not None:
        raise AuthError(409, "MEMBER_HAS_RESOURCE_RESPONSIBILITIES", "仍有负责的资源或未结束预约，请先转交资源、取消或处理预约")


class ResourceRepository:
    def __init__(self, sessions): self.sessions = sessions

    async def list(self, user_id, crew_id, page, page_size):
        async with self.sessions() as session:
            actor = await resource_actor(session, user_id, crew_id)
            require(actor, "resource.read")
            query = select(Resource).where(Resource.crew_id == crew_id)
            total = await session.scalar(select(func.count()).select_from(query.subquery()))
            rows = (await session.scalars(query.order_by(Resource.created_at, Resource.id).offset((page - 1) * page_size).limit(page_size))).all()
            return ResourcePage(items=await resource_views(session, rows, actor), total=total, page=page, page_size=page_size)

    async def get(self, user_id, crew_id, resource_id):
        async with self.sessions() as session:
            actor = await resource_actor(session, user_id, crew_id)
            require(actor, "resource.read")
            row = await session.scalar(select(Resource).where(Resource.crew_id == crew_id, Resource.id == resource_id))
            if row is None: raise not_found()
            return (await resource_views(session, [row], actor))[0]

    async def availability(self, user_id, crew_id, resource_id, window):
        # The snapshot includes resource quantity/version and confirmed rows.
        # Crew coordination also makes this advisory result internally consistent.
        async with self.sessions() as session, session.begin():
            actor = await resource_actor(session, user_id, crew_id, writing=True)
            require(actor, "resource.read")
            row = await session.scalar(select(Resource).where(Resource.crew_id == crew_id, Resource.id == resource_id))
            if row is None: raise not_found()
            rows = await capacity_rows(session, row, window.range_start, window.range_end)
            peak = peak_quantity(intervals(rows), window.range_start, window.range_end)
            return ResourceAvailability(resource_id=row.id, resource_lock_version=row.lock_version,
                range_start=window.range_start, range_end=window.range_end, total_quantity=row.total_quantity,
                peak_confirmed_quantity=peak, minimum_remaining_quantity=max(0, row.total_quantity - peak),
                confirmed_reservation_count=len(rows))

    async def reservations(self, user_id, crew_id, page, page_size, *, resource_id=None, status=None):
        async with self.sessions() as session:
            actor = await resource_actor(session, user_id, crew_id)
            query = visible_reservations(crew_id, actor)
            if resource_id is not None: query = query.where(Reservation.resource_id == resource_id)
            if status is not None: query = query.where(Reservation.status == status)
            total = await session.scalar(select(func.count()).select_from(query.subquery()))
            rows = (await session.scalars(query.order_by(Reservation.created_at.desc(), Reservation.id).offset((page - 1) * page_size).limit(page_size))).all()
            reviewer_ids = set((await session.scalars(select(Resource.id).where(Resource.crew_id == crew_id,
                Resource.id.in_({row.resource_id for row in rows}), review_scope(crew_id, actor)))).all()) if rows else set()
            now = await session.scalar(select(func.clock_timestamp()))
            return ReservationPage(items=[reservation_view(row, actor, row.resource_id in reviewer_ids, now) for row in rows],
                total=total, page=page, page_size=page_size)

    async def reservation(self, user_id, crew_id, reservation_id):
        async with self.sessions() as session:
            actor = await resource_actor(session, user_id, crew_id)
            row = await session.scalar(visible_reservations(crew_id, actor).where(Reservation.id == reservation_id))
            if row is None: raise not_found()
            resource = await session.scalar(select(Resource).where(Resource.crew_id == crew_id, Resource.id == row.resource_id))
            codes = await scope_codes(session, resource, actor)
            now = await session.scalar(select(func.clock_timestamp()))
            return reservation_view(row, actor, can_review(resource, actor, codes), now)

    async def responsibilities(self, user_id, crew_id, member_id, page, page_size):
        async with self.sessions() as session, session.begin():
            actor = await resource_actor(session, user_id, crew_id, writing=True)
            if actor.member_id != member_id: require(actor, "member.remove")
            member = await session.scalar(select(CrewMember).where(CrewMember.crew_id == crew_id,
                CrewMember.id == member_id, CrewMember.status == "active"))
            if member is None: raise not_found()
            managed = select(literal("resource").label("kind"), Resource.id, Resource.id.label("resource_id"),
                Resource.name.label("title"), Resource.status, Resource.lock_version,
                literal(None).cast(Reservation.ends_at.type).label("ends_at")).where(Resource.id.in_(resource_blockers(crew_id, member_id)))
            bookings = select(literal("reservation").label("kind"), Reservation.id, Reservation.resource_id,
                Resource.name.label("title"), Reservation.status, Reservation.lock_version, Reservation.ends_at).join(
                Resource, (Resource.id == Reservation.resource_id) & (Resource.crew_id == Reservation.crew_id)).where(
                Reservation.id.in_(reservation_blockers(crew_id, member_id)))
            all_rows = union_all(managed, bookings).subquery()
            blocking_total = await session.scalar(select(func.count()).select_from(all_rows))
            if "resource.read" in actor.permissions:
                visible = union_all(managed, bookings.where(Reservation.id.in_(visible_reservations(crew_id, actor).with_only_columns(Reservation.id)))).subquery()
                total = await session.scalar(select(func.count()).select_from(visible))
                rows = (await session.execute(select(visible).order_by(visible.c.kind, visible.c.id).offset((page - 1) * page_size).limit(page_size))).mappings().all()
            else: total, rows = 0, []
            return ResourceResponsibilities(member_id=member_id, items=[ResourceResponsibilityItem(**row) for row in rows],
                total=total, blocking_total=blocking_total, restricted_total=blocking_total - total, page=page, page_size=page_size)

    async def mutate(self, user_id, crew_id, action, body, request_id, *, resource_id=None, reservation_id=None, idempotency_key=None):
        if action != "edit" and not idempotency_key: raise ValueError("Idempotency key required")
        async with self.sessions() as session, session.begin():
            actor = await resource_actor(session, user_id, crew_id, writing=True)
            require(actor, "resource.read")
            digest = request_digest(action, reservation_id or resource_id, body)
            key = sha256(idempotency_key.encode()).hexdigest() if idempotency_key else None
            receipt = await session.scalar(select(ResourceReceipt).where(ResourceReceipt.crew_id == crew_id,
                ResourceReceipt.user_id == user_id, ResourceReceipt.key_digest == key)) if key else None
            if receipt is not None:
                if receipt.membership_joined_at != actor.joined_at: raise AuthError(409, "MEMBERSHIP_CHANGED", "成员身份已变化，请重新读取")
                if receipt.request_digest != digest: raise AuthError(409, "IDEMPOTENCY_KEY_REUSED", "同一幂等键不能用于不同请求")
                resource_id = receipt.resource_id
            if action == "create":
                require(actor, "resource.manage")
                if "producer" not in actor.roles: raise AuthError(403, "FORBIDDEN", "仅具备资源管理权限的制片可登记资源")
            if reservation_id is not None:
                booking = await session.scalar(visible_reservations(crew_id, actor).where(Reservation.id == reservation_id))
                if booking is None: raise not_found()
                resource_id = booking.resource_id
            if action == "apply": resource_id = body.resource_id
            resource = await session.scalar(select(Resource).where(Resource.crew_id == crew_id,
                Resource.id == resource_id).with_for_update()) if resource_id is not None else None
            if resource_id is not None and resource is None: raise not_found()
            codes = await scope_codes(session, resource, actor) if resource else set()
            reviewer = bool(resource and can_review(resource, actor, codes))
            if action == "edit" and not can_manage(actor, codes): raise AuthError(403, "FORBIDDEN", "没有此资源的维护权限")
            if action == "authorize": require(actor, "member.authorize")
            if action == "apply": require(actor, "reservation.apply")
            if reservation_id is not None:
                booking = await session.scalar(visible_reservations(crew_id, actor).where(Reservation.id == reservation_id).with_for_update(of=Reservation))
                if booking is None: raise not_found()
                own = booking.applicant_member_id == actor.member_id and booking.applicant_membership_joined_at == actor.joined_at
                if action == "decide" and not reviewer or action == "cancel" and not (reviewer or own and "reservation.apply" in actor.permissions):
                    raise AuthError(403, "FORBIDDEN", "当前没有此预约的操作资格")
            if receipt is not None:
                if action in {"decide", "cancel", "apply"}:
                    # Apply replay must retain visibility even after a transfer.
                    current = await session.scalar(visible_reservations(crew_id, actor).where(Reservation.id == receipt.reservation_id))
                    if current is None: raise not_found()
                model = ReservationView if action in {"apply", "decide", "cancel"} else ResourceView
                return model.model_validate({**receipt.response_snapshot, "allowed_actions": []})
            now = await session.scalar(select(func.clock_timestamp()))
            before = {"lock_version": resource.lock_version, "status": resource.status,
                "manager_member_id": str(resource.manager_member_id), "total_quantity": resource.total_quantity,
                "name": resource.name, "unit": resource.unit} if resource else None
            old_manager = resource.manager_member_id if resource else None
            if action == "create":
                await active_member(session, crew_id, body.manager_member_id)
                resource = Resource(id=uuid4(), crew_id=crew_id, created_by_member_id=actor.member_id,
                    **body.model_dump(), status="available", lock_version=1, created_at=now, updated_at=now)
                session.add(resource)
            elif action == "edit":
                check_version(resource, body.expected_lock_version)
                changes = body.model_dump(exclude_unset=True, exclude={"expected_lock_version"})
                if "total_quantity" in changes or changes.get("status") == "disabled":
                    rows = await capacity_rows(session, resource, now)
                    if rows and changes.get("status") == "disabled": raise AuthError(409, "RESOURCE_IN_USE", "仍有未结束的已批准预约，不能停用")
                    peak = peak_quantity(intervals(rows), now, datetime.max.replace(tzinfo=timezone.utc))
                    if peak > changes.get("total_quantity", resource.total_quantity): raise AuthError(409, "RESOURCE_CAPACITY_CONFLICT", "数量不能低于已批准预约的同时占用峰值")
                for field, value in changes.items(): setattr(resource, field, value)
                resource.lock_version += 1
                resource.updated_at = now
            elif action == "authorize":
                check_version(resource, body.expected_lock_version)
                manager = await active_member(session, crew_id, body.manager_member_id)
                check_version(manager, body.expected_manager_lock_version)
                permission = (await permissions_for(session, [manager]))[manager.id]
                if not body.grant_review_permission and "reservation.review" not in permission:
                    raise AuthError(409, "MEMBER_NOT_ELIGIBLE", "负责人尚无预约审批权限；管理员须明确授权")
                if body.grant_review_permission:
                    old = await session.scalar(select(MemberPermissionOverride).where(MemberPermissionOverride.crew_id == crew_id,
                        MemberPermissionOverride.member_id == manager.id, MemberPermissionOverride.permission_code == "reservation.review"))
                    if old is None or old.effect != "allow":
                        old_effect = old.effect if old else None
                        if old: old.effect = "allow"
                        else: session.add(MemberPermissionOverride(crew_id=crew_id, member_id=manager.id, permission_code="reservation.review", effect="allow"))
                        manager.lock_version += 1
                        session.add(AuditLog(crew_id=crew_id, actor_type="user", actor_id=user_id,
                            action="member.permission.resource_review", target_type="crew_member", target_id=manager.id,
                            result="success", request_id=request_id, before_summary={"effect": old_effect, "lock_version": manager.lock_version - 1},
                            after_summary={"permission_code": "reservation.review", "effect": "allow", "lock_version": manager.lock_version}))
                await session.execute(delete(ResourceScopeGrant).where(ResourceScopeGrant.crew_id == crew_id,
                    ResourceScopeGrant.resource_id == resource.id, ResourceScopeGrant.permission_code == "reservation.review"))
                session.add(ResourceScopeGrant(crew_id=crew_id, resource_id=resource.id, member_id=manager.id,
                    permission_code="reservation.review", membership_joined_at=manager.joined_at, granted_by=actor.member_id))
                resource.manager_member_id = manager.id
                resource.lock_version += 1
                resource.updated_at = now
            elif action == "apply":
                if resource.status != "available": raise AuthError(409, "RESOURCE_UNAVAILABLE", "资源已停用")
                if body.starts_at < now or body.quantity > resource.total_quantity:
                    raise AuthError(409, "INVALID_RESERVATION", "预约开始时间不能早于当前时间，数量不能超过登记总量")
                booking = Reservation(id=uuid4(), crew_id=crew_id, resource_id=resource.id,
                    applicant_member_id=actor.member_id, applicant_membership_joined_at=actor.joined_at,
                    starts_at=body.starts_at, ends_at=body.ends_at, quantity=body.quantity, status="pending",
                    lock_version=1, created_at=now, updated_at=now)
                session.add(booking)
            elif action in {"decide", "cancel"}:
                check_version(booking, body.expected_lock_version)
                before = {"lock_version": booking.lock_version, "status": booking.status}
                if action == "decide":
                    if booking.status != "pending": raise AuthError(409, "INVALID_STATE", "仅待审批预约可审批")
                    if body.decision == "accept":
                        if resource.status != "available": raise AuthError(409, "RESOURCE_UNAVAILABLE", "资源已停用")
                        if booking.ends_at <= now: raise AuthError(409, "RESERVATION_EXPIRED", "预约时段已结束")
                        applicant = await active_member(session, crew_id, booking.applicant_member_id)
                        if applicant.joined_at != booking.applicant_membership_joined_at: raise AuthError(409, "MEMBERSHIP_CHANGED", "申请人的成员身份已变化")
                        rows = await capacity_rows(session, resource, booking.starts_at, booking.ends_at)
                        require_capacity(intervals(rows), booking.starts_at, booking.ends_at, booking.quantity, resource.total_quantity)
                    booking.status = "confirmed" if body.decision == "accept" else "rejected"
                    booking.reviewed_by, booking.reviewed_at = actor.member_id, now
                    booking.decision_note, booking.approval_request_key = body.decision_note, key
                else:
                    if booking.status not in {"pending", "confirmed"} or booking.ends_at <= now: raise AuthError(409, "INVALID_STATE", "仅未结束的待审批或已批准预约可取消")
                    booking.status = "cancelled"
                    booking.cancelled_by, booking.cancelled_at, booking.cancel_reason = actor.member_id, now, body.reason
                booking.lock_version += 1
                booking.updated_at = now
            else: raise ValueError("Unsupported resource operation")
            await session.flush()
            is_booking = action in {"apply", "decide", "cancel"}
            result = reservation_view(booking, actor, reviewer, now) if is_booking else (await resource_views(session, [resource], actor))[0]
            target = booking if is_booking else resource
            after = {"lock_version": target.lock_version, "status": target.status, "resource_id": str(resource.id)}
            if not is_booking:
                after.update(manager_member_id=str(resource.manager_member_id), total_quantity=resource.total_quantity,
                    name=resource.name, unit=resource.unit)
            session.add(AuditLog(crew_id=crew_id, actor_type="user", actor_id=user_id,
                action=f"resource.{action}", target_type="resource_reservation" if is_booking else "resource", target_id=target.id,
                result="success", request_id=request_id, before_summary=before,
                after_summary=after))
            recipients = {actor.member_id, resource.manager_member_id}
            if action == "authorize" and old_manager is not None: recipients.add(old_manager)
            if is_booking: recipients.add(booking.applicant_member_id)
            session.add(ResourceChangeEvent(crew_id=crew_id, resource_id=resource.id,
                reservation_id=booking.id if is_booking else None, event_key=f"{'reservation' if is_booking else 'resource'}:{target.id}:{target.lock_version}",
                event_type=action, recipient_member_ids=sorted(map(str, recipients))))
            if key:
                session.add(ResourceReceipt(crew_id=crew_id, user_id=user_id, membership_joined_at=actor.joined_at,
                    resource_id=resource.id, reservation_id=booking.id if is_booking else None,
                    key_digest=key, request_digest=digest, response_snapshot=result.model_dump(mode="json")))
            await session.flush()
        return result
