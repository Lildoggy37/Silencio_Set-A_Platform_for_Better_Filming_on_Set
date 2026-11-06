"""Script-scoped casting; published creative content is never rewritten."""
from hashlib import sha256
from uuid import uuid4

from sqlalchemy import delete, func, select, union

from silencio_set.accounts.errors import AuthError
from silencio_set.accounts.models import User
from silencio_set.creative.casting_schemas import ActorChoice, ActorPage, CastingEntry, CastingPage, CastingResult, CastingScript, CastingScriptPage
from silencio_set.creative.models import CharacterCasting, CreativeReceipt, SceneCharacter, SceneRevision
from silencio_set.crews.models import AuditLog, CrewMember, MemberRole
from silencio_set.crews.repository import not_found
from silencio_set.plans.dependencies import mark_plan_review
from silencio_set.resources.domain import check_version
from silencio_set.resources.repository import resource_actor, require
from silencio_set.scripts.models import Script, ScriptVersion
from silencio_set.tasks.repository import request_digest


def authorize(actor, writing=False):
    # Production staff need casting for scheduling without script editing rights.
    if 'plan.edit' not in actor.permissions:
        require(actor, 'script.read')
        require(actor, 'scene.review' if writing else 'scene.read')
    require(actor, 'member.read')


def actor_query(crew_id):
    return select(CrewMember).join(User, User.id == CrewMember.user_id).join(
        MemberRole, (MemberRole.crew_id == CrewMember.crew_id) & (MemberRole.member_id == CrewMember.id),
    ).where(CrewMember.crew_id == crew_id, CrewMember.status == 'active', User.status == 'active', MemberRole.role_code == 'actor')


def current_names(script):
    return select(SceneCharacter.name.label('character_name')).join(
        SceneRevision, SceneRevision.id == SceneCharacter.scene_revision_id,
    ).where(SceneCharacter.crew_id == script.crew_id, SceneRevision.script_version_id == script.current_version_id).distinct()


async def script_row(session, crew_id, script_id):
    script = await session.scalar(select(Script).where(Script.crew_id == crew_id, Script.id == script_id))
    if script is None:
        raise not_found()
    return script


async def mark_member_casting_review(session, crew_id, member_id, now):
    rows = (await session.scalars(select(CharacterCasting).where(CharacterCasting.crew_id == crew_id,
        CharacterCasting.member_id == member_id))).all()
    for row in rows:
        await mark_plan_review(session, crew_id, 'casting_role_changed', now=now,
            script_id=row.script_id, character_name=row.character_name)


async def read(repository, user_id, crew_id, *, object_id=None, page=1, page_size=20, actors=False):
    async with repository.sessions() as session, session.begin():
        actor = await resource_actor(session, user_id, crew_id, writing=True)
        authorize(actor)
        if object_id is None:
            query = select(Script).where(Script.crew_id == crew_id)
            total = await session.scalar(select(func.count()).select_from(query.subquery()))
            rows = (await session.scalars(query.order_by(Script.title, Script.id).offset((page - 1) * page_size).limit(page_size))).all()
            return CastingScriptPage(items=[CastingScript(id=row.id, title=row.title) for row in rows], total=total, page=page, page_size=page_size)
        script = await script_row(session, crew_id, object_id)
        if actors:
            query = actor_query(crew_id)
            total = await session.scalar(select(func.count()).select_from(query.subquery()))
            rows = (await session.scalars(query.order_by(CrewMember.display_name, CrewMember.id).offset((page - 1) * page_size).limit(page_size))).all()
            return ActorPage(items=[ActorChoice(id=row.id, display_name=row.display_name, membership_joined_at=row.joined_at) for row in rows], total=total, page=page, page_size=page_size)
        names = union(current_names(script), select(CharacterCasting.character_name).where(
            CharacterCasting.crew_id == crew_id, CharacterCasting.script_id == script.id)).subquery()
        total = await session.scalar(select(func.count()).select_from(names))
        selected = list((await session.scalars(select(names.c.character_name).order_by(names.c.character_name).offset((page - 1) * page_size).limit(page_size))).all())
        bindings = (await session.scalars(select(CharacterCasting).where(CharacterCasting.crew_id == crew_id,
            CharacterCasting.script_id == script.id, CharacterCasting.character_name.in_(selected)))).all()
        bound = {row.character_name: row for row in bindings}
        eligible = (await session.scalars(actor_query(crew_id).where(CrewMember.id.in_({row.member_id for row in bindings})))).all() if bindings else []
        active = {row.id: row for row in eligible}
        current = set((await session.scalars(current_names(script).where(SceneCharacter.name.in_(selected)))).all())
        entries = []
        for name in selected:
            row = bound.get(name)
            member = active.get(row.member_id) if row else None
            valid = member is not None and member.joined_at == row.membership_joined_at
            entries.append(CastingEntry(character_name=name, is_current_character=name in current,
                member_id=row.member_id if row else None, membership_joined_at=row.membership_joined_at if row else None,
                display_name=member.display_name if valid else row.display_name if row else None,
                status='active' if valid else 'invalid' if row else 'unassigned'))
        return CastingPage(script_id=script.id, lock_version=script.lock_version, items=entries, total=total, page=page, page_size=page_size)


