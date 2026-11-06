import json
from datetime import timedelta
from hashlib import sha256
from secrets import token_urlsafe
from uuid import uuid4

from pydantic import TypeAdapter
from sqlalchemy import select, func

from silencio_set.accounts.errors import AuthError
from silencio_set.crews.models import AuditLog
from silencio_set.plans.checking import collect_check
from silencio_set.plans.models import ShootingPlan, PlanVersion, PlanReceipt, PlanPreflight, PlanPublishConfirmation, PlanPublicationEvent
from silencio_set.plans.repository import scoped
from silencio_set.plans.schemas import PublicationPreflight, UnknownItem, PublishView
from silencio_set.resources.domain import check_version
from silencio_set.resources.repository import resource_actor, require
from silencio_set.tasks.repository import request_digest

JSON = TypeAdapter(dict)


def snapshot_digest(snapshot):
    normalized = JSON.dump_python(snapshot, mode='json')
    return sha256(json.dumps(normalized, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


async def create_preflight(session, actor, version, ttl_seconds):
    now = await session.scalar(select(func.clock_timestamp()))
    report, snapshot = await collect_check(session, version, now)
    token = token_urlsafe(32)
    unknown = [UnknownItem(**item.model_dump(), item_id=uuid4()) for item in report.unknown_items]
    record = PlanPreflight(id=uuid4(), crew_id=version.crew_id, plan_version_id=version.id,
        checked_by=actor.member_id, membership_joined_at=actor.joined_at, checked_lock_version=version.lock_version,
        expected_current_published_version_id=snapshot['current_published_version_id'],
        token_digest=sha256(token.encode()).hexdigest(), snapshot_digest=snapshot_digest(snapshot),
        data_snapshot=JSON.dump_python(snapshot, mode='json'), unknown_items=[item.model_dump(mode='json') for item in unknown],
        created_at=now, expires_at=now + timedelta(seconds=ttl_seconds))
    session.add(record)
    await session.flush()
    return PublicationPreflight(**report.model_dump(exclude={'unknown_items', 'can_publish', 'checked_data_revision', 'expected_current_published_version_id', 'expires_at'}),
        unknown_items=unknown, checked_data_revision=token, expected_current_published_version_id=record.expected_current_published_version_id,
        expires_at=record.expires_at, can_publish=version.status == 'draft' and not report.blockers and 'plan.publish' in actor.permissions)


def check_evidence(record, *, actor, version, current_id, body, now, report, snapshot):
    if record is None or record.checked_by != actor.member_id or record.membership_joined_at != actor.joined_at or record.plan_version_id != version.id:
        raise AuthError(409, 'PREFLIGHT_STALE', '检查记录无效，请重新检查排期')
    if record.expires_at <= now or record.checked_lock_version != version.lock_version or record.expected_current_published_version_id != current_id or record.snapshot_digest != snapshot_digest(snapshot):
        raise AuthError(409, 'PREFLIGHT_STALE', '检查依据已变化或过期，请重新检查排期')
    if report.blockers:
        raise AuthError(409, 'PLAN_BLOCKED', '仍有已知冲突或未落实事项，不能发布')
    if {str(item) for item in body.confirmed_unknown_item_ids} != {item['item_id'] for item in record.unknown_items}:
        raise AuthError(409, 'UNKNOWN_CONFIRMATION_REQUIRED', '须逐项确认本次检查列出的全部未知档期')


async def publish(repository, user_id, crew_id, version_id, body, request_id, idempotency_key):
    async with repository.sessions() as session, session.begin():
        actor = await resource_actor(session, user_id, crew_id, writing=True)
        for code in ('plan.publish', 'plan.edit', 'availability.read_all', 'resource.read'):
            require(actor, code)
        key = sha256(idempotency_key.encode()).hexdigest()
        digest = request_digest('plan.publish', version_id, body)
        receipt = await session.scalar(select(PlanReceipt).where(PlanReceipt.crew_id == crew_id, PlanReceipt.user_id == user_id, PlanReceipt.key_digest == key))
        if receipt:
            if receipt.membership_joined_at != actor.joined_at:
                raise AuthError(409, 'MEMBERSHIP_CHANGED', '入组身份已变化')
            if receipt.request_digest != digest:
                raise AuthError(409, 'IDEMPOTENCY_KEY_REUSED', '同键不能用于不同内容')
            return PublishView.model_validate(receipt.response_snapshot)
        version = await scoped(session, PlanVersion, crew_id, version_id)
        check_version(version, body.expected_lock_version)
        if version.status != 'draft':
            raise AuthError(409, 'INVALID_STATE', '仅草稿可以发布')
        plan = await scoped(session, ShootingPlan, crew_id, version.plan_id)
        previous_id = plan.current_published_version_id
        if previous_id != body.expected_current_published_version_id:
            raise AuthError(409, 'CURRENT_VERSION_CHANGED', '当前发布版本已变化，请重新检查')
        now = await session.scalar(select(func.clock_timestamp()))
        record = await session.scalar(select(PlanPreflight).where(PlanPreflight.crew_id == crew_id,
            PlanPreflight.token_digest == sha256(body.checked_data_revision.encode()).hexdigest()))
        report, snapshot = await collect_check(session, version, now)
        check_evidence(record, actor=actor, version=version, current_id=previous_id, body=body, now=now, report=report, snapshot=snapshot)
        previous = await scoped(session, PlanVersion, crew_id, previous_id) if previous_id else None
        version.status = 'published'; version.lock_version += 1
        version.published_by = actor.member_id; version.published_at = now; version.updated_at = now
        version.publication_snapshot = JSON.dump_python(snapshot, mode='json'); version.review_reasons = []
        plan.current_published_version_id = version.id
        from silencio_set.call_sheets.dependencies import invalidate_for_plan
        affected_call_sheets = await invalidate_for_plan(session, crew_id, plan.id, version, now)
        if record.unknown_items:
            session.add(PlanPublishConfirmation(id=uuid4(), crew_id=crew_id, plan_version_id=version.id,
                confirmed_by=actor.member_id, confirmed_at=now, checked_data_revision=record.token_digest,
                unknown_availability_snapshot=record.unknown_items))
        recipients = {}
        for item in version.publication_snapshot['items'] + (previous.publication_snapshot['items'] if previous else []):
            for member in item['members']:
                recipient = {'member_id': member['member_id'], 'membership_joined_at': member['membership_joined_at']}
                recipients[(recipient['member_id'], recipient['membership_joined_at'])] = recipient
        session.add(PlanPublicationEvent(id=uuid4(), crew_id=crew_id, plan_version_id=version.id, previous_version_id=previous_id,
            kind='published',
            recipient_snapshots=[recipients[key] for key in sorted(recipients)], created_at=now))
        result = PublishView(published_version_id=version.id, lock_version=version.lock_version, previous_published_version_id=previous_id, affected_call_sheet_version_ids=affected_call_sheets)
        session.add(PlanReceipt(crew_id=crew_id, user_id=user_id, member_id=actor.member_id, membership_joined_at=actor.joined_at,
            key_digest=key, request_digest=digest, response_snapshot=result.model_dump(mode='json')))
        session.add(AuditLog(crew_id=crew_id, actor_type='user', actor_id=user_id, action='plan.publish', target_type='plan_version', target_id=version.id,
            result='success', request_id=request_id, before_summary={'current_version_id': str(previous_id) if previous_id else None},
            after_summary={'current_version_id': str(version.id), 'lock_version': version.lock_version, 'unknown_confirmations': len(record.unknown_items)}))
        await session.flush()
    return result
