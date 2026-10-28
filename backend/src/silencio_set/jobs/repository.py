from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import or_, select, update, func

from silencio_set.jobs.models import BackgroundJob, OutboxEvent
from silencio_set.mail.smtp import Delivery
from silencio_set.accounts.errors import AuthError

MAIL_JOB = "verification_email"
MAIL_TASK = "silencio.mail.send_verification"
INVITATION_JOB = "invitation_email"
INVITATION_TASK = "silencio.mail.send_invitation"
MAIL_TYPES = (MAIL_JOB, INVITATION_JOB)
DISPATCH_LIMIT = 5


@dataclass(frozen=True)
class DispatchClaim:
    event_id: UUID
    job_id: UUID
    token: UUID
    attempt: int
    task_name: str = MAIL_TASK


@dataclass(frozen=True)
class JobClaim:
    job_id: UUID
    token: UUID
    payload_ref: dict


class MailJobs:
    def __init__(self, session_factory, job_type=MAIL_JOB):
        self.sessions = session_factory
        if job_type not in MAIL_TYPES:
            raise ValueError("Unsupported mail job type")
        self.job_type = job_type

    async def enqueue(self, verification_id: UUID, payload_id: UUID, email_digest: str, expires_at: int):
        job_id, event_id = uuid4(), uuid4()
        deadline = datetime.fromtimestamp(expires_at, UTC)
        async with self.sessions() as session, session.begin():
            database_now = await session.scalar(select(func.clock_timestamp()))
            if deadline <= database_now:
                raise AuthError(503, "EMAIL_SERVICE_UNAVAILABLE", "验证请求已过期，请重新申请")
            session.add(BackgroundJob(
                id=job_id, job_type=MAIL_JOB, idempotency_key=str(verification_id), status="pending_dispatch",
                attempt_count=0, expires_at=deadline,
                payload_ref={"payload_id": str(payload_id), "verification_id": str(verification_id), "email_digest": email_digest},
            ))
            await session.flush()
            session.add(OutboxEvent(
                id=event_id, job_id=job_id, event_key=f"mail.verify:{verification_id}", event_type=MAIL_JOB,
                aggregate_ref={"verification_id": str(verification_id)}, payload_ref={"job_id": str(job_id)},
                status="pending", attempt_count=0, expires_at=deadline,
            ))
            await session.flush()
        return job_id

    async def claim_outbox(self) -> list[DispatchClaim]:
        async with self.sessions() as session, session.begin():
            now = func.clock_timestamp()
            await session.execute(update(BackgroundJob).where(
                BackgroundJob.job_type.in_(MAIL_TYPES), BackgroundJob.status == "running", BackgroundJob.lease_expires_at <= now,
            ).values(status="failed", error_summary="DELIVERY_UNKNOWN", result_ref={"delivery": "unknown"},
                     finished_at=now, lease_expires_at=None))
            await session.execute(update(BackgroundJob).where(
                BackgroundJob.job_type.in_(MAIL_TYPES), BackgroundJob.status.in_(("pending_dispatch", "queued", "retry_wait")), BackgroundJob.expires_at <= now,
            ).values(status="failed", error_summary="MAIL_EXPIRED", result_ref={"delivery": "not_sent"}, finished_at=now))
            await session.execute(update(OutboxEvent).where(
                OutboxEvent.event_type.in_(MAIL_TYPES), OutboxEvent.status == "pending", or_(OutboxEvent.expires_at <= now, OutboxEvent.attempt_count >= DISPATCH_LIMIT),
                or_(OutboxEvent.lease_expires_at.is_(None), OutboxEvent.lease_expires_at <= now),
            ).values(status="failed", last_error="DISPATCH_EXHAUSTED_OR_EXPIRED", dispatch_token=None, lease_expires_at=None))
            stopped_jobs = select(OutboxEvent.job_id).where(OutboxEvent.event_type.in_(MAIL_TYPES), OutboxEvent.status == "failed")
            await session.execute(update(BackgroundJob).where(
                BackgroundJob.id.in_(stopped_jobs), BackgroundJob.status.in_(("pending_dispatch", "queued", "retry_wait")),
            ).values(status="failed", error_summary="DISPATCH_STOPPED", result_ref={"delivery": "not_sent"}, finished_at=now))
            # Reconcile confirmed messages whose consumer never claimed the job.
            # Re-publishing is bounded and still requires the consumer's claim.
            unclaimed = select(BackgroundJob.id).where(
                BackgroundJob.job_type.in_(MAIL_TYPES), BackgroundJob.status == "queued", BackgroundJob.scheduled_at < now - timedelta(seconds=30), BackgroundJob.expires_at > now,
            )
            await session.execute(update(OutboxEvent).where(
                OutboxEvent.status == "dispatched", OutboxEvent.job_id.in_(unclaimed), OutboxEvent.attempt_count < DISPATCH_LIMIT,
            ).values(status="pending", next_attempt_at=now, last_error="CONSUMER_NOT_STARTED"))
            events = (await session.scalars(select(OutboxEvent).join(BackgroundJob).where(
                OutboxEvent.status == "pending", OutboxEvent.expires_at > now, OutboxEvent.attempt_count < DISPATCH_LIMIT,
                OutboxEvent.event_type == BackgroundJob.job_type, BackgroundJob.job_type.in_(MAIL_TYPES),
                or_(OutboxEvent.next_attempt_at.is_(None), OutboxEvent.next_attempt_at <= now),
                or_(OutboxEvent.lease_expires_at.is_(None), OutboxEvent.lease_expires_at <= now),
                BackgroundJob.status.in_(("pending_dispatch", "queued", "retry_wait")),
            ).order_by(OutboxEvent.created_at).limit(1).with_for_update(skip_locked=True, of=OutboxEvent))).all()
            claims = []
            lease_deadline = await session.scalar(select(now + timedelta(seconds=30)))
            for event in events:
                token = uuid4()
                event.dispatch_token = token
                event.lease_expires_at = lease_deadline
                event.attempt_count += 1
                claims.append(DispatchClaim(event.id, event.job_id, token, event.attempt_count,
                    INVITATION_TASK if event.event_type == INVITATION_JOB else MAIL_TASK))
        return claims

    async def published(self, claim: DispatchClaim):
        async with self.sessions() as session, session.begin():
            now = func.clock_timestamp()
            accepted = await session.scalar(update(OutboxEvent).where(
                OutboxEvent.id == claim.event_id, OutboxEvent.status == "pending", OutboxEvent.dispatch_token == claim.token,
                OutboxEvent.lease_expires_at > now,
            ).values(status="dispatched", dispatched_at=now, last_error=None, dispatch_token=None, lease_expires_at=None).returning(OutboxEvent.id))
            if accepted is not None:
                # A fast consumer may already be running or finished. Do not regress it.
                await session.execute(update(BackgroundJob).where(
                    BackgroundJob.id == claim.job_id, BackgroundJob.status.in_(("pending_dispatch", "queued", "retry_wait")),
                ).values(status="queued", scheduled_at=now))

    async def publish_failed(self, claim: DispatchClaim):
        async with self.sessions() as session, session.begin():
            await session.execute(update(OutboxEvent).where(
                OutboxEvent.id == claim.event_id, OutboxEvent.dispatch_token == claim.token,
                OutboxEvent.status == "pending", OutboxEvent.lease_expires_at > func.clock_timestamp(),
            ).values(dispatch_token=None, lease_expires_at=None, last_error="BROKER_PUBLISH_FAILED",
                     next_attempt_at=func.clock_timestamp() + timedelta(seconds=min(2 ** claim.attempt, 30))))

    async def claim_job(self, job_id: UUID, worker_id: str, max_attempts: int = 3) -> JobClaim | None:
        token = uuid4()
        async with self.sessions() as session, session.begin():
            now = func.clock_timestamp()
            job = await session.scalar(update(BackgroundJob).where(
                BackgroundJob.id == job_id, BackgroundJob.job_type == self.job_type,
                BackgroundJob.status.in_(("pending_dispatch", "queued", "retry_wait")), BackgroundJob.expires_at > now,
                BackgroundJob.attempt_count < max_attempts,
                or_(BackgroundJob.scheduled_at.is_(None), BackgroundJob.scheduled_at <= now),
            ).values(status="running", execution_token=token, worker_id=worker_id[:200],
                     lease_expires_at=now + timedelta(seconds=120), heartbeat_at=now,
                     started_at=now, attempt_count=BackgroundJob.attempt_count + 1,
                     result_ref={"delivery": "preparing"}).returning(BackgroundJob))
            claim = JobClaim(job.id, token, job.payload_ref.copy()) if job else None
        return claim

    async def mark_sending(self, claim: JobClaim) -> bool:
        async with self.sessions() as session, session.begin():
            result = await session.scalar(update(BackgroundJob).where(
                BackgroundJob.id == claim.job_id, BackgroundJob.execution_token == claim.token,
                BackgroundJob.status == "running", BackgroundJob.lease_expires_at > func.clock_timestamp(),
                BackgroundJob.expires_at > func.clock_timestamp(),
            ).values(result_ref={"delivery": "in_flight"}).returning(BackgroundJob.id))
        return result is not None

    async def finish(self, claim: JobClaim, delivery: Delivery, max_attempts: int) -> bool:
        async with self.sessions() as session, session.begin():
            job = await session.scalar(select(BackgroundJob).where(
                BackgroundJob.id == claim.job_id, BackgroundJob.execution_token == claim.token,
                BackgroundJob.status == "running", BackgroundJob.lease_expires_at > func.clock_timestamp(),
            ).with_for_update())
            if job is None:
                return False
            now = await session.scalar(select(func.clock_timestamp()))
            if job.lease_expires_at <= now:
                return False
            retry = delivery.status == "rejected" and delivery.retryable and job.attempt_count < max_attempts and job.expires_at > now + timedelta(seconds=10)
            if retry:
                rearmed = await session.scalar(update(OutboxEvent).where(
                    OutboxEvent.job_id == job.id, OutboxEvent.attempt_count < DISPATCH_LIMIT,
                ).values(status="pending", next_attempt_at=now + timedelta(seconds=10),
                         dispatch_token=None, lease_expires_at=None, last_error="SMTP_RETRY_SCHEDULED").returning(OutboxEvent.id))
                retry = rearmed is not None
            job.status = "retry_wait" if retry else "succeeded" if delivery.status == "accepted" else "cancelled" if delivery.category in {"VERIFICATION_SUPERSEDED", "INVITATION_UNAVAILABLE"} else "failed"
            job.result_ref = {"delivery": delivery.status}
            job.error_summary = None if delivery.status == "accepted" else delivery.category
            job.lease_expires_at = None
            job.scheduled_at = now + timedelta(seconds=10) if retry else job.scheduled_at
            job.finished_at = None if retry else now
        return True
