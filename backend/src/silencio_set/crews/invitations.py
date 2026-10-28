from datetime import UTC, datetime, timedelta
import hashlib
import logging
import secrets
from uuid import uuid4

from sqlalchemy import case, delete, func, select, update
from sqlalchemy.exc import DBAPIError

from silencio_set.accounts.errors import AuthError, expired_session
from silencio_set.accounts.identity import normalize_email
from silencio_set.accounts.models import User
from silencio_set.accounts.profile import AccountUnavailable, ProfileIncomplete, require_completed_profile
from silencio_set.crews.models import AuditLog, Crew, CrewInvitation, CrewMember, MemberPermissionOverride, MemberRole
from silencio_set.crews.organization import MAX_VERSION, organization_actor
from silencio_set.crews.permissions import effective_permissions
from silencio_set.crews.repository import member_view, not_found, role_map
from silencio_set.crews.invitation_schemas import InvitationCreated, InvitationJoined, InvitationPage, InvitationView
from silencio_set.jobs.models import BackgroundJob, OutboxEvent
from silencio_set.jobs.repository import INVITATION_JOB
from silencio_set.mail.invitation_payloads import INVITATION_MAIL_TTL, InvitationMail
from silencio_set.mail.payloads import PayloadUnavailable

logger = logging.getLogger(__name__)


def token_digest(token):
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def invalid_invitation():
    return AuthError(400, "INVITATION_INVALID", "邀请无效、已过期、已撤销或与当前账号不匹配")


def view(invitation, now):
    status = "expired" if invitation.status == "pending" and invitation.expires_at <= now else invitation.status
    return InvitationView(id=invitation.id, email=invitation.email_normalized, status=status,
        expires_at=invitation.expires_at, created_at=invitation.created_at, lock_version=invitation.lock_version)


async def inviter_valid(session, invitation):
    member = await session.scalar(select(CrewMember).join(User, User.id == CrewMember.user_id).where(
        CrewMember.id == invitation.invited_by_member_id, CrewMember.crew_id == invitation.crew_id,
        CrewMember.status == "active", User.status == "active"))
    if member is None:
        return False
    roles = (await role_map(session, [member]))[member.id]
    overrides = (await session.execute(select(MemberPermissionOverride.permission_code, MemberPermissionOverride.effect).where(
        MemberPermissionOverride.crew_id == invitation.crew_id, MemberPermissionOverride.member_id == member.id))).all()
    return "member.invite" in effective_permissions(roles, overrides)


async def stop_unstarted_mail(session, invitation, now):
    jobs = select(BackgroundJob.id).where(BackgroundJob.job_type == INVITATION_JOB,
        BackgroundJob.crew_id == invitation.crew_id, BackgroundJob.idempotency_key == str(invitation.id))
    await session.execute(update(BackgroundJob).where(BackgroundJob.id.in_(jobs),
        BackgroundJob.status.in_(("pending_dispatch", "queued", "retry_wait"))).values(
            status="cancelled", error_summary="INVITATION_UNAVAILABLE", result_ref={"delivery": "not_sent"}, finished_at=now))
    await session.execute(update(OutboxEvent).where(OutboxEvent.job_id.in_(jobs), OutboxEvent.status == "pending").values(
        status="failed", last_error="INVITATION_UNAVAILABLE", dispatch_token=None, lease_expires_at=None))


