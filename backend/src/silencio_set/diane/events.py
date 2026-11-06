"""Redis is a wake-up signal; every SSE payload is read and authorized in PG."""
import json
import logging

from redis.exceptions import RedisError

from silencio_set.accounts.errors import AuthError

logger = logging.getLogger(__name__)


def channel(turn_id): return 'diane:progress:'+str(turn_id)


class ProgressEvents:
    def __init__(self, redis): self.redis = redis

    async def notify(self, turn_id, version):
        try:
            await self.redis.publish(channel(turn_id), str(version))
        except RedisError:
            # A lost wake-up must not turn a saved answer into a failed run.
            logger.warning('diane_progress_notification_unavailable run_id=%s', turn_id)

    async def stream(self, repository, user, crew_id, conversation_id, turn_id, reauthorize):
        version = None
        # Subscribe before the initial read, avoiding the read/subscribe race.
        async with self.redis.pubsub() as subscription:
            await subscription.subscribe(channel(turn_id))
            while True:
                try:
                    await reauthorize()
                    turn = await repository.get_turn(user, crew_id, conversation_id, turn_id)
                except AuthError as error:
                    yield 'event: access_revoked\ndata: '+json.dumps({'code':error.code})+'\n\n'
                    return
                if turn.progress_version != version:
                    version = turn.progress_version
                    yield f'id: {version}\nevent: turn\ndata: {turn.model_dump_json()}\n\n'
                if turn.status not in {'queued', 'running'}: return
                # Recheck authority and PG state at least every 10s, even if a
                # publisher/connection lost a Redis notification. No model retry.
                await subscription.get_message(ignore_subscribe_messages=True, timeout=10)
                yield ': keep-alive\n\n'
