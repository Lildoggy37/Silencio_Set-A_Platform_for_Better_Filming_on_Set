"""Linux-only worker entry for an isolated mail integration run, never .env."""
import argparse
import json
import os
from pathlib import Path

from silencio_set.core import config


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('settings_file', type=Path)
    args = parser.parse_args()
    for name in config.Settings.model_fields:
        os.environ.pop(name.upper(), None)
    settings = config.Settings(_env_file=None, **json.loads(args.settings_file.read_text()))
    # Task functions import this loader when create_worker registers them.
    config.load_settings = lambda: settings
    from silencio_set.worker import create_worker
    app = create_worker(settings)
    app.worker_main(['worker', '--pool=prefork', '--concurrency=1', '-Q', 'notifications',
        '--loglevel=WARNING'])


if __name__ == '__main__':
    main()
