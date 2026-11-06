"""Serve the latest isolated integration DB for browser checks, with real Redis.

Creates a fresh test account; default mode does not read .env or call a model.
--diane explicitly loads only model settings and enables real model queries.
"""
import argparse
import asyncio
from datetime import UTC, datetime
import json
import os
from pathlib import Path
from secrets import token_urlsafe
from uuid import uuid4

from sqlalchemy import URL
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
import uvicorn

from silencio_set.accounts.models import User
from silencio_set.accounts.passwords import PasswordService
from silencio_set.core.config import PROJECT_ROOT, Settings
from silencio_set.main import create_app

LOCAL = Path(__file__).resolve().parents[2] / 'runtime' / 'local-services'
PASSWORD = 'local browser integration password'


async def seed(settings, email):
    engine = create_async_engine(settings.database_url.get_secret_value(), hide_parameters=True)
    try:
        sessions = async_sessionmaker(engine)
        async with sessions() as session, session.begin():
            session.add(User(email=email, email_normalized=email,
                password_hash=PasswordService().hash(PASSWORD), email_verified_at=datetime.now(UTC)))
    finally:
        await engine.dispose()


def preview_settings(*, diane=False, background=False):
    run = json.loads((LOCAL / 'last-integration-run.json').read_text(encoding='utf-8'))
    if run['exit_code'] != 0 or run['cache'] != 'redis':
        raise SystemExit('First complete postgres_smoke.py --real-redis successfully')
    postgres = json.loads((LOCAL / 'postgres-local.json').read_text(encoding='utf-8-sig'))
    redis = json.loads((LOCAL / 'redis-local.json').read_text(encoding='utf-8'))
    if diane:
        from silencio_set.diane.configuration import model_options
        model = model_options(PROJECT_ROOT / '.env')
    else:
        model = {}
    for name in Settings.model_fields:
        os.environ.pop(name.upper(), None)
    database_url = URL.create('postgresql+asyncpg', username=postgres['user'], password=postgres['password'],
        host=postgres['host'], port=postgres['port'], database=run['database'])
    redis_url = URL.create('redis', username='', password=redis['password'], host=redis['host'],
        port=redis['port'], database=str(redis['db']))
    broker = None
    if background:
        rabbit = json.loads((LOCAL/'rabbitmq-local.json').read_text(encoding='utf-8'))
        broker = URL.create('amqp', username=rabbit['user'], password=rabbit['password'],
            host=rabbit['host'], port=rabbit['port'], database=rabbit['vhost']).render_as_string(hide_password=False)
    return Settings(_env_file=None, database_url=database_url.render_as_string(hide_password=False),
        redis_url=redis_url.render_as_string(hide_password=False), auth_enabled=True,
        auth_secret=token_urlsafe(48), mail_enabled=False, diane_enabled=diane, **model,
        diane_background_enabled=background, celery_broker_url=broker,
        script_storage_root=LOCAL / 'browser-files' / 'scripts',
        location_photo_storage_root=LOCAL / 'browser-files' / 'photos')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--seed-only', action='store_true', help='Create a fresh browser account without starting another API')
    mode.add_argument('--notifications-only', action='store_true', help='Consume in-app notifications in the same isolated database')
    parser.add_argument('--diane', action='store_true', help='Enable real model queries using only LLM_* configuration (legacy QWEN_* supported)')
    parser.add_argument('--diane-background', action='store_true', help='Use the existing local RabbitMQ and a separately started agent worker/dispatcher')
    args = parser.parse_args()
    if args.diane and (args.seed_only or args.notifications_only):
        parser.error('--diane is only available when serving the preview API')
    if args.diane_background and (not args.diane or args.seed_only or args.notifications_only):
        parser.error('--diane-background requires --diane in API mode')
    settings = preview_settings(diane=args.diane, background=args.diane_background)
    if args.notifications_only:
        from silencio_set.notifications.consumer import run
        try:
            asyncio.run(run(settings))
        except KeyboardInterrupt:
            pass
        return
    email = 'browser-' + uuid4().hex + '@example.com'
    asyncio.run(seed(settings, email))
    run = json.loads((LOCAL / 'last-integration-run.json').read_text(encoding='utf-8'))
    (LOCAL / 'browser-fixture.json').write_text(json.dumps({'email':email, 'database':run['database']}), encoding='utf-8')
    if args.seed_only:
        return
    uvicorn.run(create_app(settings), host='127.0.0.1', port=8000, access_log=False,
        proxy_headers=False, log_level='warning', loop='silencio_set.infrastructure.event_loop:new_event_loop')


if __name__ == '__main__':
    main()
