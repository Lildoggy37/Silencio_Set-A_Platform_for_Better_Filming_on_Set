from hashlib import sha256
from uuid import uuid4

from sqlalchemy import delete, func, select

from silencio_set.accounts.errors import AuthError
from silencio_set.crews.models import AuditLog, MemberPermissionOverride
from silencio_set.crews.repository import not_found
from silencio_set.resources import repository as resources
from silencio_set.resources.custody_models import Checkout, ResourceReturn
from silencio_set.resources.custody_schemas import CheckoutPage, CheckoutView, CustodyAuthorizationView
from silencio_set.resources.domain import check_version
from silencio_set.resources.models import Resource, Reservation, ResourceReceipt, ResourceScopeGrant, ResourceChangeEvent
from silencio_set.tasks.repository import permissions_for, request_digest
from silencio_set.resources.custody_queries import outstanding

CODES = {"resource.checkout", "resource.return"}


def require_operator(actor, scopes, code):
    resources.require(actor, code)
    if code not in scopes: raise AuthError(403, "FORBIDDEN", "缺少该资源的借还经办范围")


def checkout_view(row, now):
    remaining = row.quantity - row.returned_quantity
    return CheckoutView(**{field: getattr(row, field) for field in CheckoutView.model_fields if field not in {"outstanding_quantity", "status"}},
        outstanding_quantity=remaining, status="returned" if remaining == 0 else "overdue" if row.expected_return_at < now else "outstanding")


