import argparse
import asyncio
import logging

import anyio

from silencio_set.core.config import load_settings
from silencio_set.jobs.repository import MAIL_TASK, INVITATION_TASK
from silencio_set.mail.runtime import mail_runtime
from silencio_set.worker import create_worker

logger = logging.getLogger(__name__)


class CeleryPublisher:
    def __init__(self, app):
        self.app = app

    def publish(self, claim):
        if claim.task_name not in {MAIL_TASK, INVITATION_TASK}:
            raise ValueError("Unsupported mail task")
        # Only UUID references leave the database. No recipient, code, payload
        # ciphertext or decryption key enters a RabbitMQ message.
        self.app.send_task(
            claim.task_name, args=[str(claim.job_id)], task_id=str(claim.event_id),
            queue="notifications", serializer="json", delivery_mode=2, ignore_result=True,
            retry=False, timeout=5, confirm_timeout=5,
            argsrepr="(<job_id>,)", kwargsrepr="{}",
        )


async def dispatch_once(jobs, publisher) -> int:
    claims = await jobs.claim_outbox()
    for claim in claims:
        try:
            await anyio.to_thread.run_sync(publisher.publish, claim)
        except Exception as exc:
            logger.warning("mail_publish_failed exception_type=%s", type(exc).__name__)
            await jobs.publish_failed(claim)
        else:
            # If confirmation succeeded but this commit fails, do not pretend
            # publishing failed; a later lease retry may deliver a duplicate.
            await jobs.published(claim)
    return len(claims)


async def run(settings, once: bool = False):
    app = create_worker(settings)
    try:
        async with mail_runtime(settings) as (jobs, _):
            while True:
                try:
                    count = await dispatch_once(jobs, CeleryPublisher(app))
                except Exception as exc:
                    logger.error("mail_dispatch_interrupted exception_type=%s", type(exc).__name__)
                    count = 0
                if once:
                    return
                await asyncio.sleep(0.25 if count else 1)
    finally:
        app.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Dispatch durable mail outbox events")
    parser.add_argument("--once", action="store_true", help="Process one cycle then exit")
    args = parser.parse_args()
    config = load_settings()
    if not config.mail_enabled:
        parser.error("MAIL_ENABLED must be explicitly configured before dispatching")
    try:
        asyncio.run(run(config, once=args.once))
    except KeyboardInterrupt:
        pass
