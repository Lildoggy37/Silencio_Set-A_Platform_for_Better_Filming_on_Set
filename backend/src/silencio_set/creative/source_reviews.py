from hashlib import sha256
from uuid import uuid4
from sqlalchemy import func, select

from silencio_set.accounts.errors import AuthError
from silencio_set.creative.models import CreativeReceipt, SourceReviewRecord, SceneRevision
from silencio_set.creative.repository import models
from silencio_set.creative.source_evidence import current_reviews, assessment_status
from silencio_set.creative.source_schemas import SourceAssessment, SourceReviewView, SourceImpactPage, SourceReviewPage
from silencio_set.crews.models import AuditLog
from silencio_set.plans.dependencies import mark_plan_review
from silencio_set.plans.repository import scoped
from silencio_set.resources.domain import check_version
from silencio_set.resources.repository import resource_actor, require
from silencio_set.scripts.models import Script, ScriptVersion
from silencio_set.tasks.repository import request_digest


def authorize(actor, kind, writing=False):
    permission = models(kind)[1]
    require(actor, 'script.read'); require(actor, f'{permission}.read')
    if writing: require(actor, f'{permission}.review')


async def context(session, crew_id, kind, object_id):
    target = await scoped(session, models(kind)[0], crew_id, object_id)
    source_id = target.script_version_id if kind == 'breakdowns' else (await scoped(session, SceneRevision, crew_id, target.scene_revision_id)).script_version_id
    fixed = await scoped(session, ScriptVersion, crew_id, source_id)
    script = await scoped(session, Script, crew_id, fixed.script_id)
    return target, fixed, script


def assessment(kind, target, fixed, script, working, review):
    return SourceAssessment(kind=kind, id=target.id, source_id=getattr(target, models(kind)[2]), version_no=target.version_no,
        review_status=target.review_status, lock_version=target.lock_version, script_id=script.id, script_title=script.title,
        source_script_version_id=fixed.id, source_script_version_no=fixed.version_no,
        working_script_version_id=script.current_version_id, working_script_version_no=working.version_no if working else None,
        working_revision=script.working_revision, status=assessment_status(fixed.id, script, review),
        latest_review=SourceReviewView.model_validate(review) if review else None)


async def read(repository, user_id, crew_id, kind, *, object_id=None, script_id=None, history=False, page=1, page_size=20):
    async with repository.sessions() as session, session.begin():
        actor = await resource_actor(session, user_id, crew_id, writing=True)
        authorize(actor, kind)
        if object_id:
            target, fixed, script = await context(session, crew_id, kind, object_id)
            if history:
                field = SourceReviewRecord.breakdown_id if kind == 'breakdowns' else SourceReviewRecord.storyboard_version_id
                query = select(SourceReviewRecord).where(SourceReviewRecord.crew_id == crew_id, field == target.id)
                total = await session.scalar(select(func.count()).select_from(query.subquery()))
                rows = (await session.scalars(query.order_by(SourceReviewRecord.reviewed_at.desc(), SourceReviewRecord.id.desc()).offset((page-1)*page_size).limit(page_size))).all()
                return SourceReviewPage(items=[SourceReviewView.model_validate(row) for row in rows], total=total, page=page, page_size=page_size)
            working = await scoped(session, ScriptVersion, crew_id, script.current_version_id) if script.current_version_id else None
            reviews = await current_reviews(session, crew_id, kind, {target.id:(target, fixed.id, script)})
            return assessment(kind, target, fixed, script, working, reviews.get(target.id))
        script = await scoped(session, Script, crew_id, script_id)
        working = await scoped(session, ScriptVersion, crew_id, script.current_version_id) if script.current_version_id else None
        model = models(kind)[0]
        query = select(model, ScriptVersion)
        if kind == 'storyboards':
            query = query.join(SceneRevision, SceneRevision.id == model.scene_revision_id).join(ScriptVersion, ScriptVersion.id == SceneRevision.script_version_id)
        else: query = query.join(ScriptVersion, ScriptVersion.id == model.script_version_id)
        query = query.where(model.crew_id == crew_id, ScriptVersion.script_id == script.id,
            ScriptVersion.id.is_distinct_from(script.current_version_id))
        total = await session.scalar(select(func.count()).select_from(query.subquery()))
        rows = (await session.execute(query.order_by(model.created_at.desc(), model.id).offset((page-1)*page_size).limit(page_size))).all()
        reviews = await current_reviews(session, crew_id, kind, {target.id:(target, fixed.id, script) for target, fixed in rows})
        return SourceImpactPage(items=[assessment(kind, target, fixed, script, working, reviews.get(target.id)) for target, fixed in rows], total=total, page=page, page_size=page_size)


