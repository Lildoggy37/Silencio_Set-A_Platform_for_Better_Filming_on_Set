from typing import Literal
from uuid import UUID
from starlette.convertors import Convertor, register_url_convertor
from fastapi import APIRouter, Depends, Request, Query
from silencio_set.accounts.errors import AuthError
from silencio_set.api.accounts import completed_user, csrf_browser, auth_service, client_ip
from silencio_set.api.tasks import IdempotencyKey
from silencio_set.api.crews import Page
from silencio_set.core.http import Envelope
from silencio_set.creative.schemas import Source, SceneSave, ShotSave, Decision, CreativeView, CreativePage, ReviewPage
from silencio_set.tasks.schemas import VersionInput
from silencio_set.creative.casting_schemas import CastingPage, CastingSave, CastingResult, ActorPage, CastingScriptPage
from silencio_set.creative.source_schemas import SourceAssessment, SourceReviewInput, SourceReviewPage, SourceImpactPage

class CreativeKindConvertor(Convertor):
    regex = 'breakdowns|storyboards'
    def convert(self, value): return value
    def to_string(self, value): return value

register_url_convertor('creative_kind', CreativeKindConvertor())
Kind=Literal['breakdowns','storyboards']
router=APIRouter(prefix='/api/v1/crews/{crew_id}',tags=['creative'])


async def service(request: Request,user=Depends(completed_user),auth=Depends(auth_service)):
    value=getattr(request.app.state,'creative',None)
    if value is None: raise AuthError(503,'SERVICE_UNAVAILABLE','创作资料服务暂不可用')
    writing=request.method!='GET'
    await auth.cache.limit('creative-write' if writing else 'creative-read',client_ip(request),str(user.id),ip_limit=10 if writing else 120,identity_limit=10 if writing else 120)
    return value


async def run(request,user,instance,action,crew_id,kind,**options):
    return Envelope(data=await instance.execute(user,action,request.state.request_id,crew_id=crew_id,kind=kind,**options),request_id=request.state.request_id)


@router.get('/scripts/{script_id}/source-impacts', response_model=Envelope[SourceImpactPage])
async def source_impacts(crew_id: UUID, script_id: UUID, kind: Kind, request: Request, page: Page=1,
                         user=Depends(completed_user), instance=Depends(service)):
    return await run(request, user, instance, 'source_impacts', crew_id, kind, script_id=script_id, page=page)


@router.get('/{kind:creative_kind}/{object_id}/source-assessment', response_model=Envelope[SourceAssessment])
async def source_assessment(crew_id: UUID, kind: Kind, object_id: UUID, request: Request,
                            user=Depends(completed_user), instance=Depends(service)):
    return await run(request, user, instance, 'source_assessment', crew_id, kind, object_id=object_id)


@router.get('/{kind:creative_kind}/{object_id}/source-reviews', response_model=Envelope[SourceReviewPage])
async def source_review_history(crew_id: UUID, kind: Kind, object_id: UUID, request: Request, page: Page=1,
                               user=Depends(completed_user), instance=Depends(service)):
    return await run(request, user, instance, 'source_history', crew_id, kind, object_id=object_id, page=page)


@router.post('/{kind:creative_kind}/{object_id}/source-reviews', response_model=Envelope[SourceAssessment], status_code=201, dependencies=[Depends(csrf_browser)])
async def source_review(crew_id: UUID, kind: Kind, object_id: UUID, request: Request, body: SourceReviewInput, idempotency_key: IdempotencyKey,
                        user=Depends(completed_user), instance=Depends(service)):
    return await run(request, user, instance, 'source_review', crew_id, kind, object_id=object_id, body=body, idempotency_key=str(idempotency_key))


@router.get('/casting-scripts', response_model=Envelope[CastingScriptPage])
async def casting_scripts(crew_id: UUID, request: Request, page: Page=1,
                          user=Depends(completed_user), instance=Depends(service)):
    return await run(request, user, instance, 'read', crew_id, 'casting', page=page)


@router.get('/scripts/{object_id}/casting', response_model=Envelope[CastingPage])
async def casting_list(crew_id: UUID, object_id: UUID, request: Request, page: Page=1,
                       user=Depends(completed_user), instance=Depends(service)):
    return await run(request, user, instance, 'read', crew_id, 'casting', object_id=object_id, page=page)


