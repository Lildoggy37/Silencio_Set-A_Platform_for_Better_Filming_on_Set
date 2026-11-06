import asyncio

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import create_async_engine

from silencio_set.core.config import load_settings
from silencio_set.infrastructure.database import Base
# Register the mappings for autogenerate; importing models performs no I/O.
from silencio_set.accounts import models
from silencio_set.jobs import models as job_models
from silencio_set.crews import models as crew_models
from silencio_set.tasks import models as task_models
from silencio_set.notifications import models as notification_models
from silencio_set.availability import models as availability_models
from silencio_set.resources import models as resource_models
from silencio_set.resources import custody_models
from silencio_set.resources import stock
from silencio_set.scripts import models as script_models
from silencio_set.creative import models as creative_models
from silencio_set.plans import models as plan_models
from silencio_set.call_sheets import models as call_sheet_models
from silencio_set.locations import models as location_models
from silencio_set.changes import models as change_models
from silencio_set.diane import proposal_models
from silencio_set.diane import memory_models

settings = load_settings()
if settings.database_url is None:
    raise RuntimeError("DATABASE_URL is required for migrations")
database_url = settings.database_url.get_secret_value()


def include_object(obj, name, type_, reflected, compare_to):
    # These four tables are versioned by AsyncPostgresSaver.setup(), not Alembic.
    return not (type_ == 'table' and name in {
        'checkpoint_migrations', 'checkpoints', 'checkpoint_blobs', 'checkpoint_writes'})


def configure(connection):
    context.configure(connection=connection, target_metadata=Base.metadata, compare_type=True, include_object=include_object)
    with context.begin_transaction():
        context.run_migrations()


async def online():
    engine = create_async_engine(database_url, poolclass=pool.NullPool, hide_parameters=True)
    try:
        async with engine.connect() as connection:
            await connection.run_sync(configure)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    context.configure(url=database_url, target_metadata=Base.metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    asyncio.run(online())
