import json
import os
from pathlib import Path
from email import policy
from email.parser import BytesParser
import re
import subprocess
import sys
import time
from uuid import UUID, uuid4

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from kombu import Connection, Queue
import pytest
from sqlalchemy import func, select

from silencio_set.core import config as config_module
from silencio_set.core.config import Settings
from silencio_set.crews.models import AuditLog, CrewMember
from silencio_set.jobs.dispatcher import CeleryPublisher, dispatch_once
from silencio_set.jobs.models import BackgroundJob, OutboxEvent
from silencio_set.jobs.repository import DispatchClaim, MAIL_TASK
from silencio_set.main import create_app
from silencio_set.worker import create_worker
from smtp_inbox import LocalInbox


@pytest.fixture
def mail_environment(monkeypatch):
    configured = os.environ.get('SILENCIO_MAIL_TEST_SETTINGS')
    if not configured:
        pytest.skip('Run scripts/mail_smoke.py for the real RabbitMQ/SMTP journey')
    path = Path(configured)
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
    with LocalInbox() as inbox:
        values = json.loads(path.read_text())
        values.update(mail_host='127.0.0.1', mail_port=inbox.port, mail_mode='local')
        path.write_text(json.dumps(values))
        settings = Settings(_env_file=None, **values)
        with monkeypatch.context() as migration_patch:
            migration_patch.setattr(config_module, 'load_settings', lambda:settings)
            command.upgrade(Config('alembic.ini'), 'head')
        with TestClient(create_app(settings), base_url='http://127.0.0.1:8000') as client:
            client.headers['Origin'] = 'http://127.0.0.1:5173'
            yield client, inbox, settings, path


def call(client, path, body, status=200):
    response = client.post('/api/v1' + path, json=body)
    assert response.status_code == status, f'{path}: {response.status_code} {response.text}'
    return response.json()['data']


def csrf(client):
    response = client.get('/api/v1/auth/csrf')
    assert response.status_code == 200
    client.headers['X-CSRF-Token'] = response.json()['data']['csrf_token']


async def job_state(sessions, verification_id):
    async with sessions() as session:
        job = await session.scalar(select(BackgroundJob).where(BackgroundJob.idempotency_key == verification_id))
        event = await session.scalar(select(OutboxEvent).where(OutboxEvent.job_id == job.id))
        return job, event


def committed_jobs(client, sessions, *references):
    deadline = time.monotonic() + 5
    while True:
        records = [client.portal.call(job_state, sessions, reference) for reference in references]
        if all(job.status == 'succeeded' for job, _ in records):
            return records
        assert time.monotonic() < deadline, 'Worker result not committed'
        time.sleep(0.05)


async def membership_effects(sessions, crew_id, user_id, invitation_id):
    async with sessions() as session:
        return {
            'members':await session.scalar(select(func.count()).select_from(CrewMember).where(CrewMember.crew_id == UUID(crew_id))),
            'recipient_memberships':await session.scalar(select(func.count()).select_from(CrewMember).where(
                CrewMember.crew_id == UUID(crew_id), CrewMember.user_id == UUID(user_id))),
            'accept_audits':await session.scalar(select(func.count()).select_from(AuditLog).where(
                AuditLog.crew_id == UUID(crew_id), AuditLog.action == 'invitation.accept',
                AuditLog.target_id == UUID(invitation_id), AuditLog.result == 'success')),
        }


