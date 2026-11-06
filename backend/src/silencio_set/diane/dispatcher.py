import asyncio
import logging

import anyio
from sqlalchemy.ext.asyncio import async_sessionmaker

from silencio_set.core.config import load_settings
from silencio_set.diane.jobs import AGENT_TASK, AgentJobs
from silencio_set.diane.memory import ConversationRepository
from silencio_set.infrastructure.health import Dependencies
from silencio_set.worker import create_worker

logger = logging.getLogger(__name__)


class AgentPublisher:
    def __init__(self, app): self.app = app

    def publish(self, claim):
        self.app.send_task(AGENT_TASK, args=[str(claim.turn_id)], task_id=str(claim.turn_id),
            queue='agent', serializer='json', delivery_mode=2, ignore_result=True,
            retry=False, timeout=5, confirm_timeout=5, argsrepr='(<run_id>,)', kwargsrepr='{}')


async def dispatch_once(jobs, publisher):
    claims = await jobs.outbox()
    for claim in claims:
        try:
            await anyio.to_thread.run_sync(publisher.publish, claim)
        except Exception as error:
            # Retain the queued row; the 10s outbox lease permits delivery retry.
            # Publishing can be ambiguous, so worker execution has a separate claim.
            logger.warning('diane_publish_failed run_id=%s exception_type=%s', claim.turn_id, type(error).__name__)
        else:
            await jobs.published(claim)
    return len(claims)


async def run(settings):
    if not settings.diane_background_enabled: raise RuntimeError('DIANE_BACKGROUND_ENABLED is required')
    dependencies, app = Dependencies(settings), create_worker(settings)
    jobs = AgentJobs(ConversationRepository(async_sessionmaker(dependencies.database, expire_on_commit=False)))
    try:
        while True:
            try:
                count = await dispatch_once(jobs, AgentPublisher(app))
            except Exception as error:
                logger.warning('diane_dispatch_interrupted exception_type=%s', type(error).__name__)
                count = 0
            await asyncio.sleep(.25 if count else 1)
    finally:
        app.close()
        await dependencies.close()


if __name__ == '__main__':
    try: asyncio.run(run(load_settings()))
    except KeyboardInterrupt: pass
