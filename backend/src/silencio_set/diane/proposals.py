from datetime import timedelta
from uuid import UUID, uuid5

from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError

from silencio_set.accounts.errors import AuthError
from silencio_set.crews.models import AuditLog, CrewMember
from silencio_set.diane.proposal_models import TaskProposal
from silencio_set.diane.proposal_schemas import ProposalView
from silencio_set.resources.repository import resource_actor
from silencio_set.tasks.repository import require_permission, validate_assignment
from silencio_set.tasks.schemas import TaskCreate


def view(row):
    return ProposalView(id=row.id, crew_id=row.crew_id, tasks=row.tasks, status=row.status,
        lock_version=row.lock_version, created_at=row.created_at, expires_at=row.expires_at,
        decided_at=row.decided_at, created_task_ids=row.created_task_ids,
        member_names={item['id']: item['name'] for item in row.member_snapshot})


class ProposalRepository:
    def __init__(self, sessions, tasks): self.sessions, self.tasks = sessions, tasks

    async def actor(self, session, user, crew_id, writing=False):
        actor = await resource_actor(session, user.id, crew_id, writing=writing)
        require_permission(actor, 'task.manage')
        return actor

    async def snapshot(self, session, crew_id, tasks):
        now = await session.scalar(select(func.clock_timestamp()))
        ids = set()
        for task in tasks:
            if task.due_at <= now: raise AuthError(409, 'PROPOSAL_DEADLINE_PASSED', '任务截止时间已过，请重新拟定。')
            await validate_assignment(session, crew_id, task.owner_member_id, task.reviewer_member_id,
                task.collaborator_member_ids, task.requires_review)
            ids.update([task.owner_member_id, task.reviewer_member_id, *task.collaborator_member_ids])
        members = (await session.scalars(select(CrewMember).where(CrewMember.crew_id == crew_id,
            CrewMember.id.in_(ids), CrewMember.status == 'active').order_by(CrewMember.id))).all()
        return [{'id':str(item.id), 'version':item.lock_version, 'joined_at':item.joined_at.isoformat(), 'name':item.display_name} for item in members]

    def scoped(self, crew_id, user, actor):
        return select(TaskProposal).where(TaskProposal.crew_id == crew_id, TaskProposal.user_id == user.id,
            TaskProposal.member_id == actor.member_id, TaskProposal.membership_joined_at == actor.joined_at)

    async def create(self, user, crew_id, tasks, request_id, generation=None):
        async with self.sessions() as session, session.begin():
            actor = await self.actor(session, user, crew_id, writing=True)
            members = await self.snapshot(session, crew_id, tasks)
            now = await session.scalar(select(func.clock_timestamp()))
            row = TaskProposal(crew_id=crew_id, user_id=user.id, member_id=actor.member_id,
                membership_joined_at=actor.joined_at, status='pending', lock_version=1,
                tasks=[task.model_dump(mode='json') for task in tasks], member_snapshot=members,
                generation=generation, created_task_ids=[], created_at=now, expires_at=now+timedelta(minutes=30))
            session.add(row); await session.flush()
            self.audit(session, user, row, 'draft', request_id)
            return view(row)

    async def list(self, user, crew_id):
        async with self.sessions() as session:
            actor = await self.actor(session, user, crew_id)
            rows = (await session.scalars(self.scoped(crew_id, user, actor).order_by(TaskProposal.created_at.desc(), TaskProposal.id).limit(10))).all()
            return [view(row) for row in rows]

    async def decide(self, user, crew_id, proposal_id, body, request_id):
        try:
            async with self.sessions() as session, session.begin():
                actor = await self.actor(session, user, crew_id, writing=True)
                row = await session.scalar(self.scoped(crew_id, user, actor).where(TaskProposal.id == proposal_id).with_for_update())
                if row is None: raise AuthError(404, 'NOT_FOUND', '提案不存在或无权访问。')
                target = 'completed' if body.decision == 'accept' else 'rejected'
                if row.status == target and row.decision_version == body.expected_lock_version: return view(row)
                if row.status != 'pending' or row.lock_version != body.expected_lock_version:
                    raise AuthError(409, 'VERSION_CONFLICT', '提案已变化，请读取最新结果。')
                now = await session.scalar(select(func.clock_timestamp()))
                if body.decision == 'accept':
                    if row.expires_at <= now: raise AuthError(409, 'PROPOSAL_EXPIRED', '提案已过期，请重新拟定。')
                    tasks = [TaskCreate.model_validate(task) for task in row.tasks]
                    if await self.snapshot(session, crew_id, tasks) != row.member_snapshot:
                        raise AuthError(409, 'PROPOSAL_STALE', '相关成员或授权已变化，请重新拟定并确认。')
                    ids = []
                    for index, task in enumerate(tasks):
                        result = await self.tasks.create_in_transaction(session, user, crew_id, task, request_id,
                            str(uuid5(row.id, f'task-{index}')))
                        ids.append(str(result.id))
                    row.created_task_ids = ids
                row.status, row.decided_at, row.decision_version = target, now, row.lock_version
                row.lock_version += 1
                self.audit(session, user, row, body.decision, request_id)
                await session.flush()
                return view(row)
        except DBAPIError:
            raise AuthError(503, 'SERVICE_UNAVAILABLE', '创建未能完成，事务已回滚；请读取提案状态后重试。') from None

    @staticmethod
    def audit(session, user, row, action, request_id):
        session.add(AuditLog(crew_id=row.crew_id, actor_type='user', actor_id=user.id,
            action='diane.task_proposal.'+action, target_type='diane_task_proposal', target_id=row.id,
            run_id=row.id, result='success', request_id=request_id,
            after_summary={'status':row.status, 'lock_version':row.lock_version,
                'task_count':len(row.tasks), 'created_task_ids':row.created_task_ids}))
