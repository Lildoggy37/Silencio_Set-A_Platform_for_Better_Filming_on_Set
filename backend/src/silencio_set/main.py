from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.exc import DBAPIError

from silencio_set.api.health import router
from silencio_set.api.accounts import router as accounts_router, auth_exception_handler
from silencio_set.accounts.cache import AuthCache
from silencio_set.accounts.errors import AuthError, unavailable
from silencio_set.accounts.repository import AccountRepository
from silencio_set.accounts.service import AuthService
from silencio_set.core.auth_http import AuthRequestMiddleware
from silencio_set.core.config import Settings, load_settings
from silencio_set.core.http import (
    RequestContextMiddleware, http_exception_handler, validation_exception_handler,
)
from silencio_set.infrastructure.health import Dependencies
from silencio_set.jobs.repository import MailJobs
from silencio_set.mail.payloads import EncryptedPayloads
from silencio_set.mail.service import MailRequests
from silencio_set.api.crews import router as crews_router
from silencio_set.crews.repository import CrewRepository
from silencio_set.crews.service import CrewService
from silencio_set.crews.organization import OrganizationRepository
from silencio_set.crews.invitations import InvitationRepository, InvitationService
from silencio_set.mail.invitation_payloads import InvitationPayloads
from silencio_set.api.invitations import router as invitations_router
from silencio_set.api.tasks import router as tasks_router, handover_router
from silencio_set.tasks.repository import TaskRepository, check_task_responsibilities
from silencio_set.tasks.service import TaskService
from silencio_set.api.notifications import router as notifications_router
from silencio_set.notifications.repository import NotificationRepository
from silencio_set.notifications.service import NotificationService
from silencio_set.api.availability import router as availability_router
from silencio_set.availability.repository import AvailabilityRepository
from silencio_set.availability.service import AvailabilityService
from silencio_set.api.resources import router as resources_router
from silencio_set.resources.repository import ResourceRepository, check_resource_responsibilities
from silencio_set.resources.service import ResourceService


def create_app(settings: Settings | None = None) -> FastAPI:
    config = settings if settings is not None else load_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        dependencies = Dependencies(config)
        app.state.dependencies = dependencies
        try:
            app.state.auth = (
                AuthService(AuthCache(dependencies.redis, config), AccountRepository(
                    async_sessionmaker(dependencies.database, expire_on_commit=False),
                )) if config.auth_enabled else None
            )
            app.state.mail_requests = (
                MailRequests(app.state.auth.cache,
                    EncryptedPayloads(app.state.auth.cache, config.mail_payload_key.get_secret_value()),
                    MailJobs(app.state.auth.repository.sessions)) if config.mail_enabled else None
            )
            app.state.crews = CrewService(CrewRepository(app.state.auth.repository.sessions),
                OrganizationRepository(app.state.auth.repository.sessions, exit_checks=[check_task_responsibilities, check_resource_responsibilities])) if config.auth_enabled else None
            app.state.tasks = TaskService(TaskRepository(app.state.auth.repository.sessions),
                app.state.crews.repository) if config.auth_enabled else None
            app.state.notifications = NotificationService(NotificationRepository(app.state.auth.repository.sessions),
                app.state.crews.repository) if config.auth_enabled else None
            app.state.availability = AvailabilityService(AvailabilityRepository(app.state.auth.repository.sessions),
                app.state.crews.repository) if config.auth_enabled else None
            app.state.resources = ResourceService(ResourceRepository(app.state.auth.repository.sessions),
                app.state.crews.repository) if config.auth_enabled else None
            app.state.invitations = InvitationService(InvitationRepository(app.state.auth.repository.sessions,
                InvitationPayloads(app.state.auth.cache, config.mail_payload_key.get_secret_value()) if config.mail_enabled else None),
                app.state.crews.repository) if config.auth_enabled else None
            yield
        finally:
            await dependencies.close()

    app = FastAPI(title="Silencio Set API", version="0.1.0", lifespan=lifespan)
    app.state.settings = config
    app.add_middleware(AuthRequestMiddleware)
    app.add_middleware(RequestContextMiddleware)
    app.add_exception_handler(HTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(AuthError, auth_exception_handler)

    async def database_exception_handler(request, exc):
        # No SQL text or database exception messages in the HTTP response.
        return await auth_exception_handler(request, unavailable())

    app.add_exception_handler(DBAPIError, database_exception_handler)
    app.include_router(router)
    app.include_router(accounts_router)
    app.include_router(crews_router)
    app.include_router(invitations_router)
    app.include_router(tasks_router)
    app.include_router(notifications_router)
    app.include_router(handover_router)
    app.include_router(availability_router)
    app.include_router(resources_router)
    return app