class InvitationRepository:
    def __init__(self, sessions, payloads=None):
        self.sessions, self.payloads = sessions, payloads

    async def create(self, user, crew_id, body, request_id):
        if self.payloads is None:
            raise AuthError(503, "EMAIL_SERVICE_UNAVAILABLE", "邀请邮件未启用，暂不能创建邀请")
        identity = normalize_email(body.email)
        async with self.sessions() as session, session.begin():
            actor = await organization_actor(session, user.id, crew_id, "member.invite")
            now = await session.scalar(select(func.clock_timestamp()))
            previous = await session.scalar(select(CrewInvitation).where(CrewInvitation.crew_id == crew_id,
                CrewInvitation.email_normalized == identity.normalized, CrewInvitation.status == "pending").with_for_update())
            if previous is not None:
                if previous.expires_at > now:
                    raise AuthError(409, "INVITATION_PENDING", "该邮箱已有待接受邀请，请先核对或撤销")
                if previous.lock_version >= MAX_VERSION:
                    raise AuthError(409, "VERSION_EXHAUSTED", "邀请版本达到上限")
                previous.status = "expired"; previous.lock_version += 1
                await stop_unstarted_mail(session, previous, now)
                await session.flush()
            existing = await session.scalar(select(CrewMember.id).join(User, User.id == CrewMember.user_id).where(
                CrewMember.crew_id == crew_id, CrewMember.status == "active", User.email_normalized == identity.normalized))
            if existing is not None:
                raise AuthError(409, "MEMBER_ALREADY_ACTIVE", "该邮箱对应的账号已在剧组中")
            crew = await session.get(Crew, crew_id)
            credential = secrets.token_urlsafe(32)
            invitation = CrewInvitation(id=uuid4(), crew_id=crew_id, email_normalized=identity.normalized,
                token_digest=token_digest(credential), invited_by_member_id=actor.id, status="pending",
                expires_at=now + timedelta(days=7), lock_version=1, created_at=now)
            payload = InvitationMail(payload_id=uuid4(), invitation_id=invitation.id, crew_id=crew_id,
                crew_name=crew.name, email=identity.display, email_normalized=identity.normalized,
                email_digest=self.payloads.cache.digest_identity("email", identity.normalized), token=credential,
                expires_at=min(int((now + timedelta(seconds=INVITATION_MAIL_TTL)).timestamp()), int(datetime.now(UTC).timestamp()) + INVITATION_MAIL_TTL),
                invitation_expires_at=int(invitation.expires_at.timestamp()))
            # Bounded Redis preparation only; SMTP/broker calls never occur under
            # the coordinator. Unknown DB commits leave ciphertext to its TTL.
            await self.payloads.save(payload)
            session.add(invitation)
            await session.flush()
            deadline = datetime.fromtimestamp(payload.expires_at, UTC)
            job = BackgroundJob(id=uuid4(), crew_id=crew_id, job_type=INVITATION_JOB, idempotency_key=str(invitation.id),
                status="pending_dispatch", attempt_count=0, expires_at=deadline, payload_ref={
                    "payload_id": str(payload.payload_id), "invitation_id": str(invitation.id), "email_digest": payload.email_digest})
            session.add(job)
            await session.flush()
            session.add(OutboxEvent(id=uuid4(), crew_id=crew_id, job_id=job.id, event_key=f"mail.invite:{invitation.id}",
                event_type=INVITATION_JOB, aggregate_ref={"invitation_id": str(invitation.id)}, payload_ref={"job_id": str(job.id)},
                status="pending", attempt_count=0, expires_at=deadline))
            session.add(AuditLog(crew_id=crew_id, actor_type="user", actor_id=user.id, action="invitation.create",
                target_type="invitation", target_id=invitation.id, result="success", request_id=request_id))
            await session.flush()
            result = InvitationCreated(invitation=view(invitation, now))
        return result

    async def list(self, user, crew_id, status, page, page_size):
        async with self.sessions() as session, session.begin():
            await organization_actor(session, user.id, crew_id, "member.invite")
            now = await session.scalar(select(func.clock_timestamp()))
            effective = case(((CrewInvitation.status == "pending") & (CrewInvitation.expires_at <= now), "expired"), else_=CrewInvitation.status)
            query = select(CrewInvitation).where(CrewInvitation.crew_id == crew_id)
            if status is not None:
                query = query.where(effective == status)
            total = await session.scalar(select(func.count()).select_from(query.subquery()))
            records = (await session.scalars(query.order_by(CrewInvitation.created_at.desc(), CrewInvitation.id.desc())
                .offset((page - 1) * page_size).limit(page_size))).all()
            return InvitationPage(items=[view(item, now) for item in records], page=page, page_size=page_size, total=total)

    async def revoke(self, user, crew_id, invitation_id, body, request_id):
        async with self.sessions() as session, session.begin():
            await organization_actor(session, user.id, crew_id, "member.invite")
            invitation = await session.scalar(select(CrewInvitation).where(CrewInvitation.id == invitation_id,
                CrewInvitation.crew_id == crew_id).with_for_update())
            if invitation is None:
                raise not_found()
            if invitation.lock_version != body.expected_lock_version:
                raise AuthError(409, "VERSION_CONFLICT", "邀请已变化，请刷新后确认")
            now = await session.scalar(select(func.clock_timestamp()))
            if invitation.status != "pending" or invitation.expires_at <= now:
                raise AuthError(409, "INVITATION_NOT_PENDING", "邀请已结束，不能撤销")
            if invitation.lock_version >= MAX_VERSION:
                raise AuthError(409, "VERSION_EXHAUSTED", "邀请版本达到上限")
            invitation.status = "revoked"; invitation.lock_version += 1
            await stop_unstarted_mail(session, invitation, now)
            session.add(AuditLog(crew_id=crew_id, actor_type="user", actor_id=user.id, action="invitation.revoke",
                target_type="invitation", target_id=invitation.id, result="success", request_id=request_id))
            await session.flush()
            result = view(invitation, now)
        return result

    async def accept(self, user, body, request_id):
        digest = token_digest(body.token.get_secret_value())
        async with self.sessions() as session, session.begin():
            current = await session.scalar(select(User).where(User.id == user.id, User.status == "active").with_for_update(read=True))
            if current is None:
                raise expired_session()
            require_completed_profile(current)
            crew_id = await session.scalar(select(CrewInvitation.crew_id).where(CrewInvitation.token_digest == digest))
            if crew_id is None:
                raise invalid_invitation()
            if await session.scalar(select(Crew).where(Crew.id == crew_id).with_for_update()) is None:
                raise invalid_invitation()
            invitation = await session.scalar(select(CrewInvitation).where(CrewInvitation.token_digest == digest,
                CrewInvitation.crew_id == crew_id).with_for_update())
            now = await session.scalar(select(func.clock_timestamp()))
            if invitation is None or invitation.expires_at <= now or invitation.email_normalized != current.email_normalized:
                raise invalid_invitation()
            existing = await session.scalar(select(CrewMember).where(CrewMember.crew_id == crew_id,
                CrewMember.user_id == current.id).with_for_update())
            if invitation.status == "accepted":
                if invitation.accepted_by_user_id != current.id or existing is None or existing.status != "active":
                    raise invalid_invitation()
                roles = (await role_map(session, [existing]))[existing.id]
                return InvitationJoined(crew_id=crew_id, membership=member_view(existing, roles))
            if invitation.status != "pending" or not await inviter_valid(session, invitation):
                raise invalid_invitation()
            if invitation.lock_version >= MAX_VERSION or (existing is not None and existing.lock_version >= MAX_VERSION):
                raise AuthError(409, "VERSION_EXHAUSTED", "记录版本达到上限")
            if existing is None:
                existing = CrewMember(id=uuid4(), crew_id=crew_id, user_id=current.id, display_name=current.display_name,
                    status="active", joined_at=now, lock_version=1)
                session.add(existing)
                await session.flush()
            elif existing.status != "active":
                await session.execute(delete(MemberRole).where(MemberRole.crew_id == crew_id, MemberRole.member_id == existing.id))
                await session.execute(delete(MemberPermissionOverride).where(MemberPermissionOverride.crew_id == crew_id,
                    MemberPermissionOverride.member_id == existing.id))
                existing.display_name = current.display_name; existing.status = "active"; existing.joined_at = now
                existing.ended_at = None; existing.lock_version += 1
            invitation.status = "accepted"; invitation.accepted_by_user_id = current.id
            invitation.accepted_at = now; invitation.lock_version += 1
            await stop_unstarted_mail(session, invitation, now)
            session.add(AuditLog(crew_id=crew_id, actor_type="user", actor_id=current.id, action="invitation.accept",
                target_type="invitation", target_id=invitation.id, result="success", request_id=request_id,
                after_summary={"member_id": str(existing.id)}))
            await session.flush()
            roles = (await role_map(session, [existing]))[existing.id]
            result = InvitationJoined(crew_id=crew_id, membership=member_view(existing, roles))
        return result

    async def delivery_current(self, payload):
        async with self.sessions() as session:
            invitation = await session.scalar(select(CrewInvitation).where(CrewInvitation.id == payload.invitation_id,
                CrewInvitation.crew_id == payload.crew_id, CrewInvitation.token_digest == token_digest(payload.token),
                CrewInvitation.email_normalized == payload.email_normalized, CrewInvitation.status == "pending",
                CrewInvitation.expires_at > func.clock_timestamp()))
            return invitation is not None and await inviter_valid(session, invitation)


