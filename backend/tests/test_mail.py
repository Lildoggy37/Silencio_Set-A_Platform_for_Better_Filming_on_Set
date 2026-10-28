import asyncio
from datetime import UTC, datetime
from email import policy
from email.parser import BytesParser
import json
import re
import smtplib
import socketserver
import threading
from uuid import uuid4

from cryptography.fernet import Fernet
from fakeredis.aioredis import FakeRedis
import pytest
from sqlalchemy.exc import OperationalError

from silencio_set.accounts.cache import AuthCache
from silencio_set.core.config import Settings
from silencio_set.jobs.repository import JobClaim
from silencio_set.mail.payloads import EncryptedPayloads, PayloadUnavailable, VerificationMail
from silencio_set.mail.service import MailConsumer, MailRequests
from silencio_set.mail.smtp import Delivery, SmtpAdapter
from test_auth_api import auth_client, bootstrap, PASSWORD


def payload_from(issued, cache):
    return VerificationMail(payload_id=uuid4(), verification_id=issued.verification_id,
        email="Director@example.com", email_normalized="director@example.com",
        email_digest=cache.digest_identity("email", "director@example.com"),
        code=issued.code.get_secret_value(), expires_at=issued.expires_at)


class MemoryMailJobs:
    """Test-only coordination double, not proof of PostgreSQL concurrency."""
    def __init__(self):
        self.jobs = {}
        self.outbox = []
        self.fail_commit = False
        self.reject_finish = False

    async def enqueue(self, verification_id, payload_id, email_digest, expires_at):
        if self.fail_commit:
            raise OperationalError("test SQL", {}, Exception("simulated database failure"))
        job_id = uuid4()
        ref = {"payload_id": str(payload_id), "verification_id": str(verification_id), "email_digest": email_digest}
        self.jobs[job_id] = {"ref": ref, "state": "pending_dispatch", "token": None}
        self.outbox.append({"job_id": str(job_id)})
        return job_id

    async def claim_job(self, job_id, worker_id, max_attempts=3):
        job = self.jobs.get(job_id)
        if job is None or job["state"] != "pending_dispatch":
            return None
        job["state"] = "running"
        job["token"] = uuid4()
        return JobClaim(job_id, job["token"], job["ref"])

    async def mark_sending(self, claim):
        return self.jobs[claim.job_id]["token"] == claim.token

    async def finish(self, claim, delivery, max_attempts):
        if self.reject_finish:
            return False
        self.jobs[claim.job_id]["state"] = "finished"
        self.jobs[claim.job_id]["delivery"] = delivery
        return True


class FakeSmtp:
    def __init__(self, delivery=Delivery("accepted", "SMTP_ACCEPTED")):
        self.delivery = delivery
        self.calls = 0

    def send(self, *args):
        self.calls += 1
        return self.delivery


