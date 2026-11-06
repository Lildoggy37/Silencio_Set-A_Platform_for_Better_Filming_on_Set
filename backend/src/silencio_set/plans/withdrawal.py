from hashlib import sha256
from uuid import uuid4

from sqlalchemy import select, func

from silencio_set.accounts.errors import AuthError
from silencio_set.call_sheets.dependencies import withdraw_for_plan
from silencio_set.crews.models import AuditLog
from silencio_set.plans.models import ShootingPlan, PlanVersion, PlanReceipt, PlanPublicationEvent
from silencio_set.plans.repository import scoped
from silencio_set.plans.schemas import WithdrawView
from silencio_set.resources.domain import check_version
from silencio_set.resources.repository import resource_actor, require
from silencio_set.tasks.repository import request_digest


async def withdraw(repository, user_id, crew_id, version_id, body, request_id, idempotency_key):
    async with repository.sessions() as session, session.begin():
        actor = await resource_actor(session, user_id, crew_id, writing=True)
        require(actor, 'plan.edit'); require(actor, 'plan.publish')
        key = sha256(idempotency_key.encode()).hexdigest()
        digest = request_digest('plan.withdraw', version_id, body)
        receipt = await session.scalar(select(PlanReceipt).where(PlanReceipt.crew_id == crew_id,
            PlanReceipt.user_id == user_id, PlanReceipt.key_digest == key))
        if receipt:
            if receipt.membership_joined_at != actor.joined_at:
                raise AuthError(409, 'MEMBERSHIP_CHANGED', '入组身份已变化')
            if receipt.request_digest != digest:
                raise AuthError(409, 'IDEMPOTENCY_KEY_REUSED', '同键不能用于不同内容')
            return WithdrawView.model_validate(receipt.response_snapshot)
        version = await scoped(session, PlanVersion, crew_id, version_id)
        check_version(version, body.expected_lock_version)
        plan = await scoped(session, ShootingPlan, crew_id, version.plan_id)
        if version.status != 'published' or version.withdrawn_at is not None or plan.current_published_version_id != version.id:
            raise AuthError(409, 'CURRENT_VERSION_CHANGED', '仅能撤回当前正式排期，请刷新后核对')
        now = await session.scalar(select(func.clock_timestamp()))
        version.withdrawn_at = now; version.withdrawn_by = actor.member_id; version.withdrawal_reason = body.reason
        version.lock_version += 1; version.updated_at = now
        plan.current_published_version_id = None
        affected = await withdraw_for_plan(session, crew_id, plan.id, now)
        people = {(person['member_id'], person['membership_joined_at']): person
                  for item in version.publication_snapshot['items'] for person in item['members']}
        session.add(PlanPublicationEvent(id=uuid4(), crew_id=crew_id, plan_version_id=version.id, previous_version_id=None,
            kind='withdrawn', recipient_snapshots=[dict(member_id=key[0], membership_joined_at=key[1]) for key in sorted(people)], created_at=now))
        result = WithdrawView(withdrawn_version_id=version.id, lock_version=version.lock_version, withdrawn_at=now,
            withdrawal_reason=body.reason, affected_call_sheet_version_ids=affected)
        session.add(PlanReceipt(crew_id=crew_id, user_id=user_id, member_id=actor.member_id, membership_joined_at=actor.joined_at,
            key_digest=key, request_digest=digest, response_snapshot=result.model_dump(mode='json')))
        session.add(AuditLog(crew_id=crew_id, actor_type='user', actor_id=user_id, action='plan.withdraw', target_type='plan_version',
            target_id=version.id, result='success', request_id=request_id,
            before_summary={'current_version_id':str(version.id)}, after_summary={'current_version_id':None,
                'lock_version':version.lock_version, 'withdrawal_reason':body.reason, 'affected_call_sheet_count':len(affected)}))
        await session.flush()
    return result