@router.get('/scripts/{object_id}/casting/actors', response_model=Envelope[ActorPage])
async def casting_actors(crew_id: UUID, object_id: UUID, request: Request, page: Page=1,
                         user=Depends(completed_user), instance=Depends(service)):
    return await run(request, user, instance, 'read', crew_id, 'casting', object_id=object_id, page=page, actors=True)


@router.patch('/scripts/{object_id}/casting', response_model=Envelope[CastingResult], dependencies=[Depends(csrf_browser)])
async def casting_save(crew_id: UUID, object_id: UUID, request: Request, body: CastingSave, idempotency_key: IdempotencyKey,
                       user=Depends(completed_user), instance=Depends(service)):
    return await run(request, user, instance, 'save', crew_id, 'casting', object_id=object_id, body=body, idempotency_key=str(idempotency_key))


@router.get('/{kind:creative_kind}',response_model=Envelope[CreativePage])
async def listing(crew_id: UUID,kind: Kind,request: Request,source_id: UUID,page: Page=1,page_size: int=Query(default=20,ge=1,le=20),user=Depends(completed_user),instance=Depends(service)):
    return await run(request,user,instance,'read',crew_id,kind,source_id=source_id,page=page,page_size=page_size)


@router.get('/{kind:creative_kind}/{object_id}',response_model=Envelope[CreativeView])
async def detail(crew_id: UUID,kind: Kind,object_id: UUID,request: Request,user=Depends(completed_user),instance=Depends(service)):
    return await run(request,user,instance,'read',crew_id,kind,object_id=object_id)


@router.get('/{kind:creative_kind}/{object_id}/reviews',response_model=Envelope[ReviewPage])
async def reviews(crew_id: UUID,kind: Kind,object_id: UUID,request: Request,page: Page=1,page_size: int=Query(default=20,ge=1,le=20),user=Depends(completed_user),instance=Depends(service)):
    return await run(request,user,instance,'read',crew_id,kind,object_id=object_id,history=True,page=page,page_size=page_size)


@router.post('/{kind:creative_kind}',response_model=Envelope[CreativeView],status_code=201,dependencies=[Depends(csrf_browser)])
async def create(crew_id: UUID,kind: Kind,request: Request,body: Source,idempotency_key: IdempotencyKey,user=Depends(completed_user),instance=Depends(service)):
    return await run(request,user,instance,'create',crew_id,kind,body=body,idempotency_key=str(idempotency_key))


@router.put('/breakdowns/{object_id}',response_model=Envelope[CreativeView],dependencies=[Depends(csrf_browser)])
async def save_scenes(crew_id: UUID,object_id: UUID,request: Request,body: SceneSave,idempotency_key: IdempotencyKey,user=Depends(completed_user),instance=Depends(service)):
    return await run(request,user,instance,'save',crew_id,'breakdowns',object_id=object_id,body=body,idempotency_key=str(idempotency_key))


@router.put('/storyboards/{object_id}',response_model=Envelope[CreativeView],dependencies=[Depends(csrf_browser)])
async def save_shots(crew_id: UUID,object_id: UUID,request: Request,body: ShotSave,idempotency_key: IdempotencyKey,user=Depends(completed_user),instance=Depends(service)):
    return await run(request,user,instance,'save',crew_id,'storyboards',object_id=object_id,body=body,idempotency_key=str(idempotency_key))


@router.post('/{kind:creative_kind}/{object_id}/reviews',response_model=Envelope[CreativeView],dependencies=[Depends(csrf_browser)])
async def review(crew_id: UUID,kind: Kind,object_id: UUID,request: Request,body: Decision,idempotency_key: IdempotencyKey,user=Depends(completed_user),instance=Depends(service)):
    return await run(request,user,instance,'review',crew_id,kind,object_id=object_id,body=body,idempotency_key=str(idempotency_key))


@router.post('/{kind:creative_kind}/{object_id}/{action}',response_model=Envelope[CreativeView],dependencies=[Depends(csrf_browser)])
async def transition(crew_id: UUID,kind: Kind,object_id: UUID,action: Literal['submit','reopen','confirm'],request: Request,body: VersionInput,idempotency_key: IdempotencyKey,user=Depends(completed_user),instance=Depends(service)):
    return await run(request,user,instance,action,crew_id,kind,object_id=object_id,body=body,idempotency_key=str(idempotency_key))
