"""Celery CLI entry; importing the package itself performs no startup I/O."""

from silencio_set.worker import create_worker

app = create_worker()
