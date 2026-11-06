"""Run a real broker/SMTP/registration journey in the project-local Linux venv."""
import asyncio
import json
import os
from pathlib import Path
from secrets import token_urlsafe
import subprocess
import sys
from uuid import uuid4

from cryptography.fernet import Fernet
from sqlalchemy import URL

from postgres_smoke import create_database

BACKEND = Path(__file__).resolve().parents[1]
LOCAL = BACKEND.parent / 'runtime' / 'local-services'


def main():
    if sys.platform == 'win32':
        path = subprocess.run(['wsl.exe', '-d', 'Ubuntu-22.04', '--exec', 'wslpath', '-a', '-u', __file__],
            capture_output=True, encoding='utf-8', check=True, creationflags=subprocess.CREATE_NO_WINDOW).stdout.strip()
        python = path.rsplit('/backend/', 1)[0] + '/runtime/local-services/worker-venv/bin/python'
        result = subprocess.run(['wsl.exe', '-d', 'Ubuntu-22.04', '--exec', python, path],
            capture_output=True, encoding='utf-8', creationflags=subprocess.CREATE_NO_WINDOW)
        print(result.stdout, end='')
        print(result.stderr, end='', file=sys.stderr)
        return result.returncode
    database = 'silencio_mail_it_' + uuid4().hex
    folder = LOCAL / 'mail-runs' / database
    folder.mkdir(parents=True)
    postgres = json.loads((LOCAL / 'postgres-local.json').read_text(encoding='utf-8-sig'))
    redis = json.loads((LOCAL / 'redis-local.json').read_text())
    broker = json.loads((LOCAL / 'rabbitmq-local.json').read_text())
    asyncio.run(create_database(postgres, database))
    settings = {
        'app_env':'test', 'auth_enabled':True, 'mail_enabled':True, 'diane_enabled':False,
        'auth_secret':token_urlsafe(48), 'mail_payload_key':Fernet.generate_key().decode(),
        'database_url':URL.create('postgresql+asyncpg', username=postgres['user'], password=postgres['password'],
            host=postgres['host'], port=postgres['port'], database=database).render_as_string(hide_password=False),
        'redis_url':URL.create('redis', username='', password=redis['password'], host=redis['host'],
            port=redis['port'], database=str(redis['db'])).render_as_string(hide_password=False),
        'celery_broker_url':URL.create('amqp', username=broker['user'], password=broker['password'],
            host=broker['host'], port=broker['port'], database=broker['vhost']).render_as_string(hide_password=False),
        'script_storage_root':str(folder / 'scripts'), 'location_photo_storage_root':str(folder / 'photos'),
    }
    file = folder / 'settings.json'
    file.write_text(json.dumps(settings))
    environment = os.environ.copy()
    environment['SILENCIO_MAIL_TEST_SETTINGS'] = str(file)
    environment['PYTHONIOENCODING'] = 'utf-8'
    print('Isolated mail integration database: ' + database, flush=True)
    result = subprocess.run([sys.executable, '-m', 'pytest', 'integration_tests/test_mail_delivery.py',
        '-q', '--tb=short', '-p', 'no:cacheprovider', '--basetemp', str(folder / 'temp')],
        cwd=BACKEND, env=environment, check=False)
    (LOCAL / 'last-mail-run.json').write_text(json.dumps({'database':database,
        'exit_code':result.returncode, 'worker':'linux-prefork', 'smtp':'loopback-test-inbox'}, indent=2))
    return result.returncode


if __name__ == '__main__':
    sys.exit(main())
