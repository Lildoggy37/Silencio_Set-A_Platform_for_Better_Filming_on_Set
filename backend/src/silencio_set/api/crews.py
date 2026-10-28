from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from silencio_set.accounts.errors import AuthError
from silencio_set.api.accounts import auth_service, client_ip, completed_user, csrf_browser
from silencio_set.core.http import Envelope, ErrorEnvelope
from silencio_set.crews.schemas import CrewCreate, CrewPage, CrewView, MemberExitResult, MemberLeave, MemberRemove, MemberPage, MemberRolesUpdate, MemberView

router = APIRouter(prefix="/api/v1/crews", tags=["crews"], responses={
    code: {"model": ErrorEnvelope} for code in (401, 403, 404, 409, 413, 415, 422, 429, 500, 503)
})
Page = Annotated[int, Query(ge=1, le=100_000)]
PageSize = Annotated[int, Query(ge=1, le=100)]


async def crew_service(request: Request, user=Depends(completed_user), auth=Depends(auth_service)):
    service = getattr(request.app.state, "crews", None)
    if service is None:
        raise AuthError(503, "SERVICE_UNAVAILABLE", "剧组服务暂不可用")
    writing = request.method in {"POST", "PUT", "PATCH", "DELETE"}
    await auth.cache.limit("crew-create" if writing else "crew-read", client_ip(request), str(user.id),
                           ip_limit=10 if writing else 120, identity_limit=10 if writing else 120)
    return service


@router.post("", response_model=Envelope[CrewView], status_code=201, dependencies=[Depends(csrf_browser)])
async def create_crew(body: CrewCreate, request: Request, user=Depends(completed_user), service=Depends(crew_service)):
    view = await service.execute(user, "crew.create", request.state.request_id, body=body)
    return Envelope(data=view, request_id=request.state.request_id)


@router.get("", response_model=Envelope[CrewPage])
async def list_crews(request: Request, page: Page = 1, page_size: PageSize = 20,
                     user=Depends(completed_user), service=Depends(crew_service)):
    view = await service.execute(user, "crew.list", request.state.request_id, page=page, page_size=page_size)
    return Envelope(data=view, request_id=request.state.request_id)


@router.get("/{crew_id}", response_model=Envelope[CrewView])
async def get_crew(crew_id: UUID, request: Request, user=Depends(completed_user), service=Depends(crew_service)):
    view = await service.execute(user, "crew.read", request.state.request_id, crew_id=crew_id)
    return Envelope(data=view, request_id=request.state.request_id)


@router.get("/{crew_id}/members", response_model=Envelope[MemberPage])
async def list_members(crew_id: UUID, request: Request, page: Page = 1, page_size: PageSize = 20,
                       status: Literal["active"] = "active", user=Depends(completed_user), service=Depends(crew_service)):
    # Historical directory is intentionally not exposed until its permission policy is implemented.
    view = await service.execute(user, "member.list", request.state.request_id, crew_id=crew_id, page=page, page_size=page_size)
    return Envelope(data=view, request_id=request.state.request_id)


@router.put("/{crew_id}/members/{member_id}/roles", response_model=Envelope[MemberView], dependencies=[Depends(csrf_browser)])
async def replace_roles(crew_id: UUID, member_id: UUID, body: MemberRolesUpdate, request: Request,
                        user=Depends(completed_user), service=Depends(crew_service)):
    view = await service.execute(user, "member.roles.replace", request.state.request_id,
                                 crew_id=crew_id, member_id=member_id, body=body)
    return Envelope(data=view, request_id=request.state.request_id)


@router.post("/{crew_id}/members/me/leave", response_model=Envelope[MemberExitResult], dependencies=[Depends(csrf_browser)])
async def leave_crew(crew_id: UUID, body: MemberLeave, request: Request,
                     user=Depends(completed_user), service=Depends(crew_service)):
    view = await service.execute(user, "member.leave", request.state.request_id, crew_id=crew_id, body=body)
    return Envelope(data=view, request_id=request.state.request_id)


@router.post("/{crew_id}/members/{member_id}/remove", response_model=Envelope[MemberExitResult], dependencies=[Depends(csrf_browser)])
async def remove_member(crew_id: UUID, member_id: UUID, body: MemberRemove, request: Request,
                        user=Depends(completed_user), service=Depends(crew_service)):
    view = await service.execute(user, "member.remove", request.state.request_id,
                                 crew_id=crew_id, member_id=member_id, body=body)
    return Envelope(data=view, request_id=request.state.request_id)