async def save(repository, user_id, crew_id, body, request_id, *, object_id, idempotency_key):
    async with repository.sessions() as session, session.begin():
        actor = await resource_actor(session, user_id, crew_id, writing=True)
        authorize(actor, writing=True)
        key = sha256(idempotency_key.encode()).hexdigest()
        digest = request_digest('casting.save', object_id, body)
        receipt = await session.scalar(select(CreativeReceipt).where(CreativeReceipt.crew_id == crew_id,
            CreativeReceipt.user_id == user_id, CreativeReceipt.key_digest == key))
        if receipt:
            if receipt.membership_joined_at != actor.joined_at:
                raise AuthError(409, 'MEMBERSHIP_CHANGED', '入组身份已变化')
            if receipt.request_digest != digest:
                raise AuthError(409, 'IDEMPOTENCY_KEY_REUSED', '同键不能用于不同内容')
            return CastingResult.model_validate(receipt.response_snapshot)
        script = await script_row(session, crew_id, object_id)
        check_version(script, body.expected_lock_version)
        binding = await session.scalar(select(CharacterCasting).where(CharacterCasting.crew_id == crew_id,
            CharacterCasting.script_id == script.id, CharacterCasting.character_name == body.character_name))
        # Only names already recorded in this script may be bound; old names can still be cleared.
        exists = await session.scalar(select(SceneCharacter.id).join(SceneRevision, SceneRevision.id == SceneCharacter.scene_revision_id).join(
            ScriptVersion, ScriptVersion.id == SceneRevision.script_version_id).where(SceneCharacter.crew_id == crew_id,
            ScriptVersion.script_id == script.id, SceneCharacter.name == body.character_name).limit(1))
        if exists is None and (binding is None or body.member_id is not None):
            raise not_found()
        member = None
        if body.member_id is not None:
            member = await session.scalar(actor_query(crew_id).where(CrewMember.id == body.member_id))
            if member is None:
                raise AuthError(409, 'CAST_MEMBER_UNAVAILABLE', '请选择本剧组当前有效的演员成员')
            if member.joined_at != body.membership_joined_at:
                raise AuthError(409, 'MEMBERSHIP_CHANGED', '演员入组身份已变化，请重新选择')
        now = await session.scalar(select(func.clock_timestamp()))
        if member:
            if binding is None:
                binding = CharacterCasting(id=uuid4(), crew_id=crew_id, script_id=script.id, character_name=body.character_name)
                session.add(binding)
            binding.member_id = member.id
            binding.membership_joined_at = member.joined_at
            binding.display_name = member.display_name
            binding.updated_by = actor.member_id
            binding.updated_at = now
        elif binding:
            await session.execute(delete(CharacterCasting).where(CharacterCasting.id == binding.id, CharacterCasting.crew_id == crew_id))
        script.lock_version += 1
        script.updated_at = now
        await mark_plan_review(session, crew_id, 'casting_changed', now=now, script_id=script.id, character_name=body.character_name)
        result = CastingResult(script_id=script.id, lock_version=script.lock_version, character_name=body.character_name,
            member_id=member.id if member else None, membership_joined_at=member.joined_at if member else None, display_name=member.display_name if member else None)
        session.add(CreativeReceipt(crew_id=crew_id, user_id=user_id, member_id=actor.member_id, membership_joined_at=actor.joined_at,
            key_digest=key, request_digest=digest, response_snapshot=result.model_dump(mode='json')))
        session.add(AuditLog(crew_id=crew_id, actor_type='user', actor_id=user_id, action='casting.save', target_type='script', target_id=script.id,
            result='success', request_id=request_id, after_summary={'character_name': body.character_name,
                'member_id': str(member.id) if member else None, 'lock_version': script.lock_version}))
        await session.flush()
    return result