@pytest.fixture
def smtp_server():
    """Actual loopback SMTP transport; no internet mail or forwarding."""
    messages = []
    mode = {"reply": "accept"}

    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            self.connection.settimeout(2)

            def reply(value):
                self.wfile.write(value + b"\r\n")
                self.wfile.flush()

            reply(b"220 localhost test SMTP")
            while line := self.rfile.readline(65536):
                command = line.upper()
                if command.startswith((b"EHLO", b"HELO")):
                    reply(b"250-localhost\r\n250 SIZE 1024000")
                elif command.startswith((b"MAIL FROM", b"RCPT TO", b"RSET")):
                    reply(b"250 OK")
                elif command.startswith(b"DATA"):
                    reply(b"354 End with dot")
                    body = bytearray()
                    while item := self.rfile.readline(65536):
                        if item == b".\r\n":
                            break
                        body.extend(item[1:] if item.startswith(b"..") else item)
                    messages.append(bytes(body))
                    if mode["reply"] == "disconnect":
                        return
                    reply(b"451 temporary refusal" if mode["reply"] == "reject" else b"250 accepted")
                elif command.startswith(b"QUIT"):
                    reply(b"221 closing")
                    return
                else:
                    reply(b"500 unsupported")

    class Server(socketserver.ThreadingTCPServer):
        daemon_threads = True

    with Server(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        thread.start()
        try:
            yield server.server_address[1], messages, mode
        finally:
            server.shutdown()
            thread.join(timeout=2)


def test_encrypted_payload_never_stores_plain_code_and_has_a_bounded_ttl():
    async def scenario():
        async with FakeRedis() as redis:
            settings = Settings(_env_file=None, auth_secret="test-only-authentication-secret-" * 2)
            cache = AuthCache(redis, settings)
            issued = await cache.issue_verification("Director@example.com", "unit")
            payload = payload_from(issued, cache)
            store = EncryptedPayloads(cache, Fernet.generate_key().decode())
            await store.save(payload)
            raw = await redis.get(store.key(payload.payload_id))
            envelope = json.loads(raw)
            assert envelope["key_version"] == "v1"
            assert payload.email.encode() not in raw
            assert payload.code.encode() not in raw
            assert 0 < await redis.ttl(store.key(payload.payload_id)) <= settings.auth_verification_ttl
            assert await store.read(payload.payload_id) == payload
            assert await store.current(payload)
            assert payload.code not in repr(payload)
    asyncio.run(scenario())


@pytest.mark.parametrize("damage", ["tamper", "wrong_key", "wrong_id", "missing", "malformed"])
def test_invalid_or_mismatched_payload_is_never_used(damage):
    async def scenario():
        async with FakeRedis() as redis:
            cache = AuthCache(redis, Settings(_env_file=None, auth_secret="test-only-authentication-secret-" * 2))
            issued = await cache.issue_verification("Director@example.com", "unit")
            payload = payload_from(issued, cache)
            key = Fernet.generate_key().decode()
            store = EncryptedPayloads(cache, key)
            await store.save(payload)
            payload_id = payload.payload_id
            if damage == "tamper":
                record = json.loads(await redis.get(store.key(payload_id)))
                record["ciphertext"] = record["ciphertext"][:-8] + "00000000"
                await redis.set(store.key(payload_id), json.dumps(record), ex=60)
            elif damage == "wrong_key":
                store = EncryptedPayloads(cache, Fernet.generate_key().decode())
            elif damage == "wrong_id":
                new_id = uuid4()
                await redis.set(store.key(new_id), await redis.get(store.key(payload_id)), ex=60)
                payload_id = new_id
            elif damage == "malformed":
                await redis.set(store.key(payload_id), '{"key_version":"v1","ciphertext":null}', ex=60)
            else:
                await redis.delete(store.key(payload_id))
            with pytest.raises(PayloadUnavailable):
                await store.read(payload_id)
    asyncio.run(scenario())


def test_superseded_request_is_not_sent_by_worker():
    async def scenario():
        async with FakeRedis() as redis:
            cache = AuthCache(redis, Settings(_env_file=None, auth_secret="test-only-authentication-secret-" * 2))
            store, jobs, smtp = EncryptedPayloads(cache, Fernet.generate_key().decode()), MemoryMailJobs(), FakeSmtp()
            await MailRequests(cache, store, jobs).request("Director@example.com", "unit")
            await redis.delete(cache.challenge_key("director@example.com") + ":cooldown")
            await cache.issue_verification("Director@example.com", "unit")
            job_id = next(iter(jobs.jobs))
            await MailConsumer(jobs, store, smtp, 3).run(job_id, "test-worker")
            assert smtp.calls == 0
            assert jobs.jobs[job_id]["delivery"].category == "VERIFICATION_SUPERSEDED"
    asyncio.run(scenario())


@pytest.mark.parametrize("case", ["duplicate", "missing_payload", "wrong_ref", "unknown", "stale_finish"])
def test_consumer_duplicate_and_failure_paths(case):
    async def scenario():
        async with FakeRedis() as redis:
            cache = AuthCache(redis, Settings(_env_file=None, auth_secret="test-only-authentication-secret-" * 2))
            store, jobs = EncryptedPayloads(cache, Fernet.generate_key().decode()), MemoryMailJobs()
            smtp = FakeSmtp(Delivery("unknown", "DELIVERY_UNKNOWN") if case == "unknown" else Delivery("accepted", "SMTP_ACCEPTED"))
            await MailRequests(cache, store, jobs).request("Director@example.com", "unit")
            job_id = next(iter(jobs.jobs))
            payload_id = jobs.jobs[job_id]["ref"]["payload_id"]
            if case == "missing_payload":
                from uuid import UUID
                await redis.delete(store.key(UUID(payload_id)))
            elif case == "wrong_ref":
                jobs.jobs[job_id]["ref"]["email_digest"] = "a" * 64
            elif case == "stale_finish":
                jobs.reject_finish = True
            consumer = MailConsumer(jobs, store, smtp, 3)
            await consumer.run(job_id, "test-worker")
            await consumer.run(job_id, "test-worker")
            if case in {"wrong_ref", "missing_payload"}:
                assert smtp.calls == 0
            else:
                assert smtp.calls == 1
            if case in {"unknown", "stale_finish"}:
                from uuid import UUID
                assert await redis.get(store.key(UUID(payload_id))) is not None
    asyncio.run(scenario())


@pytest.mark.parametrize("reply,status,retry", [("accept", "accepted", False), ("reject", "rejected", True), ("disconnect", "unknown", False)])
def test_real_loopback_smtp_classifies_accept_reject_and_unknown(smtp_server, reply, status, retry):
    port, messages, mode = smtp_server
    mode["reply"] = reply
    settings = Settings(_env_file=None, mail_port=port)
    payload = VerificationMail(payload_id=uuid4(), verification_id=uuid4(), email="director@example.com",
        email_normalized="director@example.com", email_digest="a" * 64, code="123456",
        expires_at=int(datetime.now(UTC).timestamp()) + 60)
    delivery = SmtpAdapter(settings).send(uuid4(), payload)
    assert delivery.status == status and delivery.retryable is retry
    assert len(messages) == 1
    assert "123456" not in repr(delivery)


def test_api_to_local_smtp_to_registration_login_and_nickname(auth_client, smtp_server):
    client, auth = auth_client
    port, messages, _ = smtp_server
    jobs = MemoryMailJobs()
    store = EncryptedPayloads(auth.cache, Fernet.generate_key().decode())
    client.app.state.mail_requests = MailRequests(auth.cache, store, jobs)
    bootstrap(client)
    accepted = client.post("/api/v1/auth/email-verifications", json={"email": "Director@example.com"})
    assert accepted.status_code == 202
    data = accepted.json()["data"]
    assert data["delivery_status"] == "pending_dispatch"
    assert set(data) == {"verification_id", "expires_at", "delivery_status", "resend_after_seconds"}
    assert data["resend_after_seconds"] == auth.cache.settings.auth_resend_interval
    job_id = next(iter(jobs.jobs))
    assert "Director@example.com" not in json.dumps(jobs.jobs[job_id]["ref"]) + json.dumps(jobs.outbox)
    consumer = MailConsumer(jobs, store, SmtpAdapter(Settings(_env_file=None, mail_port=port)), 3)
    client.portal.call(consumer.run, job_id, "test-worker")
    assert len(messages) == 1
    email = BytesParser(policy=policy.default).parsebytes(messages[0])
    code = re.search(r"[0-9]{6}", email.get_body(preferencelist=("plain",)).get_content()).group()
    verified = client.post("/api/v1/auth/email-verifications/verify", json={"email": "director@example.com", "verification_id": data["verification_id"], "code": code})
    assert verified.status_code == 200
    registered = client.post("/api/v1/auth/register", json={"registration_token": verified.json()["data"]["registration_token"], "password": PASSWORD})
    assert registered.status_code == 201
    assert client.post("/api/v1/auth/login", json={"email": "director@example.com", "password": PASSWORD}).status_code == 200
    bootstrap(client)
    profile = client.patch("/api/v1/users/me/profile", json={"display_name": "导演", "expected_lock_version": 1})
    assert profile.status_code == 200 and profile.json()["data"]["profile_completed"]


def test_request_does_not_return_202_when_durable_commit_fails(auth_client):
    client, auth = auth_client
    jobs = MemoryMailJobs()
    jobs.fail_commit = True
    store = EncryptedPayloads(auth.cache, Fernet.generate_key().decode())
    client.app.state.mail_requests = MailRequests(auth.cache, store, jobs)
    bootstrap(client)
    response = client.post("/api/v1/auth/email-verifications", json={"email": "Director@example.com"})
    assert response.status_code == 503
    assert not jobs.jobs and not jobs.outbox
    keys = client.portal.call(auth.cache.redis.keys, "auth:mail_payload:*")
    assert len(keys) == 1  # Orphaned encrypted payload is bounded by its TTL.


@pytest.mark.parametrize("case,status,retry", [("connect", "rejected", True), ("auth", "rejected", False),
                                             ("data", "rejected", True), ("close", "accepted", False)])
def test_smtp_error_details_are_not_returned_or_logged(monkeypatch, caplog, case, status, retry):
    class Stub:
        def __init__(self, *args, **kwargs):
            if case == "connect":
                raise OSError("private connection detail")

        def ehlo(self):
            if case == "auth":
                raise smtplib.SMTPAuthenticationError(535, b"private-auth-detail")

        def send_message(self, message):
            if case == "data":
                raise smtplib.SMTPDataError(451, b"private-server-detail")
            return {}

        def close(self):
            raise OSError("private quit detail")

    monkeypatch.setattr("silencio_set.mail.smtp.smtplib.SMTP", Stub)
    payload = VerificationMail(payload_id=uuid4(), verification_id=uuid4(), email="director@example.com",
        email_normalized="director@example.com", email_digest="a" * 64, code="123456",
        expires_at=int(datetime.now(UTC).timestamp()) + 60)
    delivery = SmtpAdapter(Settings(_env_file=None)).send(uuid4(), payload)
    assert delivery.status == status and delivery.retryable is retry
    assert "private" not in repr(delivery) + caplog.text


def test_unknown_delivery_cannot_be_marked_retryable():
    with pytest.raises(ValueError):
        Delivery("unknown", "DELIVERY_UNKNOWN", True)
    with pytest.raises(ValueError):
        Delivery("rejected", "unredacted server text")
