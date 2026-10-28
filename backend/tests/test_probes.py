import asyncio

from silencio_set.infrastructure.health import Dependencies
from silencio_set.core.config import Settings


def test_probes_coalesce_and_return_independent_reports(settings):
    async def scenario():
        resources = Dependencies(settings)
        calls = 0

        async def probe(configured, operation):
            nonlocal calls
            calls += 1
            await asyncio.sleep(0)
            return "ok"

        resources._probe = probe
        first, second = await asyncio.gather(resources.report(), resources.report())
        assert calls == 3
        first["database"] = "unavailable"
        assert second["database"] == "ok"
        resources._expires = 0
        await resources.report()
        assert calls == 6
        await resources.close()

    asyncio.run(scenario())


def test_probe_failure_is_redacted_and_not_silently_successful(settings):
    async def scenario():
        resources = Dependencies(settings)

        async def failure():
            raise RuntimeError("sensitive connection URL")

        assert await resources._probe(True, failure) == "unavailable"
        assert await resources._probe(False, failure) == "not_configured"
        await resources.close()

    asyncio.run(scenario())


def test_probe_timeout_has_a_bound(settings):
    async def scenario():
        resources = Dependencies(settings)
        cancelled = False

        async def slow():
            nonlocal cancelled
            try:
                await asyncio.sleep(60)
            finally:
                cancelled = True

        assert await resources._probe(True, slow) == "unavailable"
        assert cancelled
        await resources.close()

    asyncio.run(scenario())


def test_configured_connection_pools_construct_and_close_without_connecting():
    async def scenario():
        settings = Settings(
            _env_file=None, database_url="postgresql+asyncpg://user:example@localhost:5432/test",
            redis_url="redis://localhost:6379/0", celery_broker_url="amqp://user:example@localhost:5672/test",
        )
        resources = Dependencies(settings)
        assert resources.database.url.drivername == "postgresql+asyncpg"
        assert resources.redis is not None
        await resources.close()
    asyncio.run(scenario())