class InvitationService:
    def __init__(self, repository, audits):
        self.repository, self.audits = repository, audits

    async def execute(self, user, action, request_id, *, crew_id=None, invitation_id=None, body=None, status=None, page=1, page_size=20):
        try:
            require_completed_profile(user)
            if action == "invitation.create": return await self.repository.create(user, crew_id, body, request_id)
            if action == "invitation.list": return await self.repository.list(user, crew_id, status, page, page_size)
            if action == "invitation.revoke": return await self.repository.revoke(user, crew_id, invitation_id, body, request_id)
            if action == "invitation.accept": return await self.repository.accept(user, body, request_id)
            raise ValueError("Unsupported invitation action")
        except (AuthError, DBAPIError, PayloadUnavailable, AccountUnavailable, ProfileIncomplete) as failure:
            if isinstance(failure, AccountUnavailable): failure = expired_session()
            if isinstance(failure, ProfileIncomplete): failure = AuthError(403, "PROFILE_INCOMPLETE", "请先补全昵称")
            if isinstance(failure, PayloadUnavailable): failure = AuthError(503, "EMAIL_SERVICE_UNAVAILABLE", "邀请邮件准备失败")
            code = failure.code if isinstance(failure, AuthError) else "SERVICE_UNAVAILABLE"
            try:
                await self.audits.record_failure(user.id, action, invitation_id,
                    "denied" if isinstance(failure, AuthError) and failure.status < 500 else "failed", code, request_id, target_type="invitation")
            except Exception:
                logger.error("invitation_audit_failed request_id=%s", request_id)
            if isinstance(failure, AuthError): raise failure
            raise AuthError(503, "SERVICE_UNAVAILABLE", "邀请服务暂不可用，请检查结果后再操作") from None
