"""Run explicitly after migrations: python -m silencio_set.notifications.consumer."""
import argparse
import asyncio
import logging

from sqlalchemy.ext.asyncio import async_sessionmaker

from silencio_set.core.config import load_settings
from silencio_set.infrastructure.health import Dependencies
from silencio_set.notifications.repository import NotificationConsumer

logger = logging.getLogger(__name__)


async def run(settings, once=False):
    if settings.database_url is None: raise ValueError("DATABASE_URL is required")
    dependencies = Dependencies(settings)
    consumer = NotificationConsumer(async_sessionmaker(dependencies.database, expire_on_commit=False))
    try:
        while True:
            try:
                processed = await consumer.consume_once()
            except Exception as failure:
                logger.error("notification_consumer_interrupted exception_type=%s", type(failure).__name__)
                if once: raise RuntimeError("Notification cycle failed") from None
                processed = False
            if once: return processed
            await asyncio.sleep(0.25 if processed else 1)
    finally:
        await dependencies.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Create in-app notifications from committed task events")
    parser.add_argument("--once", action="store_true", help="Process at most one event and exit")
    arguments = parser.parse_args()
    settings = load_settings()
    if settings.database_url is None: parser.error("DATABASE_URL is required")
    try:
        asyncio.run(run(settings, arguments.once))
    except KeyboardInterrupt:
        pass
