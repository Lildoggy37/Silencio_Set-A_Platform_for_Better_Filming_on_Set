"""Create a fresh local DB and run explicit HTTP integration tests without .env.

Run from backend: .venv/Scripts/python.exe scripts/postgres_smoke.py
Credentials stay in the ignored runtime/local-services/postgres-local.json.
The dedicated database is retained for inspection; no existing database is reset.
"""
import argparse
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

import asyncpg
from redis import Redis
from sqlalchemy import URL

BACKEND = Path(__file__).resolve().parents[1]
LOCAL = BACKEND.parent / 'runtime' / 'local-services'


async def create_database(config, database):
    connection = await asyncpg.connect(
        host=config['host'], port=config['port'], user=config['user'],
        password=config['password'], database='postgres', timeout=5,
    )
    try:
        # database is generated solely from a fixed prefix and UUID hex.
        await connection.execute(f'CREATE DATABASE "{database}"')
    finally:
        await connection.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--real-redis', action='store_true', help='Require the isolated local Redis, with no cache double')
    args = parser.parse_args()
    environment = os.environ.copy()
    cache_version = None
    if args.real_redis:
        cache_config = json.loads((LOCAL / 'redis-local.json').read_text(encoding='utf-8'))
        with Redis(**cache_config, socket_connect_timeout=3, socket_timeout=3) as client:
            assert client.ping()
            cache_version = client.info('server')['redis_version']
        cache_url = URL.create('redis', username='', password=cache_config['password'], host=cache_config['host'],
            port=cache_config['port'], database=str(cache_config['db']))
        environment['SILENCIO_TEST_REDIS_URL'] = cache_url.render_as_string(hide_password=False)
    else:
        environment.pop('SILENCIO_TEST_REDIS_URL', None)
    config = json.loads((LOCAL / 'postgres-local.json').read_text(encoding='utf-8-sig'))
    if config['host'] not in {'127.0.0.1', 'localhost', '::1'}:
        raise SystemExit('This runner only creates databases on loopback PostgreSQL')
    database = 'silencio_it_' + uuid4().hex
    asyncio.run(create_database(config, database))
    url = URL.create('postgresql+asyncpg', username=config['user'], password=config['password'],
        host=config['host'], port=config['port'], database=database)
    environment['SILENCIO_TEST_DATABASE_URL'] = url.render_as_string(hide_password=False)
    environment['PYTHONIOENCODING'] = 'utf-8'
    (LOCAL / 'test-temp').mkdir(exist_ok=True)
    print(f'Isolated PostgreSQL database: {database}', flush=True)
    print(f'Cache: Redis {cache_version}' if args.real_redis else 'Cache: fakeredis', flush=True)
    result = subprocess.run([sys.executable, '-m', 'pytest', 'integration_tests', '-q', '--tb=short',
        '-p', 'no:cacheprovider', '--basetemp', str(LOCAL / 'test-temp' / database)],
        cwd=BACKEND, env=environment, check=False)
    (LOCAL / 'last-integration-run.json').write_text(json.dumps({
        'database': database, 'exit_code': result.returncode,
        'cache': 'redis' if args.real_redis else 'fakeredis', 'redis_version':cache_version,
        'mail_and_broker_verified': False,
    }, indent=2), encoding='utf-8')
    return result.returncode


if __name__ == '__main__':
    sys.exit(main())
