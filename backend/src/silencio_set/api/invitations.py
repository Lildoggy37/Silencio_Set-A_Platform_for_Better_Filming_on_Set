from uuid import UUID

from fastapi import APIRouter, Depends, Request

from silencio_set.accounts.errors import AuthError
from silencio_set.api.accounts import auth_service, client_ip, completed_user, csrf_browser
from silencio_set.api.crews import Page, PageSize
from silencio_set.core.http import Envelope, ErrorEnvelope
from silencio_set.crews.invitation_schemas import (
    InvitationAccept, InvitationCreate, InvitationCreated, InvitationJoined, InvitationPage,
    InvitationRevoke, InvitationStatus, InvitationView,
)

router = APIRouter(prefix="/api/v1", tags=["invitations"], responses={
    code: {"model": ErrorEnvelope} for code in (400, 401, 403, 404, 409, 413, 415, 422, 429, 500, 503)
})


async def service(request: Request, user=Depends(completed_user), auth=Depends(auth_service)):
    coordinator = getattr(request.app.state, "invitations", None)
    if coordinator is None:
        raise AuthError(503, "SERVICE_UNAVAILABLE", "邀请服务暂不可用")
    writing = request.method != "GET"
    await auth.cache.limit("invitation-write" if writing else "invitation-read", client_ip(request), str(user.id),
        ip_limit=10 if writing else 120, identity_limit=10 if writing else 120)
    return coordinator


@router.post("/crews/{crew_id}/invitations", status_code=202, response_model=Envelope[InvitationCreated], dependencies=[Depends(csrf_browser)])
async def create(crew_id: UUID, body: InvitationCreate, request: Request, user=Depends(completed_user), coordinator=Depends(service)):
    result = await coordinator.execute(user, "invitation.create", request.state.request_id, crew_id=crew_id, body=body)
    return Envelope(data=result, request_id=request.state.request_id)


@router.get("/crews/{crew_id}/invitations", response_model=Envelope[InvitationPage])
async def list_invitations(crew_id: UUID, request: Request, status: InvitationStatus | None = None,
    page: Page = 1, page_size: PageSize = 20, user=Depends(completed_user), coordinator=Depends(service)):
    result = await coordinator.execute(user, "invitation.list", request.state.request_id, crew_id=crew_id,
        status=status, page=page, page_size=page_size)
    return Envelope(data=result, request_id=request.state.request_id)


@router.post("/crews/{crew_id}/invitations/{invitation_id}/revoke", response_model=Envelope[InvitationView], dependencies=[Depends(csrf_browser)])
async def revoke(crew_id: UUID, invitation_id: UUID, body: InvitationRevoke, request: Request,
    user=Depends(completed_user), coordinator=Depends(service)):
    result = await coordinator.execute(user, "invitation.revoke", request.state.request_id, crew_id=crew_id,
        invitation_id=invitation_id, body=body)
    return Envelope(data=result, request_id=request.state.request_id)


@router.post("/invitations/accept", response_model=Envelope[InvitationJoined], dependencies=[Depends(csrf_browser)])
async def accept(body: InvitationAccept, request: Request, user=Depends(completed_user), coordinator=Depends(service)):
    result = await coordinator.execute(user, "invitation.accept", request.state.request_id, body=body)
    return Envelope(data=result, request_id=request.state.request_id)
