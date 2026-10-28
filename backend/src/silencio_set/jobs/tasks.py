import asyncio
import logging
import os
from uuid import UUID

from silencio_set.core.config import load_settings
from silencio_set.jobs.repository import MAIL_TASK, INVITATION_TASK
from silencio_set.mail.runtime import mail_runtime, invitation_mail_runtime

logger = logging.getLogger(__name__)


async def run_mail_job(job_id: UUID, settings):
    async with mail_runtime(settings) as (_, consumer):
        await consumer.run(job_id, f"mail:{os.getpid()}")


async def run_invitation_job(job_id: UUID, settings):
    async with invitation_mail_runtime(settings) as consumer:
        await consumer.run(job_id, f"invitation-mail:{os.getpid()}")


def register_mail_task(app):
    @app.task(name=INVITATION_TASK, acks_late=True, reject_on_worker_lost=False,
              ignore_result=True, max_retries=0, soft_time_limit=55, time_limit=60)
    def send_invitation(job_id: str):
        try:
            parsed_id = UUID(job_id)
        except (ValueError, TypeError, AttributeError):
            return
        try:
            settings = load_settings()
            if settings.mail_enabled:
                asyncio.run(run_invitation_job(parsed_id, settings))
        except Exception as exc:
            logger.error("invitation_job_interrupted job_id=%s exception_type=%s", parsed_id, type(exc).__name__)

    @app.task(name=MAIL_TASK, acks_late=True, reject_on_worker_lost=False,
              ignore_result=True, max_retries=0, soft_time_limit=55, time_limit=60)
    def send_verification(job_id: str):
        try:
            parsed_id = UUID(job_id)
        except (ValueError, TypeError, AttributeError):
            return
        try:
            settings = load_settings()
            if not settings.mail_enabled:
                return
            asyncio.run(run_mail_job(parsed_id, settings))
        except Exception as exc:
            # The database remains authoritative. No blanket SMTP retry and no
            # exception body/SMTP arguments in logs. Dispatch reconciliation and
            # lease expiry handle interruption conservatively.
            logger.error("mail_job_interrupted job_id=%s exception_type=%s", parsed_id, type(exc).__name__)
    return send_verification
