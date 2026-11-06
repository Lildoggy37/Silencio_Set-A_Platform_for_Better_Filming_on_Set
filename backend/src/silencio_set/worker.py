from celery import Celery
from kombu import Queue

from silencio_set.core.config import Settings, load_settings


def create_worker(settings: Settings | None = None) -> Celery:
    config = settings if settings is not None else load_settings()
    if config.celery_broker_url is None:
        raise RuntimeError("CELERY_BROKER_URL is required to start a worker")
    worker = Celery("silencio_set", broker=config.celery_broker_url.get_secret_value())
    worker.conf.update(
        task_queues=(Queue('notifications', durable=True, routing_key='notifications'),
            Queue('documents', durable=True, routing_key='documents'),
            Queue('agent', durable=True, routing_key='agent', queue_arguments={'x-queue-type':'quorum'})),
        # Celery detects the Agent quorum queue and uses per-consumer prefetch;
        # the local RabbitMQ 4.4 no longer permits the old global QoS default.
        worker_detect_quorum_queues=True,
        task_default_queue="documents",
        task_create_missing_queues=False,
        task_serializer="json",
        accept_content=["json"],
        result_serializer="json",
        task_ignore_result=True,
        result_backend=None,
        worker_prefetch_multiplier=1,
        worker_send_task_events=False,
        # RabbitMQ 4.3+ rejects transient non-exclusive queues. Control and
        # event queues belong to a connection; business task queues stay durable.
        control_queue_exclusive=True,
        event_queue_exclusive=True,
        broker_connection_retry_on_startup=True,
        broker_connection_max_retries=3,
        broker_transport_options={"confirm_publish": True},
        broker_connection_timeout=3,
        task_publish_retry=False,
        timezone=config.app_timezone,
        enable_utc=True,
    )
    # Late acknowledgement is enabled by guarded task definitions, not globally.
    from silencio_set.jobs.tasks import register_mail_task
    register_mail_task(worker)
    from silencio_set.diane.worker import register_agent_task
    register_agent_task(worker, config)
    return worker
