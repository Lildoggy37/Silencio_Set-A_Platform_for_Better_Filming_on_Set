from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from silencio_set.accounts.errors import AuthError
from silencio_set.api.accounts import auth_service, client_ip, completed_user, csrf_browser
from silencio_set.api.availability import calendar_window
from silencio_set.api.crews import Page
from silencio_set.api.tasks import IdempotencyKey
from silencio_set.core.http import Envelope, ErrorEnvelope
from silencio_set.resources.schemas import ResourceCreate, ResourcePatch, ResourceAuthorization, ResourcePage, ResourceView, ResourceAvailability, ReservationCreate, ReservationDecision, ReservationCancel, ReservationPage, ReservationView, ResourceResponsibilities

router = APIRouter(prefix="/api/v1/crews/{crew_id}", tags=["resources"], responses={
    code: {"model": ErrorEnvelope} for code in (401, 403, 404, 409, 413, 415, 422, 429, 500, 503)
})


async def resource_service(request: Request, user=Depends(completed_user), auth=Depends(auth_service)):
    service = getattr(request.app.state, "resources", None)
    if service is None: raise AuthError(503, "SERVICE_UNAVAILABLE", "资源服务暂不可用")
    writing = request.method != "GET"
    await auth.cache.limit("resource-write" if writing else "resource-read", client_ip(request), str(user.id),
        ip_limit=10 if writing else 120, identity_limit=10 if writing else 120)
    return service


async def run(request, user, service, action, crew_id, **options):
    result = await service.execute(user, action, request.state.request_id, crew_id=crew_id, **options)
    return Envelope(data=result, request_id=request.state.request_id)


@router.get("/resources", response_model=Envelope[ResourcePage])
async def resources(crew_id: UUID, request: Request, page: Page = 1, page_size: int = Query(default=20, ge=1, le=20),
                    user=Depends(completed_user), service=Depends(resource_service)):
    return await run(request, user, service, "list", crew_id, page=page, page_size=page_size)


@router.get("/resources/{resource_id}", response_model=Envelope[ResourceView])
async def resource(crew_id: UUID, resource_id: UUID, request: Request, user=Depends(completed_user), service=Depends(resource_service)):
    return await run(request, user, service, "get", crew_id, resource_id=resource_id)


@router.get("/resources/{resource_id}/availability", response_model=Envelope[ResourceAvailability])
async def availability(crew_id: UUID, resource_id: UUID, request: Request, window=Depends(calendar_window),
                       user=Depends(completed_user), service=Depends(resource_service)):
    return await run(request, user, service, "availability", crew_id, resource_id=resource_id, window=window)


@router.post("/resources", response_model=Envelope[ResourceView], status_code=201, dependencies=[Depends(csrf_browser)])
async def create_resource(crew_id: UUID, request: Request, body: ResourceCreate, idempotency_key: IdempotencyKey,
                          user=Depends(completed_user), service=Depends(resource_service)):
    return await run(request, user, service, "create", crew_id, body=body, idempotency_key=str(idempotency_key))


@router.patch("/resources/{resource_id}", response_model=Envelope[ResourceView], dependencies=[Depends(csrf_browser)])
async def edit_resource(crew_id: UUID, resource_id: UUID, request: Request, body: ResourcePatch,
                        user=Depends(completed_user), service=Depends(resource_service)):
    return await run(request, user, service, "edit", crew_id, resource_id=resource_id, body=body)


@router.put("/resources/{resource_id}/authorization", response_model=Envelope[ResourceView], dependencies=[Depends(csrf_browser)])
async def authorize_resource(crew_id: UUID, resource_id: UUID, request: Request, body: ResourceAuthorization, idempotency_key: IdempotencyKey,
                             user=Depends(completed_user), service=Depends(resource_service)):
    return await run(request, user, service, "authorize", crew_id, resource_id=resource_id, body=body, idempotency_key=str(idempotency_key))


@router.get("/resource-reservations", response_model=Envelope[ReservationPage])
async def reservations(crew_id: UUID, request: Request, page: Page = 1, page_size: int = Query(default=20, ge=1, le=20),
                       resource_id: UUID | None = None, status: Literal["pending", "confirmed", "rejected", "cancelled"] | None = None,
                       user=Depends(completed_user), service=Depends(resource_service)):
    return await run(request, user, service, "reservations", crew_id, page=page, page_size=page_size, resource_id=resource_id, status=status)


@router.post("/resource-reservations", response_model=Envelope[ReservationView], status_code=201, dependencies=[Depends(csrf_browser)])
async def apply_reservation(crew_id: UUID, request: Request, body: ReservationCreate, idempotency_key: IdempotencyKey,
                            user=Depends(completed_user), service=Depends(resource_service)):
    return await run(request, user, service, "apply", crew_id, body=body, idempotency_key=str(idempotency_key))


@router.get("/resource-reservations/{reservation_id}", response_model=Envelope[ReservationView])
async def reservation(crew_id: UUID, reservation_id: UUID, request: Request, user=Depends(completed_user), service=Depends(resource_service)):
    return await run(request, user, service, "reservation", crew_id, reservation_id=reservation_id)


@router.post("/resource-reservations/{reservation_id}/decisions", response_model=Envelope[ReservationView], dependencies=[Depends(csrf_browser)])
async def decide_reservation(crew_id: UUID, reservation_id: UUID, request: Request, body: ReservationDecision, idempotency_key: IdempotencyKey,
                             user=Depends(completed_user), service=Depends(resource_service)):
    return await run(request, user, service, "decide", crew_id, reservation_id=reservation_id, body=body, idempotency_key=str(idempotency_key))


@router.post("/resource-reservations/{reservation_id}/cancel", response_model=Envelope[ReservationView], dependencies=[Depends(csrf_browser)])
async def cancel_reservation(crew_id: UUID, reservation_id: UUID, request: Request, body: ReservationCancel, idempotency_key: IdempotencyKey,
                             user=Depends(completed_user), service=Depends(resource_service)):
    return await run(request, user, service, "cancel", crew_id, reservation_id=reservation_id, body=body, idempotency_key=str(idempotency_key))


@router.get("/members/{member_id}/resource-responsibilities", response_model=Envelope[ResourceResponsibilities])
async def responsibilities(crew_id: UUID, member_id: UUID, request: Request, page: Page = 1, page_size: int = Query(default=20, ge=1, le=20),
                           user=Depends(completed_user), service=Depends(resource_service)):
    return await run(request, user, service, "responsibilities", crew_id, member_id=member_id, page=page, page_size=page_size)
