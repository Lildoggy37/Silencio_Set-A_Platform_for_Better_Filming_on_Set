"""Run one Agent background component against the existing local trial DB."""
import argparse
import asyncio
import sys

from browser_preview import preview_settings
from silencio_set.diane.dispatcher import run
from silencio_set.worker import create_worker


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('component',choices=['dispatcher','worker'])
    args = parser.parse_args()
    settings = preview_settings(diane=True,background=True)
    if args.component=='dispatcher':
        try: asyncio.run(run(settings))
        except KeyboardInterrupt: pass
    else:
        app = create_worker(settings)
        try:
            app.worker_main(['worker','--pool=solo' if sys.platform=='win32' else '--pool=prefork',
                '--concurrency=1','-Q','agent','--loglevel=WARNING','--without-gossip','--without-mingle'])
        finally: app.close()


if __name__=='__main__': main()
