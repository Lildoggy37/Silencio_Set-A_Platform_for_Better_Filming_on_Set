import asyncio
from time import monotonic
from typing import Literal

import aio_pika
from redis.asyncio import Redis
from redis.backoff import NoBackoff
from redis.asyncio.retry import Retry
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from silencio_set.core.config import Settings

DependencyStatus = Literal["ok", "not_configured", "unavailable"]


class Dependencies:
    """Connection pools live with the application, not with each HTTP request."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.database = (
            create_async_engine(
                settings.database_url.get_secret_value(),
                pool_size=5, max_overflow=0, pool_timeout=1, pool_pre_ping=True,
                hide_parameters=True, connect_args={"timeout": 1, "command_timeout": 1},
            ) if settings.database_url else None
        )
        self.redis = (
            Redis.from_url(
                settings.redis_url.get_secret_value(), max_connections=10,
                socket_connect_timeout=1, socket_timeout=1,
                retry=Retry(NoBackoff(), 0),
            ) if settings.redis_url else None
        )
        self._lock = asyncio.Lock()
        self._cached: dict[str, DependencyStatus] = {}
        self._expires = 0.0

    async def _database_probe(self):
        async with self.database.connect() as connection:
            await connection.execute(text("SELECT 1"))

    async def _redis_probe(self):
        await self.redis.ping()

    async def _broker_probe(self):
        # Complete an AMQP handshake; an open TCP port alone is not readiness.
        connection = await aio_pika.connect(self.settings.celery_broker_url.get_secret_value(), timeout=1)
        await connection.close()

    async def _probe(self, configured, operation) -> DependencyStatus:
        if not configured:
            return "not_configured"
        try:
            await asyncio.wait_for(operation(), timeout=1.5)
            return "ok"
        except Exception:
            # Do not return connection strings, hostnames or exception messages.
            return "unavailable"

    async def report(self) -> dict[str, DependencyStatus]:
        # Coalesce concurrent checks and limit probe traffic. Results may lag by 5s.
        async with self._lock:
            if monotonic() < self._expires:
                return self._cached.copy()
            database, redis, rabbitmq = await asyncio.gather(
                self._probe(self.database is not None, self._database_probe),
                self._probe(self.redis is not None, self._redis_probe),
                self._probe(self.settings.celery_broker_url is not None, self._broker_probe),
            )
            self._cached = {"database": database, "redis": redis, "rabbitmq": rabbitmq}
            self._expires = monotonic() + 5
            return self._cached.copy()

    async def close(self):
        try:
            if self.redis is not None:
                await self.redis.aclose()
        finally:
            if self.database is not None:
                await self.database.dispose()
