from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response

from silencio_set.accounts.errors import AuthError
from silencio_set.api.accounts import completed_user, csrf_browser, auth_service, client_ip
from silencio_set.api.crews import Page
from silencio_set.api.tasks import IdempotencyKey
from silencio_set.call_sheets.schemas import CallSheetCreate, CallSheetVersionCreate, CallSheetEdit, RecipientEdit, CallSheetPublish, SheetPage, VersionPage, VersionView, ReviewPage, RecipientPage
from silencio_set.creative.schemas import Decision
from silencio_set.core.http import Envelope
from silencio_set.tasks.schemas import VersionInput

router = APIRouter(prefix='/api/v1/crews/{crew_id}', tags=['call-sheets'])


async def service(request: Request, user=Depends(completed_user), auth=Depends(auth_service)):
    instance = getattr(request.app.state, 'call_sheets', None)
    if instance is None: raise AuthError(503, 'SERVICE_UNAVAILABLE', '通告服务暂不可用')
    write = request.method != 'GET'
    exporting = request.url.path.endswith('/export.pdf')
    await auth.cache.limit('call-sheet-export' if exporting else 'call-sheet-write' if write else 'call-sheet-read', client_ip(request), str(user.id),
        ip_limit=10 if write or exporting else 120, identity_limit=10 if write or exporting else 120)
    return instance


async def run(request, user, instance, action, crew_id, **options):
    return Envelope(data=await instance.execute(user, action, request.state.request_id, crew_id=crew_id, **options), request_id=request.state.request_id)


@router.get('/call-sheets', response_model=Envelope[SheetPage])
async def listing(crew_id: UUID, plan_id: UUID, request: Request, page: Page=1, page_size: int=Query(default=20, ge=1, le=20), user=Depends(completed_user), instance=Depends(service)):
    return await run(request, user, instance, 'read', crew_id, plan_id=plan_id, page=page, page_size=page_size)


@router.post('/call-sheets', response_model=Envelope[VersionView], status_code=201, dependencies=[Depends(csrf_browser)])
async def create(crew_id: UUID, request: Request, body: CallSheetCreate, idempotency_key: IdempotencyKey, user=Depends(completed_user), instance=Depends(service)):
    return await run(request, user, instance, 'create', crew_id, body=body, idempotency_key=str(idempotency_key))


@router.get('/call-sheets/{sheet_id}/versions', response_model=Envelope[VersionPage])
async def versions(crew_id: UUID, sheet_id: UUID, request: Request, page: Page=1, page_size: int=Query(default=20, ge=1, le=20), user=Depends(completed_user), instance=Depends(service)):
    return await run(request, user, instance, 'read', crew_id, sheet_id=sheet_id, page=page, page_size=page_size)


@router.post('/call-sheets/{sheet_id}/versions', response_model=Envelope[VersionView], status_code=201, dependencies=[Depends(csrf_browser)])
async def new_version(crew_id: UUID, sheet_id: UUID, request: Request, body: CallSheetVersionCreate, idempotency_key: IdempotencyKey, user=Depends(completed_user), instance=Depends(service)):
    return await run(request, user, instance, 'version', crew_id, target_id=sheet_id, body=body, idempotency_key=str(idempotency_key))


@router.get('/call-sheet-versions/{version_id}', response_model=Envelope[VersionView])
async def detail(crew_id: UUID, version_id: UUID, request: Request, user=Depends(completed_user), instance=Depends(service)):
    return await run(request, user, instance, 'read', crew_id, version_id=version_id)


@router.get('/call-sheet-versions/{version_id}/export.pdf')
async def export_pdf(crew_id: UUID, version_id: UUID, request: Request, expected_lock_version: int=Query(ge=1, le=2_147_483_647), user=Depends(completed_user), instance=Depends(service)):
    content, filename = await instance.execute(user, 'export_pdf', request.state.request_id, crew_id=crew_id,
        version_id=version_id, expected_lock_version=expected_lock_version)
    return Response(content, media_type='application/pdf', headers={'Content-Disposition':f'attachment; filename="{filename}"'})


@router.patch('/call-sheet-versions/{version_id}', response_model=Envelope[VersionView], dependencies=[Depends(csrf_browser)])
async def edit(crew_id: UUID, version_id: UUID, request: Request, body: CallSheetEdit, idempotency_key: IdempotencyKey, user=Depends(completed_user), instance=Depends(service)):
    return await run(request, user, instance, 'edit', crew_id, target_id=version_id, body=body, idempotency_key=str(idempotency_key))


@router.put('/call-sheet-versions/{version_id}/recipients', response_model=Envelope[VersionView], dependencies=[Depends(csrf_browser)])
async def recipients(crew_id: UUID, version_id: UUID, request: Request, body: RecipientEdit, idempotency_key: IdempotencyKey, user=Depends(completed_user), instance=Depends(service)):
    return await run(request, user, instance, 'recipients', crew_id, target_id=version_id, body=body, idempotency_key=str(idempotency_key))


@router.get('/call-sheet-versions/{version_id}/recipient-options', response_model=Envelope[RecipientPage])
async def recipient_options(crew_id: UUID, version_id: UUID, request: Request, page: Page=1, page_size: int=Query(default=20, ge=1, le=20), user=Depends(completed_user), instance=Depends(service)):
    return await run(request, user, instance, 'recipient_options', crew_id, version_id=version_id, page=page, page_size=page_size)


@router.post('/call-sheet-versions/{version_id}/submit', response_model=Envelope[VersionView], dependencies=[Depends(csrf_browser)])
async def submit(crew_id: UUID, version_id: UUID, request: Request, body: VersionInput, idempotency_key: IdempotencyKey, user=Depends(completed_user), instance=Depends(service)):
    return await run(request, user, instance, 'submit', crew_id, target_id=version_id, body=body, idempotency_key=str(idempotency_key))


@router.get('/call-sheet-versions/{version_id}/reviews', response_model=Envelope[ReviewPage])
async def reviews(crew_id: UUID, version_id: UUID, request: Request, page: Page=1, page_size: int=Query(default=20, ge=1, le=20), user=Depends(completed_user), instance=Depends(service)):
    return await run(request, user, instance, 'read', crew_id, version_id=version_id, history=True, page=page, page_size=page_size)


@router.post('/call-sheet-versions/{version_id}/reviews', response_model=Envelope[VersionView], dependencies=[Depends(csrf_browser)])
async def review(crew_id: UUID, version_id: UUID, request: Request, body: Decision, idempotency_key: IdempotencyKey, user=Depends(completed_user), instance=Depends(service)):
    return await run(request, user, instance, 'review', crew_id, target_id=version_id, body=body, idempotency_key=str(idempotency_key))


@router.post('/call-sheet-versions/{version_id}/publish', response_model=Envelope[VersionView], dependencies=[Depends(csrf_browser)])
async def publish(crew_id: UUID, version_id: UUID, request: Request, body: CallSheetPublish, idempotency_key: IdempotencyKey, user=Depends(completed_user), instance=Depends(service)):
    return await run(request, user, instance, 'publish', crew_id, target_id=version_id, body=body, idempotency_key=str(idempotency_key))
