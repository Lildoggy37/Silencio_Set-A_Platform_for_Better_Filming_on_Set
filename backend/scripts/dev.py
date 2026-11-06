"""Start one development component using the existing application entry points."""
import argparse
import os
from pathlib import Path
import subprocess
import sys


BACKEND = Path(__file__).resolve().parents[1]
WORKER = ['-m', 'celery', '-A', 'silencio_set.worker_entry:app', 'worker',
          '--pool=prefork', '--concurrency=1', '-Q', 'notifications', '--loglevel=WARNING']
COMMANDS = {
    'api': ['-m', 'uvicorn', 'silencio_set.main:create_app', '--factory',
            '--loop', 'silencio_set.infrastructure.event_loop:new_event_loop',
            '--host', '127.0.0.1', '--port', '8000', '--no-access-log', '--no-proxy-headers'],
    'notifications': ['-m', 'silencio_set.notifications.consumer'],
    'dispatcher': ['-m', 'silencio_set.jobs.dispatcher'],
    'worker': WORKER,
    'agent-dispatcher': ['-m','silencio_set.diane.dispatcher'],
    'agent-worker': ['-m','celery','-A','silencio_set.worker_entry:app','worker',
        '--pool=solo' if sys.platform=='win32' else '--pool=prefork',
        '--concurrency=1','-Q','agent','--loglevel=WARNING','--without-gossip','--without-mingle'],
    'migrate': ['-m', 'alembic', 'upgrade', 'head'],
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('component', choices=COMMANDS,
                        help='Run in a separate terminal. Ctrl+C stops that component.')
    parser.add_argument('--distro', default='Ubuntu-22.04',
                        help='WSL distribution for the Windows mail worker')
    args = parser.parse_args()
    command = [sys.executable, *COMMANDS[args.component]]
    environment = os.environ.copy()
    if args.component == 'worker' and sys.platform == 'win32':
        from silencio_set.core.config import Settings

        wsl = ['wsl.exe', '--distribution', args.distro]
        linux_backend = subprocess.run(
            [*wsl, '--exec', 'wslpath', '-a', '-u', str(BACKEND)],
            capture_output=True, text=True, encoding='utf-8', check=True,
        ).stdout.strip()
        linux_python = linux_backend.rsplit('/backend', 1)[0] + '/runtime/local-services/worker-venv/bin/python'
        # Forward explicit configuration overrides by name, without putting
        # credential values in process arguments or printing the configuration.
        overrides = [name.upper() for name in Settings.model_fields if name.upper() in environment]
        environment['WSLENV'] = ':'.join(filter(None, [environment.get('WSLENV'), *overrides]))
        command = [*wsl, '--cd', linux_backend, '--exec', linux_python, *WORKER]
    print(f'Starting {args.component}. Use Ctrl+C in this terminal to stop.', flush=True)
    try:
        return subprocess.run(command, cwd=BACKEND, env=environment, check=False).returncode
    except KeyboardInterrupt:
        return 130


if __name__ == '__main__':
    sys.exit(main())