async def review(repository, user_id, crew_id, kind, body, request_id, *, object_id, idempotency_key):
    async with repository.sessions() as session, session.begin():
        actor = await resource_actor(session, user_id, crew_id, writing=True)
        authorize(actor, kind, writing=True)
        key = sha256(idempotency_key.encode()).hexdigest()
        digest = request_digest(f'{kind}.source_review', object_id, body)
        receipt = await session.scalar(select(CreativeReceipt).where(CreativeReceipt.crew_id == crew_id,
            CreativeReceipt.user_id == user_id, CreativeReceipt.key_digest == key))
        if receipt:
            if receipt.membership_joined_at != actor.joined_at: raise AuthError(409, 'MEMBERSHIP_CHANGED', '入组身份已变化')
            if receipt.request_digest != digest: raise AuthError(409, 'IDEMPOTENCY_KEY_REUSED', '同键不能用于不同内容')
            return SourceAssessment.model_validate(receipt.response_snapshot)
        target, fixed, script = await context(session, crew_id, kind, object_id)
        check_version(target, body.expected_lock_version)
        if target.review_status != 'confirmed':
            raise AuthError(409, 'SOURCE_TARGET_NOT_CONFIRMED', '请先完成资料内容审核，再复核对当前工作稿的适用性')
        if script.current_version_id != body.expected_working_version_id or script.working_revision != body.expected_working_revision:
            raise AuthError(409, 'SOURCE_BASIS_CHANGED', '剧本工作版本已变化，请重新核对')
        if fixed.id == script.current_version_id:
            raise AuthError(409, 'SOURCE_REVIEW_NOT_REQUIRED', '资料已基于当前工作版本，无需历史来源复核')
        reviews = await current_reviews(session, crew_id, kind, {target.id:(target, fixed.id, script)})
        previous = reviews.get(target.id)
        if body.expected_review_id != (previous.id if previous else None):
            raise AuthError(409, 'SOURCE_REVIEW_CHANGED', '来源复核结论已变化，请读取最新记录')
        now = await session.scalar(select(func.clock_timestamp()))
        record = SourceReviewRecord(id=uuid4(), crew_id=crew_id, breakdown_id=target.id if kind=='breakdowns' else None,
            storyboard_version_id=target.id if kind=='storyboards' else None, script_id=script.id,
            source_script_version_id=fixed.id, working_script_version_id=script.current_version_id, working_revision=script.working_revision,
            target_lock_version=target.lock_version, reviewed_by=actor.member_id, reviewer_joined_at=actor.joined_at,
            conclusion=body.conclusion, review_note=body.review_note, reviewed_at=now)
        session.add(record)
        if kind == 'breakdowns':
            await mark_plan_review(session, crew_id, 'source_review_changed', now=now, breakdown_id=target.id)
        working = await scoped(session, ScriptVersion, crew_id, script.current_version_id)
        result = assessment(kind, target, fixed, script, working, record)
        session.add(CreativeReceipt(crew_id=crew_id, user_id=user_id, member_id=actor.member_id, membership_joined_at=actor.joined_at,
            key_digest=key, request_digest=digest, response_snapshot=result.model_dump(mode='json')))
        session.add(AuditLog(crew_id=crew_id, actor_type='user', actor_id=user_id, action=f'{models(kind)[1]}.source_review',
            target_type=models(kind)[1], target_id=target.id, result='success', request_id=request_id,
            after_summary={'review_id':str(record.id), 'working_version_id':str(script.current_version_id),
                'working_revision':script.working_revision, 'conclusion':body.conclusion}))
        await session.flush()
    return result
