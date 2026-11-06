"""Control the existing project-local PostgreSQL instance, without a system service."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

LOCAL = Path(__file__).resolve().parents[2] / 'runtime' / 'local-services'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['start', 'stop', 'status'])
    args = parser.parse_args()
    config = json.loads((LOCAL / 'postgres-local.json').read_text(encoding='utf-8-sig'))
    # The initialized data path uses an ASCII directory junction on Windows.
    data = Path(config['data_dir'])
    pg_ctl = data.parent / 'postgresql-17.11' / 'pgsql' / 'bin' / 'pg_ctl.exe'
    command = [str(pg_ctl), '-D', str(data)]
    if args.action == 'start':
        command += ['-l', str(data.parent / 'postgres-server.log'), 'start', '-w']
    elif args.action == 'stop':
        command += ['stop', '-m', 'fast', '-w']
    else:
        command += ['status']
    return subprocess.run(command, creationflags=subprocess.CREATE_NO_WINDOW, check=False).returncode


if __name__ == '__main__':
    sys.exit(main())
