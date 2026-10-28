import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from silencio_set.accounts.errors import AuthError
from silencio_set.jobs.dispatcher import CeleryPublisher, dispatch_once
from silencio_set.jobs.models import BackgroundJob, OutboxEvent
from silencio_set.jobs.repository import DispatchClaim, JobClaim, MailJobs, MAIL_TASK
from silencio_set.mail.smtp import Delivery
from silencio_set.worker import create_worker
from silencio_set.core.config import Settings
from test_account_repository import fake_session_factory


def test_enqueue_atomically_persists_only_opaque_references():
    factory, session, transaction = fake_session_factory()
    now = datetime.now(UTC)
    session.scalar.return_value = now
    job_id = asyncio.run(MailJobs(factory).enqueue(uuid4(), uuid4(), "a" * 64, int(now.timestamp()) + 300))
    job, event = [call.args[0] for call in session.add.call_args_list]
    assert isinstance(job, BackgroundJob) and isinstance(event, OutboxEvent)
    assert job.id == event.job_id == job_id
    assert set(job.payload_ref) == {"payload_id", "verification_id", "email_digest"}
    assert event.payload_ref == {"job_id": str(job_id)}
    assert session.flush.await_count == 2
    transaction.__aexit__.assert_awaited_once()


def test_expired_request_cannot_be_durably_accepted():
    factory, session, _ = fake_session_factory()
    session.scalar.return_value = datetime.now(UTC)
    with pytest.raises(AuthError):
        asyncio.run(MailJobs(factory).enqueue(uuid4(), uuid4(), "a" * 64, 1))
    session.add.assert_not_called()


def test_claim_uses_skip_locked_and_sets_lease_pair_before_commit():
    factory, session, transaction = fake_session_factory()
    event = OutboxEvent(id=uuid4(), job_id=uuid4(), attempt_count=0)
    rows = Mock()
    rows.all.return_value = [event]
    session.scalars.return_value = rows
    session.scalar.return_value = datetime.now(UTC) + timedelta(seconds=30)
    claims = asyncio.run(MailJobs(factory).claim_outbox())
    sql = str(session.scalars.call_args.args[0].compile(dialect=postgresql.dialect()))
    assert "FOR UPDATE OF outbox_events SKIP LOCKED" in sql
    assert claims[0].token == event.dispatch_token
    assert event.lease_expires_at is not None and event.attempt_count == 1
    transaction.__aexit__.assert_awaited_once()


def test_published_receipt_cannot_regress_running_or_completed_job():
    factory, session, _ = fake_session_factory()
    session.scalar.return_value = uuid4()
    claim = DispatchClaim(uuid4(), uuid4(), uuid4(), 1)
    asyncio.run(MailJobs(factory).published(claim))
    statement = session.execute.call_args.args[0]
    sql = str(statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
    assert "'pending_dispatch', 'queued', 'retry_wait'" in sql
    assert "'running'" not in sql and "'succeeded'" not in sql


def test_job_claim_is_conditional_and_uses_database_time():
    factory, session, _ = fake_session_factory()
    session.scalar.return_value = None
    assert asyncio.run(MailJobs(factory).claim_job(uuid4(), "test-worker")) is None
    sql = str(session.scalar.call_args.args[0].compile(dialect=postgresql.dialect()))
    assert "clock_timestamp()" in sql
    assert "background_jobs.attempt_count <" in sql
    assert "background_jobs.status IN" in sql


@pytest.mark.parametrize("delivery,status", [
    (Delivery("accepted", "SMTP_ACCEPTED"), "succeeded"),
    (Delivery("unknown", "DELIVERY_UNKNOWN"), "failed"),
    (Delivery("not_sent", "VERIFICATION_SUPERSEDED"), "cancelled"),
    (Delivery("rejected", "SMTP_REJECTED"), "failed"),
    (Delivery("rejected", "SMTP_REJECTED", True), "retry_wait"),
])
def test_finish_classifies_result_and_only_retries_known_temporary_rejection(delivery, status):
    factory, session, _ = fake_session_factory()
    now = datetime.now(UTC)
    job = BackgroundJob(id=uuid4(), attempt_count=1, lease_expires_at=now + timedelta(seconds=120),
                        expires_at=now + timedelta(seconds=300), scheduled_at=now)
    session.scalar.side_effect = [job, now, uuid4()]
    assert asyncio.run(MailJobs(factory).finish(JobClaim(job.id, uuid4(), {}), delivery, 3))
    assert job.status == status
    assert job.result_ref == {"delivery": delivery.status}
    assert job.lease_expires_at is None
    if delivery.status == "unknown":
        assert session.scalar.await_count == 2  # No outbox rearm.


def test_stale_execution_token_cannot_write_result():
    factory, session, _ = fake_session_factory()
    assert not asyncio.run(MailJobs(factory).finish(JobClaim(uuid4(), uuid4(), {}), Delivery("accepted", "SMTP_ACCEPTED"), 3))
    sql = str(session.scalar.call_args.args[0].compile(dialect=postgresql.dialect()))
    assert "background_jobs.execution_token =" in sql and "background_jobs.lease_expires_at > clock_timestamp()" in sql


@pytest.mark.parametrize("succeeds", [True, False])
def test_dispatch_records_confirmation_or_defers_failure_without_sensitive_logs(succeeds, caplog):
    claim = DispatchClaim(uuid4(), uuid4(), uuid4(), 1)
    jobs = Mock()
    jobs.claim_outbox = AsyncMock(return_value=[claim])
    jobs.published, jobs.publish_failed = AsyncMock(), AsyncMock()
    publisher = Mock()
    if not succeeds:
        publisher.publish.side_effect = OSError("private-broker-password")
    assert asyncio.run(dispatch_once(jobs, publisher)) == 1
    (jobs.published if succeeds else jobs.publish_failed).assert_awaited_once_with(claim)
    assert "private-broker-password" not in caplog.text


def test_publisher_only_sends_job_uuid_and_disables_automatic_publish_retry():
    app = Mock()
    claim = DispatchClaim(uuid4(), uuid4(), uuid4(), 1)
    CeleryPublisher(app).publish(claim)
    args, options = app.send_task.call_args
    assert args == (MAIL_TASK,)
    assert options["args"] == [str(claim.job_id)]
    assert options["retry"] is False and options["queue"] == "notifications"


def test_mail_task_uses_late_ack_but_not_unbounded_worker_loss_requeue():
    worker = create_worker(Settings(_env_file=None, celery_broker_url="amqp://user:example@localhost:5672/test"))
    task = worker.tasks[MAIL_TASK]
    assert task.acks_late and not task.reject_on_worker_lost
    assert task.max_retries == 0 and task.time_limit == 60 and task.soft_time_limit == 55
    task.run("invalid-id")
    worker.close()
