from collections import defaultdict
from datetime import datetime
from hashlib import sha256
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, select

from silencio_set.accounts.errors import AuthError
from silencio_set.accounts.models import User
from silencio_set.availability.models import AvailabilitySet, MemberAvailability
from silencio_set.creative.models import Breakdown, SceneRevision, SceneResourceNeed
from silencio_set.crews.models import AuditLog, CrewMember
from silencio_set.crews.repository import not_found
from silencio_set.plans.domain import check_plan
from silencio_set.plans.models import ShootingPlan, PlanVersion, ShootingSession, SessionMember, SessionResource, PlanReceipt
from silencio_set.plans.schemas import PlanView, PlanPage, VersionSummary, VersionPage, VersionView, Choice, ChoicePage, SessionInput, NextSession
from silencio_set.resources.domain import check_version
from silencio_set.resources.models import Resource, Reservation
from silencio_set.resources.repository import resource_actor, require
from silencio_set.scripts.models import Script, ScriptVersion
from silencio_set.tasks.repository import request_digest


def require_read(actor):
    if not {'plan.read_all', 'plan.edit', 'plan.read_related'} & actor.permissions:
        raise AuthError(403, 'FORBIDDEN', '当前没有查看完整拍摄计划的权限')


def participant_versions(crew_id, actor):
    return select(ShootingSession.plan_version_id).join(SessionMember, SessionMember.session_id == ShootingSession.id).where(
        ShootingSession.crew_id == crew_id, SessionMember.member_id == actor.member_id, SessionMember.membership_joined_at == actor.joined_at)


def participant_item(item, actor):
    return any(member['member_id'] == str(actor.member_id) and datetime.fromisoformat(member['membership_joined_at'].replace('Z', '+00:00')) == actor.joined_at for member in item['members'])


async def scoped(session, model, crew_id, object_id):
    value = await session.scalar(select(model).where(model.crew_id == crew_id, model.id == object_id))
    if value is None:
        raise not_found()
    return value


async def rows_by_ids(session, model, crew_id, ids):
    if not ids:
        return {}
    rows = (await session.scalars(select(model).where(model.crew_id == crew_id, model.id.in_(ids)))).all()
    result = {row.id: row for row in rows}
    if result.keys() != ids:
        raise not_found()
    return result


async def session_items(session, version):
    rows = (await session.scalars(select(ShootingSession).where(
        ShootingSession.crew_id == version.crew_id, ShootingSession.plan_version_id == version.id,
    ).order_by(ShootingSession.sequence_no))).all()
    if not rows:
        return []
    members = (await session.scalars(select(SessionMember).where(SessionMember.crew_id == version.crew_id, SessionMember.session_id.in_([row.id for row in rows])).order_by(SessionMember.session_id, SessionMember.member_id))).all()
    resources = (await session.scalars(select(SessionResource).where(SessionResource.crew_id == version.crew_id, SessionResource.session_id.in_([row.id for row in rows])).order_by(SessionResource.session_id, SessionResource.resource_id))).all()
    people = await rows_by_ids(session, CrewMember, version.crew_id, {row.member_id for row in members})
    resource_ids = {row.resource_id for row in resources} | {row.location_resource_id for row in rows if row.location_resource_id}
    assets = await rows_by_ids(session, Resource, version.crew_id, resource_ids)
    scenes = await rows_by_ids(session, SceneRevision, version.crew_id, {row.scene_revision_id for row in rows})
    return [{
        'id': row.id, 'sequence_no': row.sequence_no, 'scene_revision_id': row.scene_revision_id,
        'scene_label': f'{scenes[row.scene_revision_id].scene_number} · {scenes[row.scene_revision_id].content["location_text"]}',
        'starts_at': row.starts_at, 'ends_at': row.ends_at, 'transition_minutes': row.transition_minutes, 'note': row.note,
        'location_resource_id': row.location_resource_id, 'location_reservation_id': row.location_reservation_id,
        'location_name': assets[row.location_resource_id].name if row.location_resource_id else None,
        'members': [{'member_id': link.member_id, 'membership_joined_at': link.membership_joined_at, 'display_name': people[link.member_id].display_name} for link in members if link.session_id == row.id],
        'resources': [{'resource_id': link.resource_id, 'quantity': link.quantity, 'reservation_id': link.reservation_id, 'name': assets[link.resource_id].name, 'resource_type': assets[link.resource_id].resource_type, 'unit': assets[link.resource_id].unit} for link in resources if link.session_id == row.id],
    } for row in rows]


async def version_view(session, row):
    items = row.publication_snapshot['items'] if row.status == 'published' else await session_items(session, row)
    return VersionView(**VersionSummary.model_validate(row).model_dump(), sessions=items, review_reasons=row.review_reasons)


