from hashlib import sha256
from uuid import uuid4
from sqlalchemy import delete, func, select
from silencio_set.accounts.errors import AuthError
from silencio_set.crews.models import AuditLog
from silencio_set.crews.repository import not_found
from silencio_set.resources.repository import resource_actor, require
from silencio_set.resources.domain import check_version
from silencio_set.scripts.models import ScriptVersion, ScriptParagraph
from silencio_set.creative.models import Breakdown, Scene, SceneRevision, StoryboardVersion, CreativeReview, CreativeReceipt
from silencio_set.creative.schemas import CreativeView, CreativeSummary, CreativePage, ReviewView, ReviewPage
from silencio_set.tasks.repository import request_digest
from silencio_set.creative.preparation import scene_links, validate_links, validate_breakdown_links, add_links


def models(kind): return (Breakdown,'scene','script_version_id') if kind=='breakdowns' else (StoryboardVersion,'storyboard','scene_revision_id')


async def view(session,kind,row,summary=False):
    items=[]
    if not summary:
        if kind=='breakdowns':
            rows=(await session.scalars(select(SceneRevision).where(SceneRevision.crew_id==row.crew_id,SceneRevision.breakdown_id==row.id).order_by(SceneRevision.scene_number,SceneRevision.id))).all()
            links = await scene_links(session, row.crew_id, rows)
            items=[dict(item.content,**links[item.id],id=str(item.id),scene_id=str(item.scene_id),scene_number=item.scene_number,source_start=item.source_start,source_end=item.source_end) for item in rows]
        else: items=row.shots
    schema=CreativeSummary if summary else CreativeView
    return schema(id=row.id,kind=kind,created_by=row.created_by,source_id=getattr(row,models(kind)[2]),version_no=row.version_no,
        review_status=row.review_status,lock_version=row.lock_version,reviewed_by=row.reviewed_by,reviewed_at=row.reviewed_at,**({} if summary else {'items':items}))


async def validate_source(session,crew_id,kind,source_id):
    if kind=='breakdowns':
        source=await session.scalar(select(ScriptVersion).where(ScriptVersion.crew_id==crew_id,ScriptVersion.id==source_id))
        if source is None: raise not_found()
        if source.status!='confirmed': raise AuthError(409,'SOURCE_NOT_CONFIRMED','请先确认来源剧本版本')
    else:
        source=await session.scalar(select(SceneRevision).where(SceneRevision.crew_id==crew_id,SceneRevision.id==source_id))
        if source is None: raise not_found()
        parent=await session.scalar(select(Breakdown).where(Breakdown.crew_id==crew_id,Breakdown.id==source.breakdown_id))
        if parent.review_status!='confirmed': raise AuthError(409,'SOURCE_NOT_CONFIRMED','请先确认来源场戏拆解')
    return source


