"""Control an isolated Redis in the existing Ubuntu WSL; no Docker or service install."""
import argparse
import json
from pathlib import Path
from secrets import token_urlsafe
import subprocess
import sys

from redis import Redis

LOCAL = Path(__file__).resolve().parents[2] / 'runtime' / 'local-services'
CONFIG = LOCAL / 'redis-local.json'
DISTRO = 'Ubuntu-22.04'


def wsl(*arguments, **options):
    return subprocess.run(['wsl.exe', '-d', DISTRO, '--exec', *arguments],
        creationflags=subprocess.CREATE_NO_WINDOW, check=True, **options)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['start', 'stop', 'status'])
    action = parser.parse_args().action
    if action == 'start' and not CONFIG.exists():
        CONFIG.write_text(json.dumps({'host':'127.0.0.1', 'port':56379,
            'password':token_urlsafe(32), 'db':0}, indent=2), encoding='utf-8')
    config = json.loads(CONFIG.read_text(encoding='utf-8'))
    if action == 'start':
        folder = LOCAL / 'redis'
        folder.mkdir(exist_ok=True)
        linux_folder = wsl('wslpath', '-a', '-u', str(folder), capture_output=True, encoding='utf-8').stdout.strip()
        conf = '\n'.join([
            'bind 127.0.0.1', f"port {config['port']}", 'protected-mode yes',
            f"requirepass {config['password']}", 'daemonize yes', 'save ""', 'appendonly no',
            'maxmemory 64mb', 'maxmemory-policy noeviction',
            'pidfile ' + json.dumps(linux_folder + '/redis.pid', ensure_ascii=False),
            'logfile ' + json.dumps(linux_folder + '/redis.log', ensure_ascii=False),
            'dir ' + json.dumps(linux_folder, ensure_ascii=False), '',
        ])
        (folder / 'redis.conf').write_text(conf, encoding='utf-8')
        wsl(linux_folder.rsplit('/', 1)[0] + '/linux-runtime/usr/bin/redis-server', linux_folder + '/redis.conf')
        print('Local WSL Redis started on 127.0.0.1:56379 (no persistence)')
    else:
        with Redis(**config, socket_connect_timeout=3, socket_timeout=3) as client:
            if action == 'stop':
                client.shutdown(nosave=True)
                print('Local WSL Redis stopped')
            else:
                assert client.ping()
                print('Local WSL Redis ' + client.info('server')['redis_version'] + ' is reachable from Windows')
    return 0


if __name__ == '__main__':
    sys.exit(main())