def test_real_registration_and_invitation_mail_journeys(mail_environment):
    client, inbox, settings, settings_path = mail_environment
    sessions = client.app.state.auth.repository.sessions
    jobs = client.app.state.mail_requests.jobs
    # This broker/vhost is created solely by rabbitmq_local.py for local tests.
    # Remove messages left by a failed prior run before asserting queue contents.
    with Connection(settings.celery_broker_url.get_secret_value()) as connection:
        queue = Queue('notifications', durable=True)(connection)
        queue.declare()
        queue.purge()
    csrf(client)
    first_email, second_email = 'first@example.com', 'second@example.com'
    first = call(client, '/auth/email-verifications', {'email':first_email}, 202)
    assert first['delivery_status'] == 'pending_dispatch'
    assert set(first) == {'verification_id','expires_at','delivery_status','resend_after_seconds'}
    first_job, first_event = client.portal.call(job_state, sessions, first['verification_id'])
    assert first_job.status == 'pending_dispatch' and first_event.status == 'pending'
    assert first_email not in json.dumps(first_job.payload_ref)
    payloads = client.app.state.mail_requests.payloads
    encrypted = client.portal.call(client.app.state.auth.cache.redis.get, payloads.key(UUID(first_job.payload_ref['payload_id'])))
    assert first_email.encode() not in encrypted
    publisher_app = create_worker(settings)
    publisher = CeleryPublisher(publisher_app)
    worker = None
    log = (settings_path.parent / 'worker.log').open('wb')
    try:
        assert client.portal.call(dispatch_once, jobs, publisher) == 1
        first_job, first_event = client.portal.call(job_state, sessions, first['verification_id'])
        assert first_job.status == 'queued' and first_event.status == 'dispatched'
        with Connection(settings.celery_broker_url.get_secret_value()) as connection:
            queue = Queue('notifications', durable=True)(connection)
            message = queue.get(no_ack=False)
            assert message is not None
            assert message.headers['task'] == MAIL_TASK
            assert message.payload[0] == [str(first_job.id)] and message.payload[1] == {}
            assert message.properties['delivery_mode'] == 2
            assert first_email not in message.body
            message.requeue()
        # Queue a duplicate before a second, distinct request. A single worker
        # with prefetch=1 must pass the duplicate to deliver the second email.
        publisher.publish(DispatchClaim(first_event.id, first_job.id, uuid4(), 1))
        second = call(client, '/auth/email-verifications', {'email':second_email}, 202)
        assert client.portal.call(dispatch_once, jobs, publisher) == 1
        worker = subprocess.Popen([sys.executable, str(Path('scripts/mail_worker.py').resolve()), str(settings_path)],
            stdout=log, stderr=subprocess.STDOUT)
        inbox.wait_for(2)
        (first_job, first_event), (second_job, _) = committed_jobs(
            client, sessions, first['verification_id'], second['verification_id'])
        assert first_job.attempt_count == second_job.attempt_count == 1
        assert len(inbox.messages) == 2
        messages = [BytesParser(policy=policy.default).parsebytes(raw) for raw in inbox.messages]
        assert {str(message['To']) for message in messages} == {first_email,second_email}
        assert {str(message['Message-ID']) for message in messages} == {
            f'<{first_job.id}@silencio-set.local>', f'<{second_job.id}@silencio-set.local>',
        }
        message = next(message for message in messages if str(message['To']) == first_email)
        code = re.search(r'验证码[：:]\s*(\d{6})', message.get_content()).group(1)
        assert code.encode() not in encrypted
        verification = call(client, '/auth/email-verifications/verify', {
            'email':first_email,'verification_id':first['verification_id'],'code':code,
        })
        password = 'real mail integration test password'
        registered = call(client, '/auth/register', {'registration_token':verification['registration_token'],'password':password}, 201)
        user = call(client, '/auth/login', {'email':first_email,'password':password})
        assert user['id'] == registered['user_id']
        assert client.get('/api/v1/users/me').json()['data']['email'] == first_email
        assert first_job.result_ref['delivery'] == second_job.result_ref['delivery'] == 'accepted'

        # The inviter is a real registered account. Account and crew mutations
        # continue through HTTP; no seeded memberships or permission overrides.
        csrf(client)
        profile = client.patch('/api/v1/users/me/profile', json={
            'display_name':'邀请人同学','expected_lock_version':user['lock_version'],
        })
        assert profile.status_code == 200
        crew = call(client, '/crews', {'name':'邀请联调剧组','project_name':'雨后的校园'}, 201)
        crew_path = f"/crews/{crew['id']}"
        invite_path = crew_path + '/invitations'
        created = call(client, invite_path, {'email':second_email}, 202)
        invitation = created['invitation']
        assert created['delivery_status'] == 'pending_dispatch'
        duplicate = client.post('/api/v1' + invite_path, json={'email':second_email})
        assert duplicate.status_code == 409
        assert duplicate.json()['error']['code'] == 'INVITATION_PENDING'
        assert client.portal.call(dispatch_once, jobs, publisher) == 1
        inbox.wait_for(3)
        (invitation_job, invitation_event), = committed_jobs(client, sessions, invitation['id'])
        assert invitation_job.job_type == 'invitation_email'
        assert invitation_event.event_type == 'invitation_email'
        assert invitation_job.attempt_count == 1
        delivered = BytesParser(policy=policy.default).parsebytes(inbox.messages[2])
        assert str(delivered['To']) == second_email
        assert str(delivered['Subject']) == 'Silencio Set 剧组邀请'
        credential = re.search(r'http://127\.0\.0\.1:5173/#invitation=([A-Za-z0-9_-]{43})', delivered.get_content()).group(1)
        assert credential not in json.dumps(created)
        assert credential not in json.dumps(invitation_job.payload_ref)
        wrong_account = client.post('/api/v1/invitations/accept', json={'token':credential})
        assert wrong_account.status_code == 400
        assert wrong_account.json()['error']['code'] == 'INVITATION_INVALID'

        # Revocation before dispatch cancels the persistent task and its outbox.
        cancelled = call(client, invite_path, {'email':'cancelled@example.com'}, 202)['invitation']
        revoked = call(client, invite_path + f"/{cancelled['id']}/revoke", {
            'expected_lock_version':cancelled['lock_version'],
        })
        assert revoked['status'] == 'revoked'
        stopped_job, stopped_event = client.portal.call(job_state, sessions, cancelled['id'])
        assert stopped_job.status == 'cancelled' and stopped_event.status == 'failed'
        assert client.portal.call(dispatch_once, jobs, publisher) == 0

        # The recipient registers using their own delivered verification email.
        logged_out = client.post('/api/v1/auth/logout', json={})
        assert logged_out.status_code == 204
        csrf(client)
        recipient_message = next(message for message in messages if str(message['To']) == second_email)
        recipient_code = re.search(r'验证码[：:]\s*(\d{6})', recipient_message.get_content()).group(1)
        recipient_grant = call(client, '/auth/email-verifications/verify', {
            'email':second_email,'verification_id':second['verification_id'],'code':recipient_code,
        })
        call(client, '/auth/register', {'registration_token':recipient_grant['registration_token'],'password':password}, 201)
        recipient = call(client, '/auth/login', {'email':second_email,'password':password})
        csrf(client)
        incomplete = client.post('/api/v1/invitations/accept', json={'token':credential})
        assert incomplete.status_code == 403
        assert incomplete.json()['error']['code'] == 'PROFILE_INCOMPLETE'
        completed = client.patch('/api/v1/users/me/profile', json={
            'display_name':'受邀同学','expected_lock_version':recipient['lock_version'],
        })
        assert completed.status_code == 200
        joined = call(client, '/invitations/accept', {'token':credential})
        assert joined['crew_id'] == crew['id'] and joined['accepted'] is True
        assert joined['membership']['role_codes'] == []
        assert call(client, '/invitations/accept', {'token':credential}) == joined
        effects = client.portal.call(membership_effects, sessions, crew['id'], recipient['id'], invitation['id'])
        assert effects == {'members':2,'recipient_memberships':1,'accept_audits':1}
        current_crew = client.get('/api/v1' + crew_path)
        assert current_crew.status_code == 200
        assert 'member.invite' not in current_crew.json()['data']['permissions']
        unauthorized = client.post('/api/v1' + invite_path, json={'email':'unauthorized@example.com'})
        assert unauthorized.status_code == 403
        assert len(inbox.messages) == 3
    finally:
        publisher_app.close()
        if worker is not None:
            worker.terminate()
            try:
                worker.wait(timeout=10)
            except subprocess.TimeoutExpired:
                worker.kill()
                worker.wait(timeout=5)
        log.close()
