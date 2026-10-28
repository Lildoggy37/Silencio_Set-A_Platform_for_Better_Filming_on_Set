from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from pydantic import ValidationError

from silencio_set.accounts.errors import AuthError
from silencio_set.api.accounts import auth_service, client_ip, completed_user, csrf_browser
from silencio_set.api.crews import Page
from silencio_set.availability.schemas import AvailabilityPage, AvailabilityView, QueryWindow, ReplaceAvailability
from silencio_set.core.http import Envelope, ErrorEnvelope

router = APIRouter(prefix="/api/v1/crews/{crew_id}", tags=["availability"], responses={
    code: {"model": ErrorEnvelope} for code in (401, 403, 404, 409, 413, 415, 422, 429, 500, 503)
})


def calendar_window(range_start: str = Query(max_length=40), range_end: str = Query(max_length=40)):
    try:
        return QueryWindow(range_start=range_start, range_end=range_end)
    except (ValidationError, ValueError):
        raise AuthError(422, "VALIDATION_ERROR", "档期范围须为带时区整秒时间，且不超过31天") from None


async def availability_service(request: Request, user=Depends(completed_user), auth=Depends(auth_service)):
    service = getattr(request.app.state, "availability", None)
    if service is None: raise AuthError(503, "SERVICE_UNAVAILABLE", "档期服务暂不可用")
    writing = request.method == "PUT"
    await auth.cache.limit("availability-write" if writing else "availability-read", client_ip(request), str(user.id),
        ip_limit=10 if writing else 120, identity_limit=10 if writing else 120)
    return service


async def run(request, user, service, action, crew_id, **options):
    result = await service.execute(user, action, request.state.request_id, crew_id=crew_id, **options)
    return Envelope(data=result, request_id=request.state.request_id)


@router.get("/members/me/availability", response_model=Envelope[AvailabilityView])
async def own_availability(crew_id: UUID, request: Request, window=Depends(calendar_window),
                            user=Depends(completed_user), service=Depends(availability_service)):
    return await run(request, user, service, "own", crew_id, window=window)


@router.put("/members/me/availability", response_model=Envelope[AvailabilityView], dependencies=[Depends(csrf_browser)])
async def replace_availability(crew_id: UUID, request: Request, body: ReplaceAvailability,
                                user=Depends(completed_user), service=Depends(availability_service)):
    return await run(request, user, service, "replace", crew_id, body=body)


@router.get("/availability", response_model=Envelope[AvailabilityPage])
async def crew_availability(crew_id: UUID, request: Request, page: Page = 1, page_size: int = Query(default=20, ge=1, le=20),
                             member_id: UUID | None = None, window=Depends(calendar_window),
                             user=Depends(completed_user), service=Depends(availability_service)):
    return await run(request, user, service, "list", crew_id, window=window, page=page, page_size=page_size, member_id=member_id)