async def save_sessions(session, crew_id, version, entries):
    scenes = await rows_by_ids(session, SceneRevision, crew_id, {entry.scene_revision_id for entry in entries})
    breakdowns = await rows_by_ids(session, Breakdown, crew_id, {row.breakdown_id for row in scenes.values()})
    if any(row.review_status != 'confirmed' for row in breakdowns.values()):
        raise AuthError(409, 'SOURCE_NOT_CONFIRMED', '拍摄场次须引用已确认场戏')
    people = await rows_by_ids(session, CrewMember, crew_id, {link.member_id for entry in entries for link in entry.members})
    ids = {link.resource_id for entry in entries for link in entry.resources} | {entry.location_resource_id for entry in entries if entry.location_resource_id}
    assets = await rows_by_ids(session, Resource, crew_id, ids)
    booking_ids = {link.reservation_id for entry in entries for link in entry.resources if link.reservation_id} | {entry.location_reservation_id for entry in entries if entry.location_reservation_id}
    bookings = await rows_by_ids(session, Reservation, crew_id, booking_ids)
    for entry in entries:
        for link in entry.members:
            person = people[link.member_id]
            if person.status != 'active' or person.joined_at != link.membership_joined_at:
                raise AuthError(409, 'MEMBER_INACTIVE', '参与成员已离组或入组身份变化，请重新选择')
        if entry.location_resource_id and assets[entry.location_resource_id].resource_type != 'location':
            raise AuthError(422, 'VALIDATION_ERROR', '拍摄场地必须选择场地资源')
        links = [(link.resource_id, link.reservation_id) for link in entry.resources]
        if entry.location_resource_id:
            links.append((entry.location_resource_id, entry.location_reservation_id))
        for resource_id, booking_id in links:
            if booking_id and bookings[booking_id].resource_id != resource_id:
                raise AuthError(409, 'RESERVATION_RESOURCE_MISMATCH', '预约不属于所选资源')
    await replace_sessions(session, crew_id, version, entries)


async def replace_sessions(session, crew_id, version, entries):
    await session.execute(delete(ShootingSession).where(ShootingSession.crew_id == crew_id, ShootingSession.plan_version_id == version.id))
    for sequence, entry in enumerate(entries, 1):
        item = ShootingSession(id=uuid4(), crew_id=crew_id, plan_version_id=version.id, sequence_no=sequence,
            **entry.model_dump(exclude={'members', 'resources'}))
        session.add(item)
        await session.flush()
        session.add_all([SessionMember(crew_id=crew_id, session_id=item.id, **link.model_dump()) for link in entry.members])
        session.add_all([SessionResource(crew_id=crew_id, session_id=item.id, **link.model_dump()) for link in entry.resources])


