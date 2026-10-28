from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import async_sessionmaker

from silencio_set.accounts.cache import AuthCache
from silencio_set.core.config import Settings
from silencio_set.infrastructure.health import Dependencies
from silencio_set.jobs.repository import MailJobs
from silencio_set.mail.payloads import EncryptedPayloads
from silencio_set.mail.service import MailConsumer
from silencio_set.mail.smtp import SmtpAdapter
from silencio_set.jobs.repository import INVITATION_JOB
from silencio_set.mail.invitation_payloads import InvitationPayloads
from silencio_set.mail.invitation_service import InvitationConsumer
from silencio_set.crews.invitations import InvitationRepository


@asynccontextmanager
async def mail_runtime(settings: Settings):
    if not settings.mail_enabled:
        raise RuntimeError("Mail must be explicitly enabled")
    dependencies = Dependencies(settings)
    try:
        cache = AuthCache(dependencies.redis, settings)
        jobs = MailJobs(async_sessionmaker(dependencies.database, expire_on_commit=False))
        payloads = EncryptedPayloads(cache, settings.mail_payload_key.get_secret_value())
        yield jobs, MailConsumer(jobs, payloads, SmtpAdapter(settings), settings.mail_max_attempts)
    finally:
        await dependencies.close()


@asynccontextmanager
async def invitation_mail_runtime(settings: Settings):
    if not settings.mail_enabled:
        raise RuntimeError("Mail must be explicitly enabled")
    dependencies = Dependencies(settings)
    try:
        cache = AuthCache(dependencies.redis, settings)
        sessions = async_sessionmaker(dependencies.database, expire_on_commit=False)
        jobs = MailJobs(sessions, INVITATION_JOB)
        payloads = InvitationPayloads(cache, settings.mail_payload_key.get_secret_value())
        yield InvitationConsumer(jobs, payloads, InvitationRepository(sessions), SmtpAdapter(settings), settings.mail_max_attempts)
    finally:
        await dependencies.close()
