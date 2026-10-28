from dataclasses import dataclass
from uuid import UUID, uuid4

import anyio

from silencio_set.accounts.cache import AuthCache
from silencio_set.accounts.errors import AuthError
from silencio_set.accounts.identity import normalize_email
from silencio_set.jobs.repository import MailJobs
from silencio_set.mail.payloads import EncryptedPayloads, PayloadUnavailable, VerificationMail
from silencio_set.mail.smtp import Delivery, SmtpAdapter


@dataclass(frozen=True)
class MailAcceptance:
    verification_id: UUID
    expires_at: int


class MailRequests:
    def __init__(self, cache: AuthCache, payloads: EncryptedPayloads, jobs: MailJobs):
        self.cache, self.payloads, self.jobs = cache, payloads, jobs

    async def request(self, email: str, ip: str) -> MailAcceptance:
        issued = await self.cache.issue_verification(email, ip)
        identity = normalize_email(email)
        payload = VerificationMail(
            payload_id=uuid4(), verification_id=issued.verification_id, email=identity.display,
            email_normalized=identity.normalized, email_digest=self.cache.digest_identity("email", identity.normalized),
            code=issued.code.get_secret_value(), expires_at=issued.expires_at,
        )
        try:
            await self.payloads.save(payload)
            await self.jobs.enqueue(issued.verification_id, payload.payload_id, payload.email_digest, issued.expires_at)
        except PayloadUnavailable:
            raise AuthError(503, "EMAIL_SERVICE_UNAVAILABLE", "邮件验证准备失败，请稍后重试") from None
        # Do not delete Redis state on an uncertain DB commit: the outbox may
        # exist. Orphaned state expires; clients can request again after cooldown.
        return MailAcceptance(issued.verification_id, issued.expires_at)


class MailConsumer:
    def __init__(self, jobs: MailJobs, payloads: EncryptedPayloads, smtp: SmtpAdapter, max_attempts: int):
        self.jobs, self.payloads, self.smtp, self.max_attempts = jobs, payloads, smtp, max_attempts

    async def run(self, job_id: UUID, worker_id: str):
        claim = await self.jobs.claim_job(job_id, worker_id, self.max_attempts)
        if claim is None:
            return
        try:
            payload_id = UUID(claim.payload_ref["payload_id"])
            payload = await self.payloads.read(payload_id)
            if (str(payload.verification_id) != claim.payload_ref["verification_id"]
                    or payload.email_digest != claim.payload_ref["email_digest"]):
                raise PayloadUnavailable()
            if not await self.payloads.current(payload):
                await self.jobs.finish(claim, Delivery("not_sent", "VERIFICATION_SUPERSEDED"), self.max_attempts)
                return
        except (PayloadUnavailable, KeyError, ValueError, TypeError, AuthError):
            await self.jobs.finish(claim, Delivery("not_sent", "PAYLOAD_UNAVAILABLE"), self.max_attempts)
            return
        if not await self.jobs.mark_sending(claim):
            return
        delivery = await anyio.to_thread.run_sync(self.smtp.send, job_id, payload)
        saved = await self.jobs.finish(claim, delivery, self.max_attempts)
        if saved and delivery.status == "accepted":
            await self.payloads.cleanup(payload_id)
