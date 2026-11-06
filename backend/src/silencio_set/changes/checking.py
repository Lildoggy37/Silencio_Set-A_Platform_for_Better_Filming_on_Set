from hashlib import sha256
from uuid import uuid4
from sqlalchemy import func, or_, select

from silencio_set.accounts.errors import AuthError
from silencio_set.changes.evidence import evidence, resolution_reason
from silencio_set.changes.models import ImpactCheck, ImpactIssue
from silencio_set.changes.queries import issue_query, target_query, unfinished
from silencio_set.changes.repository import authorize_category, items_for_rows
from silencio_set.changes.schemas import ImpactCheckView
from silencio_set.changes.transitions import observe, resolve
from silencio_set.creative.source_evidence import current_reviews
from silencio_set.crews.models import AuditLog
from silencio_set.plans.models import PlanVersion
from silencio_set.call_sheets.models import CallSheet
from silencio_set.resources.repository import resource_actor
from silencio_set.tasks.repository import request_digest

MAX_CHECK_TARGETS = 1000


async def missing_evidence(session,crew_id,category,ids,now):
    if not ids: return {}
    query,model=target_query(category,crew_id)
    query=query.where(model.id.in_(ids))
    if category=='plans': query=query.add_columns(unfinished(PlanVersion.id,now).label('has_unfinished'))
    elif category=='call_sheets': query=query.add_columns(unfinished(PlanVersion.id,now,CallSheet.shooting_date).label('has_unfinished'))
    rows=(await session.execute(query)).all()
    contexts={row[0].id:(row,True) if category in {'breakdowns','storyboards'} else (tuple(row[:-1]),row[-1]) for row in rows}
    reviews = await current_reviews(session,crew_id,category,{key:(row[0],row[1].id,row[2]) for key,(row,_) in contexts.items()}) if category in {'breakdowns','storyboards'} else {}
    return {key:(evidence(category,row,reviews.get(key),has_unfinished=active),
                 resolution_reason(category,row,reviews.get(key),has_unfinished=active)) for key,(row,active) in contexts.items()}


async def check(repository,user_id,crew_id,category,body,request_id,idempotency_key):
    async with repository.sessions() as session,session.begin():
        actor=await resource_actor(session,user_id,crew_id,writing=True)
        authorize_category(actor,category)
        key=sha256(idempotency_key.encode()).hexdigest()
        digest=request_digest(f'changes.check.{category}',crew_id,body)
        previous=await session.scalar(select(ImpactCheck).where(ImpactCheck.crew_id==crew_id,ImpactCheck.user_id==user_id,ImpactCheck.key_digest==key))
        if previous:
            if previous.membership_joined_at!=actor.joined_at: raise AuthError(409,'MEMBERSHIP_CHANGED','入组身份已变化')
            if previous.request_digest!=digest: raise AuthError(409,'IDEMPOTENCY_KEY_REUSED','同键不能用于不同检查')
            return ImpactCheckView.model_validate(previous)
        now=await session.scalar(select(func.clock_timestamp()))
        query,order=issue_query(category,crew_id,now)
        count=await session.scalar(select(func.count()).select_from(query.subquery()))
        if count>MAX_CHECK_TARGETS:
            raise AuthError(409,'CHANGE_CHECK_TOO_LARGE','此类别待处理项超过单次记录上限，未更新问题记录')
        rows=(await session.execute(query.order_by(*order))).all()
        items,reviews=await items_for_rows(session,crew_id,category,rows)
        bases={row[0].id:evidence(category,row,reviews.get(row[0].id)) for row in rows}
        ids={item.id for item in items}
        existing=(await session.scalars(select(ImpactIssue).where(ImpactIssue.crew_id==crew_id,ImpactIssue.category==category,
            or_(ImpactIssue.status=='open',ImpactIssue.target_id.in_(ids))))).all()
        by_target={row.target_id:row for row in existing}
        missing={row.target_id for row in existing if row.status=='open' and row.target_id not in ids}
        closures=await missing_evidence(session,crew_id,category,missing,now)
        run=ImpactCheck(id=uuid4(),crew_id=crew_id,user_id=user_id,checked_by=actor.member_id,membership_joined_at=actor.joined_at,
            category=category,key_digest=key,request_digest=digest,checked_at=now,detected_count=0,updated_count=0,
            resolved_count=0,reopened_count=0,open_count=len(items))
        session.add(run); await session.flush()
        events=[]
        for item in items:
            original=by_target.get(item.id)
            row,event=observe(original,item,bases[item.id],run)
            if original is None: session.add(row)
            if event: events.append(event)
        for target_id in sorted(missing,key=str):
            basis,reason=closures[target_id]
            events.append(resolve(by_target[target_id],basis,reason,run))
        for event in events:
            field={'detected':'detected_count','updated':'updated_count','resolved':'resolved_count','reopened':'reopened_count'}[event.kind]
            setattr(run,field,getattr(run,field)+1)
        await session.flush()
        session.add_all(events)
        session.add(AuditLog(crew_id=crew_id,actor_type='user',actor_id=user_id,action='changes.check',target_type='impact_check',
            target_id=run.id,result='success',request_id=request_id,
            after_summary={'category':category,'open_count':run.open_count,'transition_count':len(events)}))
        await session.flush()
        result=ImpactCheckView.model_validate(run)
    return result
