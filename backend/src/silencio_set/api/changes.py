from uuid import UUID
from typing import Literal
from fastapi import APIRouter, Depends, Query, Request
from silencio_set.accounts.errors import AuthError
from silencio_set.api.accounts import auth_service, client_ip, completed_user, csrf_browser
from silencio_set.api.crews import Page
from silencio_set.api.tasks import IdempotencyKey
from silencio_set.notifications.schemas import EmptyInput
from silencio_set.changes.schemas import Category, ChangeIssuePage, ImpactCheckView, RecordedIssuePage, ImpactHistoryPage
from silencio_set.core.http import Envelope

router = APIRouter(prefix='/api/v1/crews/{crew_id}/change-issues', tags=['change-workbench'])


async def service(request: Request, user=Depends(completed_user), auth=Depends(auth_service)):
    value = request.app.state.changes
    if value is None: raise AuthError(503, 'SERVICE_UNAVAILABLE', '变更清单暂不可用')
    writing=request.method=='POST'
    await auth.cache.limit('changes-write' if writing else 'changes-read',client_ip(request),str(user.id),
        ip_limit=10 if writing else 120,identity_limit=10 if writing else 120)
    return value


@router.get('/{category}', response_model=Envelope[ChangeIssuePage])
async def issues(crew_id: UUID, category: Category, request: Request, page: Page=1,
                 page_size: int=Query(default=20, ge=1, le=20), user=Depends(completed_user), instance=Depends(service)):
    return Envelope(data=await instance.list(user, crew_id, request.state.request_id, category=category, page=page, page_size=page_size), request_id=request.state.request_id)


@router.post('/{category}/checks',response_model=Envelope[ImpactCheckView],status_code=201,dependencies=[Depends(csrf_browser)])
async def check(crew_id: UUID,category: Category,request: Request,body: EmptyInput,idempotency_key: IdempotencyKey,
                user=Depends(completed_user),instance=Depends(service)):
    result=await instance.execute(user,'check',crew_id,request.state.request_id,category=category,body=body,idempotency_key=str(idempotency_key))
    return Envelope(data=result,request_id=request.state.request_id)


@router.get('/{category}/records',response_model=Envelope[RecordedIssuePage])
async def records(crew_id: UUID,category: Category,request: Request,status: Literal['all','open','resolved']='all',page: Page=1,
                  page_size: int=Query(default=20,ge=1,le=20),user=Depends(completed_user),instance=Depends(service)):
    result=await instance.execute(user,'records',crew_id,request.state.request_id,category=category,status=status,page=page,page_size=page_size)
    return Envelope(data=result,request_id=request.state.request_id)


@router.get('/{category}/records/{issue_id}/history',response_model=Envelope[ImpactHistoryPage])
async def history(crew_id: UUID,category: Category,issue_id: UUID,request: Request,page: Page=1,
                  page_size: int=Query(default=20,ge=1,le=20),user=Depends(completed_user),instance=Depends(service)):
    result=await instance.execute(user,'history',crew_id,request.state.request_id,category=category,issue_id=issue_id,page=page,page_size=page_size)
    return Envelope(data=result,request_id=request.state.request_id)
