from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from starlette.responses import FileResponse

from silencio_set.accounts.errors import AuthError
from silencio_set.api.accounts import auth_service, client_ip, completed_user, csrf_browser
from silencio_set.api.tasks import IdempotencyKey
from silencio_set.core.http import Envelope
from silencio_set.locations.images import CONTENT_TYPES
from silencio_set.locations.schemas import LocationDetailsEdit, LocationView, PhotoDescriptionEdit
from silencio_set.tasks.schemas import VersionInput

router=APIRouter(prefix='/api/v1/crews/{crew_id}/locations',tags=['locations'])


async def service(request:Request,user=Depends(completed_user),auth=Depends(auth_service)):
    instance=getattr(request.app.state,'locations',None)
    if instance is None: raise AuthError(503,'SERVICE_UNAVAILABLE','场地服务暂不可用')
    write=request.method!='GET'
    await auth.cache.limit('location-write' if write else 'location-read',client_ip(request),str(user.id),
        ip_limit=10 if write else 120,identity_limit=10 if write else 120)
    return instance


async def run(request,user,instance,action,crew_id,**options):
    return Envelope(data=await instance.execute(user,action,request.state.request_id,crew_id=crew_id,**options),request_id=request.state.request_id)


@router.get('/{resource_id}',response_model=Envelope[LocationView])
async def get_location(crew_id:UUID,resource_id:UUID,request:Request,user=Depends(completed_user),instance=Depends(service)):
    return await run(request,user,instance,'get',crew_id,resource_id=resource_id)


@router.patch('/{resource_id}',response_model=Envelope[LocationView],dependencies=[Depends(csrf_browser)])
async def edit_details(crew_id:UUID,resource_id:UUID,request:Request,body:LocationDetailsEdit,idempotency_key:IdempotencyKey,user=Depends(completed_user),instance=Depends(service)):
    return await run(request,user,instance,'edit_details',crew_id,resource_id=resource_id,body=body,idempotency_key=str(idempotency_key))


@router.post('/{resource_id}/photos',response_model=Envelope[LocationView],status_code=201,dependencies=[Depends(csrf_browser)])
async def upload(crew_id:UUID,resource_id:UUID,request:Request,idempotency_key:IdempotencyKey,file:UploadFile=File(),
    expected_lock_version:int=Form(ge=1,le=2_147_483_647),description:str|None=Form(default=None,max_length=500),user=Depends(completed_user),instance=Depends(service)):
    try:
        return await run(request,user,instance,'upload_photo',crew_id,resource_id=resource_id,upload=file,
            expected_lock_version=expected_lock_version,description=description,idempotency_key=str(idempotency_key))
    finally: await file.close()


@router.patch('/{resource_id}/photos/{photo_id}',response_model=Envelope[LocationView],dependencies=[Depends(csrf_browser)])
async def edit_photo(crew_id:UUID,resource_id:UUID,photo_id:UUID,request:Request,body:PhotoDescriptionEdit,idempotency_key:IdempotencyKey,user=Depends(completed_user),instance=Depends(service)):
    return await run(request,user,instance,'edit_photo',crew_id,resource_id=resource_id,photo_id=photo_id,body=body,idempotency_key=str(idempotency_key))


@router.post('/{resource_id}/photos/{photo_id}/remove',response_model=Envelope[LocationView],dependencies=[Depends(csrf_browser)])
async def remove_photo(crew_id:UUID,resource_id:UUID,photo_id:UUID,request:Request,body:VersionInput,idempotency_key:IdempotencyKey,user=Depends(completed_user),instance=Depends(service)):
    return await run(request,user,instance,'remove_photo',crew_id,resource_id=resource_id,photo_id=photo_id,body=body,idempotency_key=str(idempotency_key))


@router.get('/{resource_id}/photos/{photo_id}/content')
async def content(crew_id:UUID,resource_id:UUID,photo_id:UUID,request:Request,variant:Literal['preview','full']=Query(default='preview'),user=Depends(completed_user),instance=Depends(service)):
    photo=await instance.execute(user,'content',request.state.request_id,crew_id=crew_id,resource_id=resource_id,photo_id=photo_id)
    key=photo.preview_storage_key if variant=='preview' else photo.storage_key
    path=instance.storage.path(key)
    if not path.is_file(): raise AuthError(503,'PHOTO_FILE_UNAVAILABLE','照片暂不可读取')
    extension='webp' if variant=='preview' else photo.file_format
    return FileResponse(path,media_type='image/webp' if variant=='preview' else CONTENT_TYPES[photo.file_format],
        headers={'Content-Disposition':f'inline; filename="location-{photo.id.hex}.{extension}"','Cross-Origin-Resource-Policy':'same-origin'})
