from uuid import UUID

import anyio

from silencio_set.accounts.errors import AuthError
from silencio_set.mail.payloads import PayloadUnavailable
from silencio_set.mail.smtp import Delivery


class InvitationConsumer:
    def __init__(self, jobs, payloads, invitations, smtp, max_attempts):
        self.jobs, self.payloads, self.invitations = jobs, payloads, invitations
        self.smtp, self.max_attempts = smtp, max_attempts

    async def run(self, job_id, worker_id):
        claim = await self.jobs.claim_job(job_id, worker_id, self.max_attempts)
        if claim is None:
            return
        try:
            payload_id = UUID(claim.payload_ref["payload_id"])
            payload = await self.payloads.read(payload_id)
            if (str(payload.invitation_id) != claim.payload_ref["invitation_id"] or payload.email_digest != claim.payload_ref["email_digest"]
                    or payload.email_digest != self.payloads.cache.digest_identity("email", payload.email_normalized)):
                raise PayloadUnavailable()
            if not await self.invitations.delivery_current(payload):
                await self.jobs.finish(claim, Delivery("not_sent", "INVITATION_UNAVAILABLE"), self.max_attempts)
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
