from uuid import UUID
from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from starlette.responses import FileResponse
from silencio_set.accounts.errors import AuthError
from silencio_set.api.accounts import auth_service, completed_user, csrf_browser, client_ip
from silencio_set.api.crews import Page
from silencio_set.api.tasks import IdempotencyKey
from silencio_set.core.http import Envelope
from silencio_set.scripts.schemas import ScriptCreate, ScriptText, WorkingVersion, ScriptResult, ScriptPage, VersionPage
from silencio_set.tasks.schemas import VersionInput

router = APIRouter(prefix="/api/v1/crews/{crew_id}/scripts", tags=["scripts"])


async def service(request: Request, user=Depends(completed_user), auth=Depends(auth_service)):
    instance = getattr(request.app.state, "scripts", None)
    if instance is None: raise AuthError(503, "SERVICE_UNAVAILABLE", "剧本服务暂不可用")
    writing = request.method != "GET"
    await auth.cache.limit("script-write" if writing else "script-read", client_ip(request), str(user.id),
        ip_limit=10 if writing else 120, identity_limit=10 if writing else 120)
    return instance


async def run(request, user, instance, action, crew_id, **options):
    return Envelope(data=await instance.execute(user, action, request.state.request_id, crew_id=crew_id, **options), request_id=request.state.request_id)


@router.get("", response_model=Envelope[ScriptPage])
async def list_scripts(crew_id: UUID, request: Request, page: Page = 1, page_size: int = Query(default=20, ge=1, le=20), user=Depends(completed_user), instance=Depends(service)):
    return await run(request, user, instance, "read", crew_id, page=page, page_size=page_size)


@router.post("", response_model=Envelope[ScriptResult], status_code=201, dependencies=[Depends(csrf_browser)])
async def create_script(crew_id: UUID, request: Request, body: ScriptCreate, idempotency_key: IdempotencyKey, user=Depends(completed_user), instance=Depends(service)):
    return await run(request, user, instance, "create", crew_id, body=body, idempotency_key=str(idempotency_key))


@router.get("/{script_id}", response_model=Envelope[ScriptResult])
async def script_detail(crew_id: UUID, script_id: UUID, request: Request, user=Depends(completed_user), instance=Depends(service)):
    return await run(request, user, instance, "read", crew_id, script_id=script_id)


@router.get("/{script_id}/versions", response_model=Envelope[VersionPage])
async def versions(crew_id: UUID, script_id: UUID, request: Request, page: Page = 1, page_size: int = Query(default=20, ge=1, le=20), user=Depends(completed_user), instance=Depends(service)):
    return await run(request, user, instance, "read", crew_id, script_id=script_id, versions=True, page=page, page_size=page_size)


@router.get("/{script_id}/versions/{version_id}", response_model=Envelope[ScriptResult])
async def version_detail(crew_id: UUID, script_id: UUID, version_id: UUID, request: Request, user=Depends(completed_user), instance=Depends(service)):
    return await run(request, user, instance, "read", crew_id, script_id=script_id, version_id=version_id)


@router.post("/{script_id}/versions", response_model=Envelope[ScriptResult], status_code=201, dependencies=[Depends(csrf_browser)])
async def add_version(crew_id: UUID, script_id: UUID, request: Request, body: ScriptText, idempotency_key: IdempotencyKey, user=Depends(completed_user), instance=Depends(service)):
    return await run(request, user, instance, "add_version", crew_id, script_id=script_id, body=body, idempotency_key=str(idempotency_key))


@router.post('/{script_id}/imports', response_model=Envelope[ScriptResult], status_code=201, dependencies=[Depends(csrf_browser)])
async def import_file(crew_id: UUID, script_id: UUID, request: Request, idempotency_key: IdempotencyKey,
    file: UploadFile=File(), expected_lock_version: int=Form(ge=1, le=2_147_483_647), user=Depends(completed_user), instance=Depends(service)):
    try:
        return await run(request, user, instance, 'import_file', crew_id, script_id=script_id, upload=file,
            expected_lock_version=expected_lock_version, idempotency_key=str(idempotency_key))
    finally:
        await file.close()


@router.get('/{script_id}/versions/{version_id}/source-file')
async def source_file(crew_id: UUID, script_id: UUID, version_id: UUID, request: Request, user=Depends(completed_user), instance=Depends(service)):
    source = await instance.execute(user, 'source_file', request.state.request_id, crew_id=crew_id, script_id=script_id, version_id=version_id)
    path = instance.storage.path(source.storage_key)
    if not path.is_file(): raise AuthError(503, 'SOURCE_FILE_UNAVAILABLE', '原始文件暂不可读取')
    # Originals are downloaded, never served as executable inline content.
    return FileResponse(path, filename=source.original_name, media_type='application/octet-stream')


@router.patch("/{script_id}/versions/{version_id}", response_model=Envelope[ScriptResult], dependencies=[Depends(csrf_browser)])
async def correct_version(crew_id: UUID, script_id: UUID, version_id: UUID, request: Request, body: ScriptText, idempotency_key: IdempotencyKey, user=Depends(completed_user), instance=Depends(service)):
    return await run(request, user, instance, "correct", crew_id, script_id=script_id, version_id=version_id, body=body, idempotency_key=str(idempotency_key))


@router.post("/{script_id}/versions/{version_id}/confirm", response_model=Envelope[ScriptResult], dependencies=[Depends(csrf_browser)])
async def confirm_version(crew_id: UUID, script_id: UUID, version_id: UUID, request: Request, body: VersionInput, idempotency_key: IdempotencyKey, user=Depends(completed_user), instance=Depends(service)):
    return await run(request, user, instance, "confirm", crew_id, script_id=script_id, version_id=version_id, body=body, idempotency_key=str(idempotency_key))


@router.put("/{script_id}/working-version", response_model=Envelope[ScriptResult], dependencies=[Depends(csrf_browser)])
async def working_version(crew_id: UUID, script_id: UUID, request: Request, body: WorkingVersion, idempotency_key: IdempotencyKey, user=Depends(completed_user), instance=Depends(service)):
    return await run(request, user, instance, "set_working", crew_id, script_id=script_id, body=body, idempotency_key=str(idempotency_key))
