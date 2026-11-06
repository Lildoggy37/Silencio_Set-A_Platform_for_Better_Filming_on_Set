import asyncio
import logging
from uuid import UUID

import anyio

from silencio_set.diane.events import ProgressEvents
from silencio_set.diane.jobs import AGENT_TASK, AgentJobs
from silencio_set.infrastructure.event_loop import new_event_loop

logger = logging.getLogger(__name__)


async def execute_job(service, jobs, events, turn_id):
    execution = await jobs.claim(turn_id, service.settings.diane_timeout_seconds)
    if execution is None: return
    await events.notify(turn_id, execution.turn.progress_version)
    result = None
    try:
        async def progress(phase):
            version = await jobs.progress(execution, phase)
            await events.notify(turn_id, version)
        result = await service.answer_turn(execution.user, execution.crew_id, execution.conversation_id,
            execution.turn, execution.request_id, on_progress=progress)
    except Exception as error:
        logger.warning('diane_job_failed run_id=%s exception_type=%s', turn_id, type(error).__name__)
    finally:
        with anyio.CancelScope(shield=True):
            turn = await jobs.complete(execution, result)
            if turn is not None: await events.notify(turn_id, turn.progress_version)


async def run_job(turn_id, settings):
    # Reuse the application composition and its resource cleanup. No HTTP server.
    from silencio_set.main import create_app
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        await execute_job(app.state.diane, AgentJobs(app.state.diane_conversations),
            ProgressEvents(app.state.dependencies.redis), turn_id)


def register_agent_task(app, settings):
    @app.task(name=AGENT_TASK, acks_late=True, reject_on_worker_lost=False,
        ignore_result=True, max_retries=0, soft_time_limit=150, time_limit=165)
    def answer(turn_id):
        if not settings.diane_background_enabled: return
        try:
            parsed = UUID(turn_id)
            with asyncio.Runner(loop_factory=new_event_loop) as runner:
                runner.run(run_job(parsed, settings))
        except Exception as error:
            logger.error('diane_worker_interrupted exception_type=%s', type(error).__name__)
    return answer
