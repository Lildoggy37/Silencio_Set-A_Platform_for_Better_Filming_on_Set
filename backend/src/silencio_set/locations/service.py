from hashlib import sha256
from uuid import uuid4

import anyio
from pydantic import ValidationError
from sqlalchemy.exc import DBAPIError

from silencio_set.accounts.errors import AuthError
from silencio_set.accounts.profile import require_completed_profile, AccountUnavailable, ProfileIncomplete
from silencio_set.locations.images import MAX_BYTES, normalize_in_process
from silencio_set.locations.models import LocationPhoto
from silencio_set.locations.schemas import PhotoUploadIdentity


class LocationService:
    def __init__(self,repository,audits,storage):
        self.repository,self.audits,self.storage=repository,audits,storage
        self.image_limiter=anyio.CapacityLimiter(1)

    async def upload_photo(self,user,crew_id,resource_id,upload,expected_lock_version,description,request_id,idempotency_key):
        data=await upload.read(MAX_BYTES+1)
        if len(data)>MAX_BYTES: raise AuthError(413,'PHOTO_TOO_LARGE','图片不能超过10MiB')
        try:
            body=PhotoUploadIdentity(expected_lock_version=expected_lock_version,description=description,source_sha256=sha256(data).hexdigest())
        except ValidationError: raise AuthError(422,'VALIDATION_ERROR','请核对照片说明和版本') from None
        previous=await self.repository.prepare_upload(user.id,crew_id,resource_id,body,idempotency_key)
        if previous is not None: return previous
        processed=await anyio.to_thread.run_sync(normalize_in_process,data,limiter=self.image_limiter)
        photo_id=uuid4()
        key=f'{crew_id.hex}/{photo_id.hex}.{processed.file_format}'
        preview_key=f'{crew_id.hex}/{photo_id.hex}-preview.webp'
        await anyio.to_thread.run_sync(self.storage.put,key,processed.content)
        await anyio.to_thread.run_sync(self.storage.put,preview_key,processed.preview)
        photo=LocationPhoto(id=photo_id,crew_id=crew_id,location_resource_id=resource_id,description=body.description,
            file_format=processed.file_format,byte_size=len(processed.content),width_px=processed.width_px,height_px=processed.height_px,
            storage_key=key,preview_storage_key=preview_key,preview_byte_size=len(processed.preview),
            sha256=sha256(processed.content).hexdigest(),removed_at=None,removed_by=None)
        return await self.repository.mutate(user.id,crew_id,resource_id,'upload_photo',body,request_id,
            idempotency_key=idempotency_key,new_photo=photo)

    async def execute(self,user,action,request_id,*,crew_id,**options):
        try:
            require_completed_profile(user)
            if action in {'get','content'}: return await getattr(self.repository,action)(user.id,crew_id,**options)
            if action=='upload_photo': return await self.upload_photo(user,crew_id,request_id=request_id,**options)
            return await self.repository.mutate(user.id,crew_id,action=action,body=options.pop('body'),request_id=request_id,**options)
        except (AuthError,DBAPIError,OSError,AccountUnavailable,ProfileIncomplete) as error:
            if isinstance(error,AccountUnavailable): error=AuthError(401,'SESSION_EXPIRED','会话已失效')
            elif isinstance(error,ProfileIncomplete): error=AuthError(403,'PROFILE_INCOMPLETE','请先完善昵称')
            await self.audits.record_failure(user.id,f'location.{action}',crew_id,
                'denied' if isinstance(error,AuthError) and error.status<500 else 'failed',
                error.code if isinstance(error,AuthError) else 'SERVICE_UNAVAILABLE',request_id)
            if isinstance(error,AuthError): raise error
            raise AuthError(503,'SERVICE_UNAVAILABLE','场地资料服务暂不可用，请读取当前状态') from None
