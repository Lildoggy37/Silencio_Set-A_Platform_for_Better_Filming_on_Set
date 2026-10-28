from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request
from pydantic import AwareDatetime

from silencio_set.accounts.errors import AuthError
from silencio_set.api.accounts import auth_service, client_ip, completed_user, csrf_browser
from silencio_set.api.crews import Page, PageSize
from silencio_set.core.http import Envelope, ErrorEnvelope
from silencio_set.tasks.schemas import CancelInput, ExitResponsibilityPage, ReviewInput, SubmissionInput, SubmissionPage, TaskCreate, TaskDetail, TaskPage, TaskPatch, TaskStatus, TaskView, VersionInput

router = APIRouter(prefix="/api/v1/crews/{crew_id}/tasks", tags=["tasks"], responses={
    code: {"model": ErrorEnvelope} for code in (401, 403, 404, 409, 413, 415, 422, 429, 500, 503)
})
IdempotencyKey = Annotated[UUID, Header(alias="Idempotency-Key")]
handover_router = APIRouter(prefix="/api/v1/crews/{crew_id}/members", tags=["member-responsibilities"])


async def task_service(request: Request, user=Depends(completed_user), auth=Depends(auth_service)):
    service = getattr(request.app.state, "tasks", None)
    if service is None: raise AuthError(503, "SERVICE_UNAVAILABLE", "任务服务暂不可用")
    writing = request.method in {"POST", "PATCH"}
    await auth.cache.limit("task-write" if writing else "task-read", client_ip(request), str(user.id),
        ip_limit=10 if writing else 120, identity_limit=10 if writing else 120)
    return service


async def run(request, user, service, action, crew_id, **options):
    result = await service.execute(user, action, request.state.request_id, crew_id=crew_id, **options)
    return Envelope(data=result, request_id=request.state.request_id)


@handover_router.get("/{member_id}/exit-responsibilities", response_model=Envelope[ExitResponsibilityPage])
async def exit_responsibilities(crew_id: UUID, member_id: UUID, request: Request, page: Page = 1, page_size: PageSize = 20,
                                 user=Depends(completed_user), service=Depends(task_service)):
    return await run(request, user, service, "responsibilities", crew_id, member_id=member_id, page=page, page_size=page_size)


@router.get("", response_model=Envelope[TaskPage])
async def list_tasks(crew_id: UUID, request: Request, page: Page = 1, page_size: PageSize = 20,
                     status: TaskStatus | None = None, owner_member_id: UUID | None = None,
                     due_from: Annotated[AwareDatetime | None, Query()] = None,
                     due_to: Annotated[AwareDatetime | None, Query()] = None,
                     user=Depends(completed_user), service=Depends(task_service)):
    if due_from is not None and due_to is not None and due_from > due_to:
        raise AuthError(422, "VALIDATION_ERROR", "截止时间查询范围无效")
    return await run(request, user, service, "list", crew_id, page=page, page_size=page_size,
        status=status, owner_member_id=owner_member_id, due_from=due_from, due_to=due_to)


@router.get("/{task_id}", response_model=Envelope[TaskDetail])
async def get_task(crew_id: UUID, task_id: UUID, request: Request, user=Depends(completed_user), service=Depends(task_service)):
    return await run(request, user, service, "read", crew_id, task_id=task_id)


@router.get("/{task_id}/submissions", response_model=Envelope[SubmissionPage])
async def submission_history(crew_id: UUID, task_id: UUID, request: Request, page: Page = 1, page_size: PageSize = 20,
                              user=Depends(completed_user), service=Depends(task_service)):
    return await run(request, user, service, "read", crew_id, task_id=task_id, page=page, page_size=page_size)


@router.post("", response_model=Envelope[TaskView], status_code=201, dependencies=[Depends(csrf_browser)])
async def create_task(crew_id: UUID, body: TaskCreate, request: Request, idempotency_key: IdempotencyKey,
                      user=Depends(completed_user), service=Depends(task_service)):
    return await run(request, user, service, "create", crew_id, body=body, idempotency_key=str(idempotency_key))


@router.patch("/{task_id}", response_model=Envelope[TaskView], dependencies=[Depends(csrf_browser)])
async def edit_task(crew_id: UUID, task_id: UUID, body: TaskPatch, request: Request,
                    user=Depends(completed_user), service=Depends(task_service)):
    return await run(request, user, service, "edit", crew_id, task_id=task_id, body=body)


@router.post("/{task_id}/start", response_model=Envelope[TaskView], dependencies=[Depends(csrf_browser)])
async def start_task(crew_id: UUID, task_id: UUID, body: VersionInput, request: Request,
                     user=Depends(completed_user), service=Depends(task_service)):
    return await run(request, user, service, "start", crew_id, task_id=task_id, body=body)


@router.post("/{task_id}/submissions", response_model=Envelope[TaskView], dependencies=[Depends(csrf_browser)])
async def submit_task(crew_id: UUID, task_id: UUID, body: SubmissionInput, request: Request, idempotency_key: IdempotencyKey,
                      user=Depends(completed_user), service=Depends(task_service)):
    return await run(request, user, service, "submit", crew_id, task_id=task_id, body=body, idempotency_key=str(idempotency_key))


@router.post("/{task_id}/reviews", response_model=Envelope[TaskView], dependencies=[Depends(csrf_browser)])
async def review_task(crew_id: UUID, task_id: UUID, body: ReviewInput, request: Request, idempotency_key: IdempotencyKey,
                      user=Depends(completed_user), service=Depends(task_service)):
    return await run(request, user, service, "review", crew_id, task_id=task_id, body=body, idempotency_key=str(idempotency_key))


@router.post("/{task_id}/cancel", response_model=Envelope[TaskView], dependencies=[Depends(csrf_browser)])
async def cancel_task(crew_id: UUID, task_id: UUID, body: CancelInput, request: Request, idempotency_key: IdempotencyKey,
                      user=Depends(completed_user), service=Depends(task_service)):
    return await run(request, user, service, "cancel", crew_id, task_id=task_id, body=body, idempotency_key=str(idempotency_key))
