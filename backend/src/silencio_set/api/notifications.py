from uuid import UUID

from fastapi import APIRouter, Depends, Request

from silencio_set.accounts.errors import AuthError
from silencio_set.api.accounts import auth_service, client_ip, completed_user, csrf_browser
from silencio_set.api.crews import Page, PageSize
from silencio_set.core.http import Envelope, ErrorEnvelope
from silencio_set.notifications.schemas import AcknowledgeInput, EmptyInput, NotificationPage, NotificationView

router = APIRouter(prefix="/api/v1/crews/{crew_id}/notifications", tags=["notifications"], responses={
    code: {"model": ErrorEnvelope} for code in (401, 403, 404, 409, 413, 415, 422, 429, 500, 503)
})


async def notification_service(request: Request, user=Depends(completed_user), auth=Depends(auth_service)):
    service = getattr(request.app.state, "notifications", None)
    if service is None: raise AuthError(503, "SERVICE_UNAVAILABLE", "通知服务暂不可用")
    writing = request.method == "POST"
    await auth.cache.limit("notification-write" if writing else "notification-read", client_ip(request), str(user.id),
        ip_limit=30 if writing else 120, identity_limit=30 if writing else 120)
    return service


async def run(request, user, service, action, crew_id, **options):
    result = await service.execute(user, action, request.state.request_id, crew_id=crew_id, **options)
    return Envelope(data=result, request_id=request.state.request_id)


@router.get("", response_model=Envelope[NotificationPage])
async def list_notifications(crew_id: UUID, request: Request, page: Page = 1, page_size: PageSize = 20,
                              unread: bool | None = None, needs_acknowledgement: bool | None = None,
                              user=Depends(completed_user), service=Depends(notification_service)):
    return await run(request, user, service, "list", crew_id, page=page, page_size=page_size,
        unread=unread, needs_acknowledgement=needs_acknowledgement)


@router.get("/{notification_id}", response_model=Envelope[NotificationView])
async def get_notification(crew_id: UUID, notification_id: UUID, request: Request,
                           user=Depends(completed_user), service=Depends(notification_service)):
    return await run(request, user, service, "get", crew_id, notification_id=notification_id)


@router.post("/{notification_id}/read", response_model=Envelope[NotificationView], dependencies=[Depends(csrf_browser)])
async def read_notification(crew_id: UUID, notification_id: UUID, body: EmptyInput, request: Request,
                            user=Depends(completed_user), service=Depends(notification_service)):
    return await run(request, user, service, "read", crew_id, notification_id=notification_id)


@router.post("/{notification_id}/acknowledge", response_model=Envelope[NotificationView], dependencies=[Depends(csrf_browser)])
async def acknowledge_notification(crew_id: UUID, notification_id: UUID, body: AcknowledgeInput, request: Request,
                                   user=Depends(completed_user), service=Depends(notification_service)):
    return await run(request, user, service, "acknowledge", crew_id, notification_id=notification_id, object_version=body.object_version)