class PlanRepository:
    def __init__(self, sessions, *, preflight_ttl_seconds=600):
        self.sessions = sessions
        self.preflight_ttl_seconds = preflight_ttl_seconds

    async def get_plan(self, user_id, crew_id, *, plan_id):
        async with self.sessions() as session, session.begin():
            actor = await resource_actor(session, user_id, crew_id, writing=True)
            require_read(actor)
            plan = await scoped(session, ShootingPlan, crew_id, plan_id)
            if not {'plan.read_all', 'plan.edit'} & actor.permissions:
                related = participant_versions(crew_id, actor).where(ShootingSession.plan_version_id == plan.current_published_version_id)
                if not await session.scalar(select(related.exists())): raise not_found()
            return PlanView.model_validate(plan)

    async def read(self, user_id, crew_id, *, plan_id=None, version_id=None, page=1, page_size=20):
        async with self.sessions() as session, session.begin():
            actor = await resource_actor(session, user_id, crew_id, writing=True)
            require_read(actor)
            full = bool({'plan.read_all', 'plan.edit'} & actor.permissions)
            if version_id:
                version = await scoped(session, PlanVersion, crew_id, version_id)
                if not full:
                    plan = await scoped(session, ShootingPlan, crew_id, version.plan_id)
                    if version.status != 'published' or plan.current_published_version_id != version.id:
                        raise not_found()
                    items = [item for item in version.publication_snapshot['items'] if participant_item(item, actor)]
                    if not items: raise not_found()
                    return VersionView(**VersionSummary.model_validate(version).model_dump(), sessions=items, review_reasons=version.review_reasons)
                return await version_view(session, version)
            if plan_id:
                plan = await scoped(session, ShootingPlan, crew_id, plan_id)
                if not full and not await session.scalar(select(participant_versions(crew_id, actor).where(ShootingSession.plan_version_id == plan.current_published_version_id).exists())):
                    raise not_found()
                query = select(PlanVersion).where(PlanVersion.crew_id == crew_id, PlanVersion.plan_id == plan_id)
                if not full:
                    query = query.join(ShootingPlan, ShootingPlan.current_published_version_id == PlanVersion.id).where(PlanVersion.id.in_(participant_versions(crew_id, actor)))
                schema, item_schema, order = VersionPage, VersionSummary, PlanVersion.version_no.desc()
            else:
                query = select(ShootingPlan).where(ShootingPlan.crew_id == crew_id)
                if not full:
                    query = query.where(ShootingPlan.current_published_version_id.in_(participant_versions(crew_id, actor)))
                schema, item_schema, order = PlanPage, PlanView, ShootingPlan.created_at.desc()
            total = await session.scalar(select(func.count()).select_from(query.subquery()))
            rows = (await session.scalars(query.order_by(order, query.column_descriptions[0]['entity'].id).offset((page - 1) * page_size).limit(page_size))).all()
            return schema(items=[item_schema.model_validate(row) for row in rows], total=total, page=page, page_size=page_size)

    async def next(self, user_id, crew_id):
        async with self.sessions() as session, session.begin():
            actor = await resource_actor(session, user_id, crew_id, writing=True)
            require_read(actor)
            query = select(ShootingSession, ShootingPlan, PlanVersion).join(ShootingPlan,
                ShootingPlan.current_published_version_id == ShootingSession.plan_version_id).join(PlanVersion, PlanVersion.id == ShootingSession.plan_version_id).where(
                ShootingSession.crew_id == crew_id, ShootingSession.ends_at > func.clock_timestamp())
            if not {'plan.read_all', 'plan.edit'} & actor.permissions:
                query = query.join(SessionMember, SessionMember.session_id == ShootingSession.id).where(SessionMember.member_id == actor.member_id, SessionMember.membership_joined_at == actor.joined_at)
            result = (await session.execute(query.order_by(ShootingSession.starts_at, ShootingSession.id).limit(1))).first()
            if result is None: return None
            row, plan, version = result
            item = next(item for item in version.publication_snapshot['items'] if item['id'] == str(row.id))
            return NextSession(session_id=row.id, plan_name=plan.name, scene_label=item['scene_label'], location_name=item['location_name'],
                starts_at=item['starts_at'], ends_at=item['ends_at'], review_reasons=version.review_reasons)

    async def mutate(self, user_id, crew_id, action, body, request_id, *, idempotency_key, object_id=None):
        async with self.sessions() as session, session.begin():
            actor = await resource_actor(session, user_id, crew_id, writing=True)
            require(actor, 'plan.edit')
            key = sha256(idempotency_key.encode()).hexdigest()
            digest = request_digest(f'plan.{action}', object_id, body)
            receipt = await session.scalar(select(PlanReceipt).where(PlanReceipt.crew_id == crew_id, PlanReceipt.user_id == user_id, PlanReceipt.key_digest == key))
            result_type = PlanView if action == 'create' else VersionView
            if receipt:
                if receipt.membership_joined_at != actor.joined_at:
                    raise AuthError(409, 'MEMBERSHIP_CHANGED', '入组身份已变化')
                if receipt.request_digest != digest:
                    raise AuthError(409, 'IDEMPOTENCY_KEY_REUSED', '同键不能用于不同内容')
                return result_type.model_validate(receipt.response_snapshot)
            now = await session.scalar(select(func.clock_timestamp()))
            if action == 'create':
                row = ShootingPlan(id=uuid4(), crew_id=crew_id, name=body.name, created_by=actor.member_id, created_at=now)
                session.add(row)
                result = PlanView.model_validate(row)
            elif action == 'version':
                await scoped(session, ShootingPlan, crew_id, object_id)
                last = await session.scalar(select(func.coalesce(func.max(PlanVersion.version_no), 0)).where(PlanVersion.plan_id == object_id))
                row = PlanVersion(id=uuid4(), crew_id=crew_id, plan_id=object_id, source_version_id=body.copy_from, version_no=last + 1, status='draft', lock_version=1, review_reasons=[], created_by=actor.member_id, created_at=now, updated_at=now)
                session.add(row)
                await session.flush()
                if body.copy_from:
                    original = await scoped(session, PlanVersion, crew_id, body.copy_from)
                    if original.plan_id != object_id:
                        raise not_found()
                    contents = await session_items(session, original)
                    entries = [SessionInput(**{key: value for key, value in item.items() if key not in {'id', 'sequence_no', 'scene_label', 'location_name', 'members', 'resources'}},
                        members=[{key: value for key, value in link.items() if key != 'display_name'} for link in item['members']],
                        resources=[{key: value for key, value in link.items() if key not in {'name', 'unit', 'resource_type'}} for link in item['resources']]) for item in contents]
                    await replace_sessions(session, crew_id, row, entries)
                await session.flush()
                result = await version_view(session, row)
            else:
                row = await scoped(session, PlanVersion, crew_id, object_id)
                check_version(row, body.expected_lock_version)
                if row.status != 'draft':
                    raise AuthError(409, 'PLAN_IMMUTABLE', '已发布排期不能覆盖，请复制为新草稿')
                require(actor, 'resource.read'); require(actor, 'member.read')
                await save_sessions(session, crew_id, row, body.sessions)
                row.lock_version += 1
                row.updated_at = now
                await session.flush()
                result = await version_view(session, row)
            session.add(PlanReceipt(crew_id=crew_id, user_id=user_id, member_id=actor.member_id, membership_joined_at=actor.joined_at, key_digest=key, request_digest=digest, response_snapshot=result.model_dump(mode='json')))
            session.add(AuditLog(crew_id=crew_id, actor_type='user', actor_id=user_id, action=f'plan.{action}', target_type='shooting_plan', target_id=row.id, result='success', request_id=request_id, after_summary={'id': str(row.id)}))
            await session.flush()
        return result

    async def preflight(self, user_id, crew_id, version_id, body):
        async with self.sessions() as session, session.begin():
            actor = await resource_actor(session, user_id, crew_id, writing=True)
            for permission in ('plan.edit', 'availability.read_all', 'resource.read'):
                require(actor, permission)
            version = await scoped(session, PlanVersion, crew_id, version_id)
            check_version(version, body.expected_lock_version)
            from silencio_set.plans.publication import create_preflight
            return await create_preflight(session, actor, version, self.preflight_ttl_seconds)

    async def choices(self, user_id, crew_id, kind, *, resource_id=None, page=1, page_size=20):
        async with self.sessions() as session, session.begin():
            actor = await resource_actor(session, user_id, crew_id, writing=True)
            require(actor, 'plan.edit')
            if kind == 'scenes':
                query = select(SceneRevision, Breakdown.version_no, ScriptVersion.version_no, Script.title).join(Breakdown, Breakdown.id == SceneRevision.breakdown_id).join(ScriptVersion, ScriptVersion.id == SceneRevision.script_version_id).join(Script, Script.id == ScriptVersion.script_id).where(SceneRevision.crew_id == crew_id, Breakdown.review_status == 'confirmed')
                order = (Script.title, ScriptVersion.version_no.desc(), Breakdown.version_no.desc(), SceneRevision.scene_number, SceneRevision.id)
            elif kind == 'members':
                require(actor, 'member.read')
                query = select(CrewMember).join(User, User.id == CrewMember.user_id).where(CrewMember.crew_id == crew_id, CrewMember.status == 'active', User.status == 'active')
                order = (CrewMember.display_name, CrewMember.id)
            elif kind == 'resources':
                require(actor, 'resource.read')
                query = select(Resource).where(Resource.crew_id == crew_id, Resource.status == 'available')
                order = (Resource.name, Resource.id)
            else:
                require(actor, 'resource.read')
                if resource_id is None:
                    raise AuthError(422, 'VALIDATION_ERROR', '选择预约前须指定资源')
                query = select(Reservation).where(Reservation.crew_id == crew_id, Reservation.resource_id == resource_id, Reservation.status == 'confirmed')
                order = (Reservation.starts_at, Reservation.id)
            total = await session.scalar(select(func.count()).select_from(query.subquery()))
            result = (await session.execute(query.order_by(*order).offset((page - 1) * page_size).limit(page_size))).all()
            choices = []
            for row in result:
                if kind == 'scenes':
                    scene, breakdown_no, script_no, title = row
                    choice = Choice(id=scene.id, label=f'{title} · 剧本{script_no}／拆解{breakdown_no} · {scene.scene_number} · {scene.content["location_text"]}')
                else:
                    value = row[0]
                    if kind == 'members': choice = Choice(id=value.id, label=value.display_name, membership_joined_at=value.joined_at)
                    elif kind == 'resources': choice = Choice(id=value.id, label=value.name, resource_type=value.resource_type, unit=value.unit)
                    else: choice = Choice(id=value.id, label=f'{value.starts_at.astimezone(ZoneInfo("Asia/Shanghai")):%Y-%m-%d %H:%M} — {value.ends_at.astimezone(ZoneInfo("Asia/Shanghai")):%Y-%m-%d %H:%M}', quantity=value.quantity)
                choices.append(choice)
            return ChoicePage(items=choices, total=total, page=page, page_size=page_size)