class CustodyRepository:
    def __init__(self, sessions): self.sessions = sessions

    async def get(self, user_id, crew_id, checkout_id):
        async with self.sessions() as session, session.begin():
            actor = await resources.resource_actor(session, user_id, crew_id, writing=True)
            resources.require(actor, "resource.read")
            loan = await session.scalar(select(Checkout).where(Checkout.crew_id == crew_id, Checkout.id == checkout_id))
            if loan is None: raise not_found()
            resource = await session.scalar(select(Resource).where(Resource.crew_id == crew_id, Resource.id == loan.resource_id))
            scopes = await resources.scope_codes(session, resource, actor)
            if not (actor.permissions & scopes & CODES) and not (loan.borrower_member_id == actor.member_id and loan.borrower_membership_joined_at == actor.joined_at):
                raise not_found()
            return checkout_view(loan, await session.scalar(select(func.clock_timestamp())))

    async def list(self, user_id, crew_id, resource_id, page, page_size):
        async with self.sessions() as session, session.begin():
            actor = await resources.resource_actor(session, user_id, crew_id, writing=True)
            resources.require(actor, "resource.read")
            resource = await session.scalar(select(Resource).where(Resource.crew_id == crew_id, Resource.id == resource_id))
            if resource is None: raise not_found()
            scopes = await resources.scope_codes(session, resource, actor)
            query = select(Checkout).where(Checkout.crew_id == crew_id, Checkout.resource_id == resource_id)
            if not (actor.permissions & scopes & CODES):
                query = query.where(Checkout.borrower_member_id == actor.member_id, Checkout.borrower_membership_joined_at == actor.joined_at)
            total = await session.scalar(select(func.count()).select_from(query.subquery()))
            rows = (await session.scalars(query.order_by(Checkout.checked_out_at.desc(), Checkout.id).offset((page - 1) * page_size).limit(page_size))).all()
            now = await session.scalar(select(func.clock_timestamp()))
            return CheckoutPage(items=[checkout_view(row, now) for row in rows], total=total, page=page, page_size=page_size)

    async def mutate(self, user_id, crew_id, action, body, request_id, *, idempotency_key, resource_id=None, reservation_id=None, checkout_id=None):
        async with self.sessions() as session, session.begin():
            actor = await resources.resource_actor(session, user_id, crew_id, writing=True)
            resources.require(actor, "resource.read")
            digest = request_digest(action, checkout_id or reservation_id or resource_id, body)
            key = sha256(idempotency_key.encode()).hexdigest()
            receipt = await session.scalar(select(ResourceReceipt).where(ResourceReceipt.crew_id == crew_id,
                ResourceReceipt.user_id == user_id, ResourceReceipt.key_digest == key))
            if receipt:
                if receipt.membership_joined_at != actor.joined_at: raise AuthError(409, "MEMBERSHIP_CHANGED", "入组身份已变化")
                if receipt.request_digest != digest: raise AuthError(409, "IDEMPOTENCY_KEY_REUSED", "同键不能用于不同内容")
            if checkout_id:
                loan = await session.scalar(select(Checkout).where(Checkout.crew_id == crew_id, Checkout.id == checkout_id))
                if loan is None: raise not_found()
                reservation_id, resource_id = loan.reservation_id, loan.resource_id
            if reservation_id:
                booking = await session.scalar(select(Reservation).where(Reservation.crew_id == crew_id, Reservation.id == reservation_id))
                if booking is None: raise not_found()
                resource_id = booking.resource_id
            resource = await session.scalar(select(Resource).where(Resource.crew_id == crew_id, Resource.id == resource_id).with_for_update())
            if resource is None: raise not_found()
            scopes = await resources.scope_codes(session, resource, actor)
            if action in {"custody_authorize", "stock_authorize"}: resources.require(actor, "member.authorize")
            else: require_operator(actor, scopes, "resource.checkout" if action == "checkout" else "resource.return")
            if receipt:
                model = CustodyAuthorizationView if action in {"custody_authorize", "stock_authorize"} else CheckoutView
                return model.model_validate(receipt.response_snapshot)
            now = await session.scalar(select(func.clock_timestamp()))
            if action in {"custody_authorize", "stock_authorize"}:
                codes = {"stock.manage"} if action == "stock_authorize" else CODES
                check_version(resource, body.expected_lock_version)
                if resource.resource_type not in ({"consumable"} if action == "stock_authorize" else {"equipment", "prop"}): raise AuthError(409, "RESOURCE_TYPE_UNSUPPORTED", "资源类型与经办范围不匹配")
                member = await resources.active_member(session, crew_id, body.member_id)
                check_version(member, body.expected_member_lock_version)
                permissions = (await permissions_for(session, [member]))[member.id]
                if not body.grant_capabilities and not set(body.permission_codes) <= permissions:
                    raise AuthError(409, "MEMBER_NOT_ELIGIBLE", "须已有经办能力，或管理员明确授予")
                changed = []
                if body.grant_capabilities:
                    for code in body.permission_codes:
                        override = await session.scalar(select(MemberPermissionOverride).where(MemberPermissionOverride.crew_id == crew_id,
                            MemberPermissionOverride.member_id == member.id, MemberPermissionOverride.permission_code == code))
                        if override is None or override.effect != "allow":
                            changed.append({"code": code, "before": override.effect if override else None, "after": "allow"})
                            if override: override.effect = "allow"
                            else: session.add(MemberPermissionOverride(crew_id=crew_id, member_id=member.id, permission_code=code, effect="allow"))
                    if changed: member.lock_version += 1
                previous = (await session.scalars(select(ResourceScopeGrant.permission_code).where(ResourceScopeGrant.crew_id == crew_id,
                    ResourceScopeGrant.resource_id == resource.id, ResourceScopeGrant.member_id == member.id,
                    ResourceScopeGrant.permission_code.in_(codes)))).all()
                await session.execute(delete(ResourceScopeGrant).where(ResourceScopeGrant.crew_id == crew_id, ResourceScopeGrant.resource_id == resource.id,
                    ResourceScopeGrant.member_id == member.id, ResourceScopeGrant.permission_code.in_(codes)))
                for code in body.permission_codes:
                    session.add(ResourceScopeGrant(crew_id=crew_id, resource_id=resource.id, member_id=member.id,
                        permission_code=code, membership_joined_at=member.joined_at, granted_by=actor.member_id))
                resource.lock_version += 1; resource.updated_at = now
                old_manager = resource.manager_member_id
                if action == "stock_authorize" and body.transfer_management: resource.manager_member_id = member.id
                result = CustodyAuthorizationView(resource_id=resource.id, member_id=member.id,
                    resource_lock_version=resource.lock_version, member_lock_version=member.lock_version, permission_codes=body.permission_codes)
                before = {"scopes": sorted(previous), "resource_version": resource.lock_version - 1}
                after = {**result.model_dump(mode="json"), "capability_changes": changed}
                if action == "stock_authorize":
                    before["manager_member_id"] = str(old_manager)
                    after["manager_member_id"] = str(resource.manager_member_id)
                target_id, target_type = resource.id, "resource"
            else:
                booking = await session.scalar(select(Reservation).where(Reservation.crew_id == crew_id, Reservation.id == reservation_id).with_for_update())
                if action == "checkout":
                    check_version(booking, body.expected_lock_version)
                    if resource.resource_type not in {"equipment", "prop"}: raise AuthError(409, "RESOURCE_TYPE_UNSUPPORTED", "场地不提供实物借还")
                    if resource.status != "available" or booking.status != "confirmed" or not booking.starts_at <= now < booking.ends_at:
                        raise AuthError(409, "CHECKOUT_NOT_ALLOWED", "须在有效且已批准的预约时段内借出")
                    if not now <= body.expected_return_at <= booking.ends_at: raise AuthError(409, "INVALID_RETURN_TIME", "预计归还须在本次借出与预约结束之间")
                    borrower = await resources.active_member(session, crew_id, booking.applicant_member_id)
                    if borrower.joined_at != booking.applicant_membership_joined_at: raise AuthError(409, "MEMBERSHIP_CHANGED", "借用人成员身份已变化")
                    issued = await session.scalar(select(func.coalesce(func.sum(Checkout.quantity), 0)).where(Checkout.crew_id == crew_id, Checkout.reservation_id == booking.id))
                    physical = await outstanding(session, crew_id, resource_id=resource.id)
                    if issued + body.quantity > booking.quantity or physical + body.quantity > resource.total_quantity:
                        raise AuthError(409, "PHYSICAL_CAPACITY_CONFLICT", "超过预约累计借出量或实际可借数量")
                    loan = Checkout(id=uuid4(), crew_id=crew_id, resource_id=resource.id, reservation_id=booking.id,
                        borrower_member_id=borrower.id, borrower_membership_joined_at=borrower.joined_at, quantity=body.quantity,
                        returned_quantity=0, checked_out_at=now, expected_return_at=body.expected_return_at, handled_by=actor.member_id, lock_version=1)
                    session.add(loan)
                    check_version(booking, booking.lock_version)
                else:
                    loan = await session.scalar(select(Checkout).where(Checkout.crew_id == crew_id, Checkout.id == checkout_id).with_for_update())
                    check_version(loan, body.expected_lock_version)
                    check_version(booking, booking.lock_version)
                    if now < loan.checked_out_at or body.quantity > loan.quantity - loan.returned_quantity:
                        raise AuthError(409, "RETURN_QUANTITY_CONFLICT", "归还数量超过未归还量，或系统时间异常")
                    loan.returned_quantity += body.quantity; loan.lock_version += 1
                    session.add(ResourceReturn(id=uuid4(), crew_id=crew_id, checkout_id=loan.id, quantity=body.quantity,
                        returned_at=now, handled_by=actor.member_id, note=body.note))
                booking.lock_version += 1; booking.updated_at = now
                result = checkout_view(loan, now)
                before = {"reservation_version": booking.lock_version - 1}
                after = {**result.model_dump(mode="json"), "reservation_version": booking.lock_version, "operation_quantity": body.quantity}
                target_id, target_type = loan.id, "resource_checkout"
                session.add(ResourceChangeEvent(crew_id=crew_id, resource_id=resource.id, reservation_id=booking.id,
                    event_key=f"custody:{loan.id}:{loan.lock_version}", event_type=action,
                    recipient_member_ids=sorted({str(actor.member_id), str(loan.borrower_member_id), str(resource.manager_member_id)})))
            session.add(AuditLog(crew_id=crew_id, actor_type="user", actor_id=user_id, action=f"resource.{action}",
                target_type=target_type, target_id=target_id, result="success", request_id=request_id, before_summary=before, after_summary=after))
            session.add(ResourceReceipt(crew_id=crew_id, user_id=user_id, membership_joined_at=actor.joined_at,
                resource_id=resource.id, reservation_id=reservation_id, key_digest=key, request_digest=digest,
                response_snapshot=result.model_dump(mode="json")))
            await session.flush()
        return result
