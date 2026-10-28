from celery import Celery
from kombu import Queue

from silencio_set.core.config import Settings, load_settings


def create_worker(settings: Settings | None = None) -> Celery:
    config = settings if settings is not None else load_settings()
    if config.celery_broker_url is None:
        raise RuntimeError("CELERY_BROKER_URL is required to start a worker")
    worker = Celery("silencio_set", broker=config.celery_broker_url.get_secret_value())
    worker.conf.update(
        task_queues=tuple(Queue(name, durable=True) for name in ("notifications", "documents", "agent")),
        task_default_queue="documents",
        task_create_missing_queues=False,
        task_serializer="json",
        accept_content=["json"],
        result_serializer="json",
        task_ignore_result=True,
        result_backend=None,
        worker_prefetch_multiplier=1,
        worker_send_task_events=False,
        broker_connection_retry_on_startup=True,
        broker_connection_max_retries=3,
        broker_transport_options={"confirm_publish": True},
        broker_connection_timeout=3,
        task_publish_retry=False,
        timezone=config.app_timezone,
        enable_utc=True,
    )
    # Late acknowledgement applies only to the guarded mail task, not globally.
    from silencio_set.jobs.tasks import register_mail_task
    register_mail_task(worker)
    return worker
