"""Opt-in PostgreSQL tests; real Redis is selected explicitly by the runner."""
import os
from secrets import token_urlsafe

from alembic import command
from alembic.config import Config
from fakeredis.aioredis import FakeRedis
from fastapi.testclient import TestClient
from pydantic import SecretStr
import pytest

from silencio_set.core import config as config_module
from silencio_set.core.config import Settings
from silencio_set.infrastructure.health import Dependencies
from silencio_set import main
from silencio_set.infrastructure.event_loop import new_event_loop


@pytest.fixture
def postgres_client(monkeypatch, tmp_path):
    configured_url = os.environ.get('SILENCIO_TEST_DATABASE_URL')
    if not configured_url:
        pytest.skip('Run scripts/postgres_smoke.py to create an isolated PostgreSQL test database')
    database_url = SecretStr(configured_url)
    configured_redis = os.environ.get('SILENCIO_TEST_REDIS_URL')
    # Never inherit local production settings or load the repository .env.
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
    settings = Settings(
        _env_file=None, app_env='test', database_url=database_url,
        redis_url=SecretStr(configured_redis) if configured_redis else 'redis://127.0.0.1:1/0', auth_enabled=True,
        auth_secret=token_urlsafe(48), mail_enabled=False, diane_enabled=False,
        script_storage_root=tmp_path / 'scripts', location_photo_storage_root=tmp_path / 'photos',
    )
    with monkeypatch.context() as migration_patch:
        migration_patch.setattr(config_module, 'load_settings', lambda: settings)
        command.upgrade(Config('alembic.ini'), 'head')

    class PostgresWithTestCache(Dependencies):
        def __init__(self, config):
            super().__init__(config)
            self.unused_redis = self.redis
            self.redis = FakeRedis()

        async def close(self):
            await super().close()
            await self.unused_redis.aclose()

    if not configured_redis:
        monkeypatch.setattr(main, 'Dependencies', PostgresWithTestCache)
    app = main.create_app(settings)
    with TestClient(app, base_url='http://127.0.0.1:8000', backend_options={'loop_factory':new_event_loop}) as client:
        client.headers['Origin'] = 'http://127.0.0.1:5173'
        yield client
