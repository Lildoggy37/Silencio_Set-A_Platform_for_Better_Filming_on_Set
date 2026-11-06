"""A bounded psycopg pool for the LangGraph PostgreSQL checkpointer."""
from contextlib import asynccontextmanager

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from sqlalchemy.engine import make_url


@asynccontextmanager
async def checkpoint_store(settings):
    url = make_url(settings.database_url.get_secret_value())
    # Pass credentials as libpq fields, preserving special characters safely.
    conninfo = make_conninfo(host=url.host, port=url.port or 5432, user=url.username,
        password=url.password, dbname=url.database, **dict(url.query))
    async with AsyncConnectionPool(conninfo, min_size=1, max_size=settings.diane_concurrency,
        timeout=3, kwargs={'autocommit': True, 'prepare_threshold': 0, 'row_factory': dict_row}) as pool:
        saver = AsyncPostgresSaver(pool)
        await saver.setup()
        yield saver