class CreativeRepository:
    def __init__(self,sessions): self.sessions=sessions

    async def read(self,user_id,crew_id,kind,*,object_id=None,source_id=None,page=1,page_size=20,history=False):
        model,permission,source_field=models(kind)
        async with self.sessions() as session,session.begin():
            actor=await resource_actor(session,user_id,crew_id,writing=True)
            require(actor,'script.read'); require(actor,f'{permission}.read')
            query=select(model).where(model.crew_id==crew_id)
            if object_id:
                row=await session.scalar(query.where(model.id==object_id))
                if row is None: raise not_found()
                if not history: return await view(session,kind,row)
                target=CreativeReview.breakdown_id if kind=='breakdowns' else CreativeReview.storyboard_version_id
                query=select(CreativeReview).where(CreativeReview.crew_id==crew_id,target==object_id)
                total=await session.scalar(select(func.count()).select_from(query.subquery()))
                rows=(await session.scalars(query.order_by(CreativeReview.reviewed_at.desc(),CreativeReview.id).offset((page-1)*page_size).limit(page_size))).all()
                return ReviewPage(items=[ReviewView.model_validate(item) for item in rows],total=total,page=page,page_size=page_size)
            query=query.where(getattr(model,source_field)==source_id)
            total=await session.scalar(select(func.count()).select_from(query.subquery()))
            rows=(await session.scalars(query.order_by(model.version_no.desc()).offset((page-1)*page_size).limit(page_size))).all()
            return CreativePage(items=[await view(session,kind,row,True) for row in rows],total=total,page=page,page_size=page_size)

    async def mutate(self,user_id,crew_id,kind,action,body,request_id,*,idempotency_key,object_id=None):
        model,permission,source_field=models(kind)
        async with self.sessions() as session,session.begin():
            actor=await resource_actor(session,user_id,crew_id,writing=True)
            require(actor,'script.read'); require(actor,f'{permission}.read'); require(actor,f'{permission}.review' if action in {'review','confirm'} else f'{permission}.edit')
            key=sha256(idempotency_key.encode()).hexdigest(); digest=request_digest(f'{kind}.{action}',object_id,body)
            receipt=await session.scalar(select(CreativeReceipt).where(CreativeReceipt.crew_id==crew_id,CreativeReceipt.user_id==user_id,CreativeReceipt.key_digest==key))
            if receipt:
                if receipt.membership_joined_at!=actor.joined_at: raise AuthError(409,'MEMBERSHIP_CHANGED','入组身份已变化')
                if receipt.request_digest!=digest: raise AuthError(409,'IDEMPOTENCY_KEY_REUSED','同键不能用于不同内容')
                return CreativeView.model_validate(receipt.response_snapshot)
            now=await session.scalar(select(func.clock_timestamp()))
            row=await session.scalar(select(model).where(model.crew_id==crew_id,model.id==object_id).with_for_update()) if object_id else None
            if action=='create':
                await validate_source(session,crew_id,kind,body.source_id)
                last=await session.scalar(select(func.coalesce(func.max(model.version_no),0)).where(model.crew_id==crew_id,getattr(model,source_field)==body.source_id))
                row=model(id=uuid4(),crew_id=crew_id,**{source_field:body.source_id},version_no=last+1,review_status='draft',lock_version=1,
                    created_by=actor.member_id,created_at=now,updated_at=now,reviewed_by=None,reviewed_at=None)
                if kind=='storyboards': row.shots=[]
                session.add(row); await session.flush()
                if body.copy_from:
                    original=await session.scalar(select(model).where(model.crew_id==crew_id,model.id==body.copy_from,getattr(model,source_field)==body.source_id))
                    if original is None: raise not_found()
                    if kind=='storyboards': row.shots=[dict(item) for item in original.shots]
                    else:
                        old=(await session.scalars(select(SceneRevision).where(SceneRevision.crew_id==crew_id,SceneRevision.breakdown_id==original.id))).all()
                        links = await scene_links(session, crew_id, old)
                        for item in old:
                            revision_id = uuid4()
                            session.add(SceneRevision(id=revision_id,crew_id=crew_id,scene_id=item.scene_id,breakdown_id=row.id,script_version_id=row.script_version_id,
                                scene_number=item.scene_number,source_start=item.source_start,source_end=item.source_end,content=dict(item.content)))
                            await session.flush()
                            add_links(session, crew_id, revision_id, links[item.id])
            else:
                if row is None: raise not_found()
                check_version(row,body.expected_lock_version)
                if action=='save':
                    if row.review_status!='draft': raise AuthError(409,'CREATIVE_IMMUTABLE','仅草稿可修改；已确认内容请新建版本')
                    if kind=='storyboards': row.shots=[dict(item.model_dump(mode='json'),sort_order=i) for i,item in enumerate(body.shots,1)]
                    else:
                        source=await validate_source(session,crew_id,kind,row.script_version_id)
                        paragraphs=(await session.scalars(select(ScriptParagraph.paragraph_no).where(ScriptParagraph.crew_id==crew_id,ScriptParagraph.script_version_id==source.id))).all()
                        available=set(paragraphs)
                        await validate_links(session, crew_id, actor, [need for scene in body.scenes for need in scene.resource_needs])
                        await session.execute(delete(SceneRevision).where(SceneRevision.crew_id==crew_id,SceneRevision.breakdown_id==row.id))
                        for entry in body.scenes:
                            if not set(range(entry.source_start,entry.source_end+1))<=available: raise AuthError(409,'INVALID_SOURCE_RANGE','原文段落范围不存在')
                            scene_id=entry.scene_id
                            if scene_id:
                                scene=await session.scalar(select(Scene).where(Scene.crew_id==crew_id,Scene.id==scene_id,Scene.script_id==source.script_id))
                                if scene is None: raise not_found()
                            else:
                                scene_id=uuid4(); session.add(Scene(id=scene_id,crew_id=crew_id,script_id=source.script_id)); await session.flush()
                            revision_id = uuid4()
                            session.add(SceneRevision(id=revision_id,crew_id=crew_id,scene_id=scene_id,breakdown_id=row.id,script_version_id=source.id,
                                scene_number=entry.scene_number,source_start=entry.source_start,source_end=entry.source_end,
                                content=entry.model_dump(mode='json',exclude={'scene_id','scene_number','source_start','source_end','characters','resource_needs'})))
                            await session.flush()
                            add_links(session, crew_id, revision_id, entry.model_dump(mode='json'))
                elif action=='submit':
                    if row.review_status!='draft': raise AuthError(409,'INVALID_STATE','仅草稿可以送审')
                    contents=await view(session,kind,row)
                    if not contents.items: raise AuthError(409,'EMPTY_CREATIVE','请先填写场戏或镜头')
                    await validate_source(session,crew_id,kind,getattr(row,source_field))
                    if kind == 'breakdowns': await validate_breakdown_links(session, crew_id, actor, row.id)
                    row.review_status='pending_review'
                elif action in {'review','confirm'}:
                    direct = action == 'confirm'
                    if direct:
                        require(actor, f'{permission}.edit')
                        if row.created_by != actor.member_id:
                            raise AuthError(403, 'FORBIDDEN', '只能直接确认自己创建的稿件')
                        if row.review_status != 'draft':
                            raise AuthError(409, 'INVALID_STATE', '仅草稿可直接确认')
                        if not (await view(session,kind,row)).items:
                            raise AuthError(409, 'EMPTY_CREATIVE', '请先填写场戏或镜头')
                    decision = 'accept' if direct else body.decision
                    note = '作者直接确认' if direct else body.review_note
                    if not direct and row.review_status!='pending_review': raise AuthError(409,'INVALID_STATE','仅待审内容可审核')
                    await validate_source(session,crew_id,kind,getattr(row,source_field))
                    if kind == 'breakdowns' and decision == 'accept': await validate_breakdown_links(session, crew_id, actor, row.id)
                    snapshot=(await view(session,kind,row)).model_dump(mode='json')
                    session.add(CreativeReview(crew_id=crew_id,breakdown_id=row.id if kind=='breakdowns' else None,storyboard_version_id=row.id if kind=='storyboards' else None,
                        reviewed_by=actor.member_id,decision=decision,review_note=note,reviewed_lock_version=row.lock_version,content_snapshot=snapshot,reviewed_at=now))
                    row.review_status='confirmed' if decision=='accept' else 'rejected'; row.reviewed_by=actor.member_id; row.reviewed_at=now
                elif action=='reopen':
                    if row.review_status!='rejected': raise AuthError(409,'INVALID_STATE','仅退回内容可重新打开')
                    row.review_status='draft'; row.reviewed_by=None; row.reviewed_at=None
                else: raise ValueError('Unknown creative action')
                row.lock_version+=1; row.updated_at=now
            await session.flush()
            result=await view(session,kind,row)
            session.add(CreativeReceipt(crew_id=crew_id,user_id=user_id,member_id=actor.member_id,membership_joined_at=actor.joined_at,key_digest=key,request_digest=digest,response_snapshot=result.model_dump(mode='json')))
            session.add(AuditLog(crew_id=crew_id,actor_type='user',actor_id=user_id,action=f'{permission}.{action}',target_type=permission,target_id=row.id,result='success',request_id=request_id,
                after_summary={'status':row.review_status,'lock_version':row.lock_version}))
            await session.flush()
        return result
