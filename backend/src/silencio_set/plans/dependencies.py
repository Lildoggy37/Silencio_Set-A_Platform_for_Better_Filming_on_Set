"""Checks used by existing business writes under the same crew coordinator."""
from sqlalchemy import select, func, text, or_

from silencio_set.accounts.errors import AuthError
from silencio_set.creative.models import SceneRevision, SceneCharacter
from silencio_set.plans.models import ShootingPlan, PlanVersion, ShootingSession, SessionMember, SessionResource
from silencio_set.scripts.models import ScriptVersion


def current_sessions(crew_id):
    return select(ShootingSession).join(ShootingPlan, ShootingPlan.current_published_version_id == ShootingSession.plan_version_id).where(
        ShootingPlan.crew_id == crew_id,
        ShootingSession.ends_at + ShootingSession.transition_minutes * text("INTERVAL '1 minute'") > func.clock_timestamp())


async def check_plan_responsibilities(session, crew_id, member_id):
    query = current_sessions(crew_id).join(SessionMember, SessionMember.session_id == ShootingSession.id).where(SessionMember.member_id == member_id)
    if await session.scalar(select(query.exists())):
        raise AuthError(409, 'PLAN_MEMBER_IN_USE', '仍有未结束的正式拍摄安排，请先由制片发布人员调整后再离组')


async def check_booking_dependency(session, crew_id, reservation_id):
    allocated = select(SessionResource.session_id).where(SessionResource.session_id == ShootingSession.id, SessionResource.reservation_id == reservation_id).exists()
    query = current_sessions(crew_id).where(or_(ShootingSession.location_reservation_id == reservation_id, allocated))
    if await session.scalar(select(query.exists())):
        raise AuthError(409, 'RESERVATION_IN_PLAN', '该预约仍用于正式拍摄安排，请先发布替代安排再取消')


async def check_resource_unit_dependency(session, crew_id, resource_id):
    allocated = select(SessionResource.session_id).where(SessionResource.session_id == ShootingSession.id, SessionResource.resource_id == resource_id).exists()
    query = current_sessions(crew_id).where(or_(ShootingSession.location_resource_id == resource_id, allocated))
    if await session.scalar(select(query.exists())):
        raise AuthError(409, 'RESOURCE_IN_PLAN', '该资源仍用于正式排期，不能直接更改单位')


async def mark_plan_review(session, crew_id, reason, *, now, member_id=None, resource_id=None, script_id=None, breakdown_id=None, character_name=None, range_start=None, range_end=None):
    query = current_sessions(crew_id)
    if member_id:
        query = query.join(SessionMember, SessionMember.session_id == ShootingSession.id).where(SessionMember.member_id == member_id)
    if resource_id:
        allocated = select(SessionResource.session_id).where(SessionResource.session_id == ShootingSession.id, SessionResource.resource_id == resource_id).exists()
        query = query.where(or_(ShootingSession.location_resource_id == resource_id, allocated))
    if script_id:
        query = query.join(SceneRevision, SceneRevision.id == ShootingSession.scene_revision_id).join(ScriptVersion, ScriptVersion.id == SceneRevision.script_version_id).where(ScriptVersion.script_id == script_id)
    if character_name is not None:
        query = query.where(select(SceneCharacter.id).where(SceneCharacter.crew_id == crew_id,
            SceneCharacter.scene_revision_id == ShootingSession.scene_revision_id, SceneCharacter.name == character_name).exists())
    if breakdown_id is not None:
        query = query.where(select(SceneRevision.id).where(SceneRevision.id == ShootingSession.scene_revision_id,
            SceneRevision.crew_id == crew_id, SceneRevision.breakdown_id == breakdown_id).exists())
    if range_start is not None:
        query = query.where(ShootingSession.starts_at < range_end, ShootingSession.ends_at + ShootingSession.transition_minutes * text("INTERVAL '1 minute'") > range_start)
    ids = query.with_only_columns(ShootingSession.plan_version_id).distinct()
    rows = (await session.scalars(select(PlanVersion).where(PlanVersion.crew_id == crew_id, PlanVersion.id.in_(ids)))).all()
    for row in rows:
        if reason not in row.review_reasons:
            row.review_reasons = [*row.review_reasons, reason]
            from silencio_set.call_sheets.dependencies import mark_source_review
            await mark_source_review(session, crew_id, row.id, now)
