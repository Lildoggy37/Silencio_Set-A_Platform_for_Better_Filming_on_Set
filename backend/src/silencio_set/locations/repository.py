from hashlib import sha256

from sqlalchemy import func, select

from silencio_set.accounts.errors import AuthError
from silencio_set.crews.models import AuditLog
from silencio_set.crews.repository import not_found
from silencio_set.locations.models import LocationDetails, LocationPhoto
from silencio_set.locations.schemas import LocationDetailsView, LocationView, PhotoView
from silencio_set.resources.domain import check_version
from silencio_set.resources.models import Resource, ResourceReceipt
from silencio_set.resources.repository import resource_actor, require, can_manage, scope_codes
from silencio_set.tasks.repository import request_digest

MAX_PHOTOS = 20


async def location(session, crew_id, resource_id):
    row=await session.scalar(select(Resource).where(Resource.crew_id==crew_id,Resource.id==resource_id,Resource.resource_type=='location'))
    if row is None: raise not_found()
    return row


async def editable(session, actor, resource):
    return can_manage(actor,await scope_codes(session,resource,actor))


async def require_editor(session, actor, resource):
    if not await editable(session,actor,resource): raise AuthError(403,'FORBIDDEN','当前没有维护该场地的权限')


async def require_capacity(session, resource):
    count=await session.scalar(select(func.count()).select_from(LocationPhoto).where(LocationPhoto.crew_id==resource.crew_id,
        LocationPhoto.location_resource_id==resource.id,LocationPhoto.removed_at.is_(None)))
    if count>=MAX_PHOTOS: raise AuthError(409,'LOCATION_PHOTO_LIMIT','每个场地最多保留20张照片，请先移除不再需要的照片')


async def replay(session,user_id,actor,resource_id,crew_id,action,body,key,target_id=None):
    receipt=await session.scalar(select(ResourceReceipt).where(ResourceReceipt.crew_id==crew_id,ResourceReceipt.user_id==user_id,
        ResourceReceipt.key_digest==sha256(key.encode()).hexdigest()))
    if receipt is None: return None
    if receipt.membership_joined_at!=actor.joined_at: raise AuthError(409,'MEMBERSHIP_CHANGED','入组身份已变化')
    if receipt.request_digest!=request_digest(f'location.{action}',target_id or resource_id,body):
        raise AuthError(409,'IDEMPOTENCY_KEY_REUSED','同键不能用于不同操作内容')
    return LocationView.model_validate(receipt.response_snapshot)


async def view(session,resource,actor):
    details=await session.scalar(select(LocationDetails).where(LocationDetails.crew_id==resource.crew_id,LocationDetails.location_resource_id==resource.id))
    photos=(await session.scalars(select(LocationPhoto).where(LocationPhoto.crew_id==resource.crew_id,
        LocationPhoto.location_resource_id==resource.id,LocationPhoto.removed_at.is_(None)).order_by(LocationPhoto.created_at,LocationPhoto.id))).all()
    return LocationView(resource_id=resource.id,resource_name=resource.name,resource_status=resource.status,resource_lock_version=resource.lock_version,
        details=LocationDetailsView.model_validate(details) if details else LocationDetailsView(),photos=[PhotoView.model_validate(row) for row in photos],
        allowed_actions=['edit_details','upload_photo','edit_photo','remove_photo'] if await editable(session,actor,resource) else [])


class LocationRepository:
    def __init__(self,sessions): self.sessions=sessions

    async def get(self,user_id,crew_id,resource_id):
        async with self.sessions() as session,session.begin():
            actor=await resource_actor(session,user_id,crew_id,writing=True); require(actor,'resource.read')
            return await view(session,await location(session,crew_id,resource_id),actor)

    async def content(self,user_id,crew_id,resource_id,photo_id):
        async with self.sessions() as session,session.begin():
            actor=await resource_actor(session,user_id,crew_id,writing=True); require(actor,'resource.read')
            await location(session,crew_id,resource_id)
            photo=await session.scalar(select(LocationPhoto).where(LocationPhoto.crew_id==crew_id,
                LocationPhoto.location_resource_id==resource_id,LocationPhoto.id==photo_id,LocationPhoto.removed_at.is_(None)))
            if photo is None: raise not_found()
            return photo

    async def prepare_upload(self,user_id,crew_id,resource_id,body,key):
        async with self.sessions() as session,session.begin():
            actor=await resource_actor(session,user_id,crew_id,writing=True); require(actor,'resource.read')
            resource=await location(session,crew_id,resource_id); await require_editor(session,actor,resource)
            previous=await replay(session,user_id,actor,resource_id,crew_id,'upload_photo',body,key)
            if previous is not None: return previous
            check_version(resource,body.expected_lock_version); await require_capacity(session,resource)
            return None

    async def mutate(self,user_id,crew_id,resource_id,action,body,request_id,*,idempotency_key,photo_id=None,new_photo=None):
        async with self.sessions() as session,session.begin():
            actor=await resource_actor(session,user_id,crew_id,writing=True); require(actor,'resource.read')
            resource=await location(session,crew_id,resource_id); await require_editor(session,actor,resource)
            previous=await replay(session,user_id,actor,resource_id,crew_id,action,body,idempotency_key,photo_id)
            if previous is not None: return previous
            check_version(resource,body.expected_lock_version)
            now=await session.scalar(select(func.clock_timestamp()))
            if action=='edit_details':
                details=await session.scalar(select(LocationDetails).where(LocationDetails.crew_id==crew_id,LocationDetails.location_resource_id==resource_id))
                if details is None:
                    details=LocationDetails(crew_id=crew_id,location_resource_id=resource_id,address=None,access_instructions=None,shooting_constraints=None)
                    session.add(details)
                for field,value in body.model_dump(exclude={'expected_lock_version'},exclude_unset=True).items(): setattr(details,field,value)
            elif action=='upload_photo':
                await require_capacity(session,resource)
                new_photo.created_by=actor.member_id;new_photo.created_at=now
                session.add(new_photo)
            elif action in {'edit_photo','remove_photo'}:
                photo=await session.scalar(select(LocationPhoto).where(LocationPhoto.crew_id==crew_id,
                    LocationPhoto.location_resource_id==resource_id,LocationPhoto.id==photo_id,LocationPhoto.removed_at.is_(None)))
                if photo is None: raise not_found()
                if action=='edit_photo': photo.description=body.description
                else: photo.removed_at=now;photo.removed_by=actor.member_id
            else: raise ValueError('Unknown location action')
            resource.lock_version+=1;resource.updated_at=now
            await session.flush()
            result=await view(session,resource,actor)
            session.add(ResourceReceipt(crew_id=crew_id,user_id=user_id,membership_joined_at=actor.joined_at,resource_id=resource_id,
                key_digest=sha256(idempotency_key.encode()).hexdigest(),request_digest=request_digest(f'location.{action}',photo_id or resource_id,body),
                response_snapshot=result.model_dump(mode='json')))
            session.add(AuditLog(crew_id=crew_id,actor_type='user',actor_id=user_id,action=f'location.{action}',target_type='resource',target_id=resource_id,
                result='success',request_id=request_id,after_summary={'lock_version':resource.lock_version,'photo_id':str(new_photo.id if new_photo is not None else photo_id) if action!='edit_details' else None}))
            await session.flush()
        return result
