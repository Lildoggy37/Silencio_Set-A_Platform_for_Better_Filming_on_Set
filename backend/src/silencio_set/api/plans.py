from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from silencio_set.accounts.errors import AuthError
from silencio_set.api.accounts import completed_user, csrf_browser, auth_service, client_ip
from silencio_set.api.crews import Page
from silencio_set.api.tasks import IdempotencyKey
from silencio_set.core.http import Envelope
from silencio_set.plans.schemas import PlanCreate, VersionCreate, SessionsSave, PlanView, PlanPage, VersionView, VersionPage, ChoicePage, PublicationPreflight, PublishInput, PublishView, NextSession
from silencio_set.tasks.schemas import VersionInput
from silencio_set.plans.schemas import WithdrawInput, WithdrawView

router = APIRouter(prefix='/api/v1/crews/{crew_id}', tags=['shooting-plans'])


async def service(request: Request, user=Depends(completed_user), auth=Depends(auth_service)):
    value = getattr(request.app.state, 'plans', None)
    if value is None:
        raise AuthError(503, 'SERVICE_UNAVAILABLE', '拍摄计划服务暂不可用')
    writing = request.method != 'GET'
    await auth.cache.limit('plan-write' if writing else 'plan-read', client_ip(request), str(user.id), ip_limit=10 if writing else 120, identity_limit=10 if writing else 120)
    return value


async def run(request, user, instance, action, crew_id, **options):
    return Envelope(data=await instance.execute(user, action, request.state.request_id, crew_id=crew_id, **options), request_id=request.state.request_id)


@router.get('/shooting-plans', response_model=Envelope[PlanPage])
async def plans(crew_id: UUID, request: Request, page: Page=1, page_size: int=Query(default=20, ge=1, le=20), user=Depends(completed_user), instance=Depends(service)):
    return await run(request, user, instance, 'read', crew_id, page=page, page_size=page_size)


@router.post('/shooting-plans', response_model=Envelope[PlanView], status_code=201, dependencies=[Depends(csrf_browser)])
async def create(crew_id: UUID, request: Request, body: PlanCreate, idempotency_key: IdempotencyKey, user=Depends(completed_user), instance=Depends(service)):
    return await run(request, user, instance, 'create', crew_id, body=body, idempotency_key=str(idempotency_key))


@router.get('/shooting-plans/{plan_id}', response_model=Envelope[PlanView])
async def plan_metadata(crew_id: UUID, plan_id: UUID, request: Request, user=Depends(completed_user), instance=Depends(service)):
    return await run(request, user, instance, 'get_plan', crew_id, plan_id=plan_id)


@router.get('/shooting-plans/{plan_id}/versions', response_model=Envelope[VersionPage])
async def versions(crew_id: UUID, plan_id: UUID, request: Request, page: Page=1, page_size: int=Query(default=20, ge=1, le=20), user=Depends(completed_user), instance=Depends(service)):
    return await run(request, user, instance, 'read', crew_id, plan_id=plan_id, page=page, page_size=page_size)


@router.post('/shooting-plans/{plan_id}/versions', response_model=Envelope[VersionView], status_code=201, dependencies=[Depends(csrf_browser)])
async def new_version(crew_id: UUID, plan_id: UUID, request: Request, body: VersionCreate, idempotency_key: IdempotencyKey, user=Depends(completed_user), instance=Depends(service)):
    return await run(request, user, instance, 'version', crew_id, body=body, object_id=plan_id, idempotency_key=str(idempotency_key))


@router.get('/plan-versions/{version_id}', response_model=Envelope[VersionView])
async def detail(crew_id: UUID, version_id: UUID, request: Request, user=Depends(completed_user), instance=Depends(service)):
    return await run(request, user, instance, 'read', crew_id, version_id=version_id)


@router.put('/plan-versions/{version_id}/sessions', response_model=Envelope[VersionView], dependencies=[Depends(csrf_browser)])
async def save(crew_id: UUID, version_id: UUID, request: Request, body: SessionsSave, idempotency_key: IdempotencyKey, user=Depends(completed_user), instance=Depends(service)):
    return await run(request, user, instance, 'save', crew_id, body=body, object_id=version_id, idempotency_key=str(idempotency_key))


@router.post('/plan-versions/{version_id}/preflight', response_model=Envelope[PublicationPreflight], dependencies=[Depends(csrf_browser)])
async def preflight(crew_id: UUID, version_id: UUID, request: Request, body: VersionInput, user=Depends(completed_user), instance=Depends(service)):
    return await run(request, user, instance, 'preflight', crew_id, version_id=version_id, body=body)


@router.post('/plan-versions/{version_id}/publish', response_model=Envelope[PublishView], dependencies=[Depends(csrf_browser)])
async def publish(crew_id: UUID, version_id: UUID, request: Request, body: PublishInput, idempotency_key: IdempotencyKey, user=Depends(completed_user), instance=Depends(service)):
    return await run(request, user, instance, 'publish', crew_id, version_id=version_id, body=body, idempotency_key=str(idempotency_key))


@router.post('/plan-versions/{version_id}/withdraw', response_model=Envelope[WithdrawView], dependencies=[Depends(csrf_browser)])
async def withdraw(crew_id: UUID, version_id: UUID, request: Request, body: WithdrawInput, idempotency_key: IdempotencyKey, user=Depends(completed_user), instance=Depends(service)):
    return await run(request, user, instance, 'withdraw', crew_id, version_id=version_id, body=body, idempotency_key=str(idempotency_key))


@router.get('/shooting-sessions/next', response_model=Envelope[NextSession | None])
async def next_session(crew_id: UUID, request: Request, user=Depends(completed_user), instance=Depends(service)):
    return await run(request, user, instance, 'next', crew_id)


@router.get('/shooting-plan-options/{kind}', response_model=Envelope[ChoicePage])
async def choices(crew_id: UUID, kind: Literal['scenes', 'members', 'resources', 'reservations'], request: Request, resource_id: UUID | None=None, page: Page=1, page_size: int=Query(default=20, ge=1, le=20), user=Depends(completed_user), instance=Depends(service)):
    return await run(request, user, instance, 'choices', crew_id, kind=kind, resource_id=resource_id, page=page, page_size=page_size)
