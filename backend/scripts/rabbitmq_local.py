"""Control the project-local RabbitMQ 4.4 node in the existing Ubuntu WSL."""
import argparse
import asyncio
from hashlib import sha256
import json
import os
from pathlib import Path
from secrets import token_urlsafe
import shlex
import signal
import socket
import subprocess
import sys
import tempfile
import time

LOCAL = Path(__file__).resolve().parents[2] / 'runtime' / 'local-services'


async def probe(config):
    import aio_pika
    from sqlalchemy import URL
    url = URL.create('amqp', username=config['user'], password=config['password'],
        host=config['host'], port=config['port'], database=config['vhost'])
    connection = await aio_pika.connect(url.render_as_string(hide_password=False), timeout=3)
    try:
        properties = connection.transport.connection.server_properties
        print('RabbitMQ AMQP handshake succeeded; version=' + str(properties['version']))
    finally:
        await connection.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['start', 'stop', 'status'])
    action = parser.parse_args().action
    if sys.platform == 'win32':
        path = subprocess.run(['wsl.exe', '-d', 'Ubuntu-22.04', '--exec', 'wslpath', '-a', '-u', __file__],
            capture_output=True, encoding='utf-8', check=True, creationflags=subprocess.CREATE_NO_WINDOW).stdout.strip()
        worker_python = path.rsplit('/backend/', 1)[0] + '/runtime/local-services/worker-venv/bin/python'
        result = subprocess.run(['wsl.exe', '-d', 'Ubuntu-22.04', '--exec', worker_python, path, action],
            capture_output=True, encoding='utf-8', creationflags=subprocess.CREATE_NO_WINDOW, check=False)
        print(result.stdout, end='')
        print(result.stderr, end='', file=sys.stderr)
        return result.returncode

    config_path = LOCAL / 'rabbitmq-local.json'
    folder = LOCAL / 'rabbitmq'
    if action == 'start' and not config_path.exists():
        config_path.write_text(json.dumps({'host':'127.0.0.1', 'port':55672, 'user':'silencio_local',
            'password':token_urlsafe(32), 'vhost':'silencio_local', 'cookie':token_urlsafe(32)}, indent=2))
    config = json.loads(config_path.read_text())
    # Erlang's Cookie permissions and RabbitMQ's runtime/state use Linux's native
    # filesystem; original packages, credentials and logs remain in the project.
    home = Path(tempfile.gettempdir()) / ('silencio-set-rabbitmq-' + sha256(str(LOCAL).encode()).hexdigest()[:12])
    runtime = home.with_name(home.name + '-runtime-rmq4.4.0-otp27.3.4.19')
    erlang = runtime / 'usr/lib/erlang'
    binaries = runtime / 'bin'
    # Keep this test node's Raft/WAL recovery off the slow NTFS mount.
    data = home.with_name(home.name + '-data')
    if action == 'start':
        # Unpack the already-verified archives once into native Linux storage.
        # Repeated BEAM module reads through /mnt are costly on this machine.
        if not (runtime / '.ready').exists():
            runtime.mkdir(mode=0o700, exist_ok=True)
            archives = json.loads((LOCAL / 'downloads/erlang-manifest.json').read_text())
            archives.append(json.loads((LOCAL / 'downloads/rabbitmq-artifact.json').read_text()))
            for archive in archives:
                subprocess.run(['dpkg-deb', '-x', str(LOCAL / 'downloads' / archive['name']), str(runtime)], check=True)
            (runtime / '.ready').touch()
        folder.mkdir(exist_ok=True)
        binaries.mkdir(exist_ok=True)
        home.mkdir(mode=0o700, exist_ok=True)
        cookie = home / '.erlang.cookie'
        cookie.write_text(config['cookie'])
        cookie.chmod(0o600)
        data.mkdir(mode=0o700, exist_ok=True)
        config['linux_data_dir'] = str(data)
        config_path.write_text(json.dumps(config, indent=2))
        # Debian's erl launcher has an absolute /usr prefix. Relocate only this
        # project copy; do not install into /usr or alter the system PATH.
        launcher_text = ('#!/bin/sh\n'
            'ROOTDIR=' + shlex.quote(str(erlang)) + '\n'
            'BINDIR="$ROOTDIR/erts-15.2.7.14/bin"\n'
            'export ROOTDIR BINDIR\nexport EMU=beam PROGNAME=erl\n'
            'exec "$BINDIR/erlexec" "$@"\n')
        launcher = binaries / 'erl'
        launcher.write_text(launcher_text)
        launcher.chmod(0o755)
        (folder / 'rabbitmq.conf').write_text('\n'.join([
            'listeners.tcp.1 = 127.0.0.1:55672', 'distribution.listener.interface = 127.0.0.1',
            'default_user = ' + config['user'], 'default_pass = ' + config['password'],
            'default_vhost = ' + config['vhost'], 'vm_memory_high_watermark.absolute = 256MiB',
            'disk_free_limit.absolute = 100MB', '',
        ]))
        (folder / 'enabled_plugins').write_text('[].\n')
    environment = os.environ.copy()
    environment.update({
        'PATH':str(binaries) + ':' + str(erlang / 'erts-15.2.7.14/bin') + ':' + environment['PATH'],
        'HOME':str(home), 'ERL_EPMD_ADDRESS':'127.0.0.1', 'ERL_EPMD_PORT':'55674',
        'ERL_AFLAGS':'+S 2:2 +A 2',
        # Tell the vendor launcher to exec the VM directly under this supervisor.
        'RUNNING_UNDER_SYSTEMD':'true',
        'ERL_CRASH_DUMP':str(folder / 'erl_crash.dump'),
        'RABBITMQ_NODENAME':'silencio_local@localhost',
        'RABBITMQ_BASE':str(folder), 'RABBITMQ_LOG_BASE':str(folder / 'log'),
        'RABBITMQ_MNESIA_BASE':str(data),
        'RABBITMQ_CONFIG_FILE':str(folder / 'rabbitmq.conf'),
        'RABBITMQ_ENABLED_PLUGINS_FILE':str(folder / 'enabled_plugins'),
        'RABBITMQ_CONF_ENV_FILE':str(folder / 'no-system-env.conf'), 'RABBITMQ_DIST_PORT':'55673',
        'RABBITMQ_SERVER_ADDITIONAL_ERL_ARGS':'+S 2:2 +A 2',
        'RABBITMQ_CTL_ERL_ARGS':'+S 1:1 +A 1',
    })
    scripts = runtime / 'usr/lib/rabbitmq/lib/rabbitmq_server-4.4.0/sbin'
    if action == 'start':
        with socket.socket() as port_check:
            port_check.bind((config['host'], config['port']))
        subprocess.run([str(erlang / 'erts-15.2.7.14/bin/epmd'), '-daemon'], env=environment, check=True)
        with (folder / 'startup.log').open('ab') as log:
            process = subprocess.Popen([str(scripts / 'rabbitmq-server')], env=environment,
                stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        (folder / 'rabbitmq.pid').write_text(str(process.pid))
        # Loading the vendor Erlang archives through WSL's NTFS mount can take
        # longer than 30s on a cold start. A process exit still fails immediately.
        deadline = time.monotonic() + 120
        while True:
            if process.poll() is not None:
                raise RuntimeError('Project RabbitMQ exited; see runtime/local-services/rabbitmq/startup.log')
            try:
                with socket.create_connection((config['host'], config['port']), timeout=1):
                    break
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError('Project RabbitMQ startup timed out') from None
                time.sleep(0.2)
        asyncio.run(probe(config))
        print('Project RabbitMQ started on 127.0.0.1:55672')
    elif action == 'stop':
        pid = int((folder / 'rabbitmq.pid').read_text())
        arguments = Path(f'/proc/{pid}/cmdline').read_bytes()
        process_environment = Path(f'/proc/{pid}/environ').read_bytes()
        if (str(erlang).encode() not in arguments
                or b'RABBITMQ_NODENAME=silencio_local@localhost\0' not in process_environment):
            raise RuntimeError('Matching node is not using the project-local Erlang runtime')
        os.kill(pid, signal.SIGTERM)
        deadline = time.monotonic() + 15
        while Path(f'/proc/{pid}').exists():
            if time.monotonic() >= deadline:
                raise TimeoutError('Project RabbitMQ did not stop in time')
            time.sleep(0.1)
        subprocess.run([str(erlang / 'erts-15.2.7.14/bin/epmd'), '-kill'], env=environment, check=True)
        (home / '.erlang.cookie').unlink()
        home.rmdir()
        print('Project RabbitMQ stopped')
    else:
        asyncio.run(probe(config))
    return 0


if __name__ == '__main__':
    sys.exit(main())
