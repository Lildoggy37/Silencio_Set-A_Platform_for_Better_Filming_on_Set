"""Task writes and member exits share the crew coordinator and lock order."""
from dataclasses import dataclass
from hashlib import sha256
import json
from uuid import UUID, uuid4

from sqlalchemy import delete, exists, func, or_, select

from silencio_set.accounts.errors import AuthError, expired_session
from silencio_set.accounts.models import User
from silencio_set.accounts.profile import require_completed_profile
from silencio_set.crews.models import AuditLog, CrewMember, MemberPermissionOverride
from silencio_set.crews.organization import MAX_VERSION, organization_actor
from silencio_set.crews.permissions import effective_permissions
from silencio_set.crews.repository import not_found, role_map
from silencio_set.tasks.models import Task, TaskCollaborator, TaskSubmission, TaskReceipt, TaskChangeEvent
from silencio_set.tasks.schemas import ExitResponsibilityPage, ExitTaskItem, SubmissionPage, SubmissionView, TaskDetail, TaskPage, TaskView

TERMINAL = {"completed", "cancelled"}


@dataclass(frozen=True)
class TaskActor:
    member_id: UUID
    permissions: frozenset[str]


async def permissions_for(session, members):
    roles = await role_map(session, members)
    overrides = {member.id: [] for member in members}
    if members:
        rows = (await session.execute(select(MemberPermissionOverride.member_id,
            MemberPermissionOverride.permission_code, MemberPermissionOverride.effect).where(
            MemberPermissionOverride.member_id.in_(overrides)))).all()
        for member_id, code, effect in rows:
            overrides[member_id].append((code, effect))
    return {member.id: effective_permissions(roles[member.id], overrides[member.id]) for member in members}


async def task_actor(session, user_id, crew_id, *, writing=False):
    if writing:
        member = await organization_actor(session, user_id, crew_id, None)
    else:
        user = await session.scalar(select(User).where(User.id == user_id, User.status == "active"))
        if user is None:
            raise expired_session()
        require_completed_profile(user)
        member = await session.scalar(select(CrewMember).where(CrewMember.crew_id == crew_id,
            CrewMember.user_id == user_id, CrewMember.status == "active"))
        if member is None:
            raise not_found()
    return TaskActor(member.id, (await permissions_for(session, [member]))[member.id])


def require_permission(actor, permission):
    if permission not in actor.permissions:
        raise AuthError(403, "FORBIDDEN", "当前没有执行该任务操作的权限")


def visible_tasks(crew_id, actor):
    query = select(Task).where(Task.crew_id == crew_id)
    if "task.manage" in actor.permissions:
        return query
    require_permission(actor, "task.read")
    collaborator = exists(select(1).where(TaskCollaborator.crew_id == crew_id,
        TaskCollaborator.task_id == Task.id, TaskCollaborator.member_id == actor.member_id))
    return query.where(or_(Task.owner_member_id == actor.member_id,
        Task.reviewer_member_id == actor.member_id, Task.created_by_member_id == actor.member_id, collaborator))


def allowed_actions(task, actor):
    actions = []
    if task.status not in TERMINAL and "task.manage" in actor.permissions:
        actions.append("cancel")
        if task.status in {"pending", "in_progress"}:
            actions.append("edit")
    if actor.member_id == task.owner_member_id and "task.work" in actor.permissions:
        if task.status == "pending": actions.append("start")
        if task.status == "in_progress": actions.append("submit")
    if task.status == "pending_review" and actor.member_id == task.reviewer_member_id and "task.review" in actor.permissions:
        actions.append("review")
    return sorted(actions)


def check_action(task, actor, action, version):
    # Object authority is checked independently of status and action hints.
    if action in {"start", "submit"}:
        require_permission(actor, "task.work")
        if task.owner_member_id != actor.member_id: raise AuthError(403, "FORBIDDEN", "仅任务负责人可以执行")
    elif action == "review":
        require_permission(actor, "task.review")
        if task.reviewer_member_id != actor.member_id: raise AuthError(403, "FORBIDDEN", "仅指定确认人可以验收")
    else:
        require_permission(actor, "task.manage")
    if task.lock_version != version:
        raise AuthError(409, "VERSION_CONFLICT", "任务已变化，请重新读取后确认")
    if task.lock_version >= MAX_VERSION:
        raise AuthError(409, "VERSION_EXHAUSTED", "任务版本达到上限")
    if action not in allowed_actions(task, actor):
        raise AuthError(409, "INVALID_STATE", "当前任务状态不允许此操作")


def request_digest(action, task_id, body):
    canonical = json.dumps({"action": action, "target": str(task_id) if task_id else None,
        "body": body.model_dump(mode="json")}, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return sha256(canonical.encode()).hexdigest()


async def collaborators_for(session, tasks):
    mapping = {task.id: [] for task in tasks}
    if tasks:
        rows = (await session.execute(select(TaskCollaborator.task_id, TaskCollaborator.member_id).where(
            TaskCollaborator.crew_id == tasks[0].crew_id, TaskCollaborator.task_id.in_(mapping)))).all()
        for task_id, member_id in rows:
            mapping[task_id].append(member_id)
    return {task_id: sorted(members) for task_id, members in mapping.items()}


def task_view(task, collaborators, actor):
    return TaskView(**{field: getattr(task, field) for field in TaskView.model_fields
        if field not in {"collaborator_member_ids", "allowed_actions"}},
        collaborator_member_ids=collaborators, allowed_actions=allowed_actions(task, actor))


async def validate_assignment(session, crew_id, owner, reviewer, collaborators):
    ids = {owner, reviewer, *collaborators}
    members = (await session.scalars(select(CrewMember).join(User, User.id == CrewMember.user_id).where(
        CrewMember.crew_id == crew_id, CrewMember.id.in_(ids), CrewMember.status == "active", User.status == "active"))).all()
    if {member.id for member in members} != ids:
        raise AuthError(409, "MEMBER_INACTIVE", "指派成员必须仍在当前剧组且账号有效")
    permissions = await permissions_for(session, members)
    if "task.work" not in permissions[owner] or "task.review" not in permissions[reviewer] or any(
        not ({"task.read", "task.manage"} & permissions[member_id]) for member_id in ids):
        raise AuthError(409, "MEMBER_NOT_ELIGIBLE", "指派成员缺少对应任务权限")


def blocking_tasks_query(crew_id, member_id):
    return select(Task.id).where(Task.crew_id == crew_id, Task.status.not_in(TERMINAL),
        or_(Task.owner_member_id == member_id, Task.reviewer_member_id == member_id))


async def check_task_responsibilities(session, crew_id, member_id):
    if await session.scalar(blocking_tasks_query(crew_id, member_id).limit(1)) is not None:
        raise AuthError(409, "MEMBER_HAS_RESPONSIBILITIES", "仍有负责或确认的未完成任务，请先转交或处理")


class TaskRepository:
    def __init__(self, sessions):
        self.sessions = sessions

    async def responsibilities(self, user_id, crew_id, member_id, page, page_size):
        async with self.sessions() as session, session.begin():
            # This infrequent exit preview shares the coordinator so counts,
            # member version and task selection refer to the same crew state.
            actor = await task_actor(session, user_id, crew_id, writing=True)
            if member_id != actor.member_id: require_permission(actor, "member.remove")
            member = await session.scalar(select(CrewMember).where(CrewMember.crew_id == crew_id,
                CrewMember.id == member_id, CrewMember.status == "active"))
            if member is None: raise not_found()
            blockers = blocking_tasks_query(crew_id, member_id)
            blocking_total = await session.scalar(select(func.count()).select_from(blockers.subquery()))
            can_read = bool({"task.read", "task.manage"} & actor.permissions)
            total = 0; rows = []
            if can_read:
                query = visible_tasks(crew_id, actor).where(Task.id.in_(blockers.correlate(None)))
                total = await session.scalar(select(func.count()).select_from(query.subquery()))
                rows = (await session.scalars(query.order_by(Task.due_at, Task.id).offset((page - 1) * page_size).limit(page_size))).all()
            result = ExitResponsibilityPage(member_id=member_id, member_lock_version=member.lock_version,
                items=[ExitTaskItem(id=row.id, title=row.title, status=row.status, due_at=row.due_at, lock_version=row.lock_version,
                    is_owner=row.owner_member_id == member_id, is_reviewer=row.reviewer_member_id == member_id) for row in rows],
                total=total, blocking_total=blocking_total, restricted_total=blocking_total - total, page=page, page_size=page_size)
        return result

    async def list(self, user_id, crew_id, page, page_size, *, status=None, owner_member_id=None, due_from=None, due_to=None):
        async with self.sessions() as session:
            actor = await task_actor(session, user_id, crew_id)
            query = visible_tasks(crew_id, actor)
            if status is not None: query = query.where(Task.status == status)
            if owner_member_id is not None: query = query.where(Task.owner_member_id == owner_member_id)
            if due_from is not None: query = query.where(Task.due_at >= due_from)
            if due_to is not None: query = query.where(Task.due_at <= due_to)
            total = await session.scalar(select(func.count()).select_from(query.subquery()))
            tasks = (await session.scalars(query.order_by(Task.due_at, Task.id).offset((page - 1) * page_size).limit(page_size))).all()
            collaborators = await collaborators_for(session, tasks)
            return TaskPage(items=[task_view(task, collaborators[task.id], actor) for task in tasks], page=page, page_size=page_size, total=total)

    async def get(self, user_id, crew_id, task_id, *, page=None, page_size=20):
        async with self.sessions() as session:
            actor = await task_actor(session, user_id, crew_id)
            task = await session.scalar(visible_tasks(crew_id, actor).where(Task.id == task_id))
            if task is None: raise not_found()
            query = select(TaskSubmission).where(TaskSubmission.crew_id == crew_id, TaskSubmission.task_id == task_id)
            total = await session.scalar(select(func.count()).select_from(query.subquery()))
            if page is not None:
                rows = (await session.scalars(query.order_by(TaskSubmission.submission_no.desc()).offset((page - 1) * page_size).limit(page_size))).all()
                return SubmissionPage(items=[SubmissionView.model_validate(row) for row in rows], page=page, page_size=page_size, total=total)
            current = await session.scalar(query.order_by(TaskSubmission.submission_no.desc()).limit(1))
            collaborators = (await collaborators_for(session, [task]))[task.id]
            names = dict((await session.execute(select(CrewMember.id, CrewMember.display_name).where(
                CrewMember.crew_id == crew_id, CrewMember.id.in_({task.owner_member_id, task.reviewer_member_id, *collaborators})))).all())
            return TaskDetail(task=task_view(task, collaborators, actor), member_names=names,
                current_submission=SubmissionView.model_validate(current) if current else None, submission_count=total)

    async def mutate(self, user_id, crew_id, action, body, request_id, *, task_id=None, idempotency_key=None):
        if action in {"create", "submit", "review", "cancel"} and not idempotency_key:
            raise ValueError("This task operation requires an idempotency key")
        async with self.sessions() as session, session.begin():
            actor = await task_actor(session, user_id, crew_id, writing=True)
            if action == "create": require_permission(actor, "task.manage")
            task = None
            if task_id is not None:
                task = await session.scalar(visible_tasks(crew_id, actor).where(Task.id == task_id).with_for_update())
                if task is None: raise not_found()
            digest = request_digest(action, task_id, body)
            key_digest = sha256(idempotency_key.encode()).hexdigest() if idempotency_key else None
            if key_digest:
                receipt = await session.scalar(select(TaskReceipt).where(TaskReceipt.crew_id == crew_id,
                    TaskReceipt.user_id == user_id, TaskReceipt.key_digest == key_digest))
                if receipt is not None:
                    if receipt.request_digest != digest:
                        raise AuthError(409, "IDEMPOTENCY_KEY_REUSED", "同一幂等键不能用于不同请求")
                    # Recheck CURRENT visibility and operation permission even
                    # when returning an earlier successful result.
                    current = task or await session.scalar(visible_tasks(crew_id, actor).where(Task.id == receipt.task_id))
                    if current is None: raise not_found()
                    required = "task.work" if action == "submit" else "task.review" if action == "review" else "task.manage"
                    require_permission(actor, required)
                    if action == "submit" and current.owner_member_id != actor.member_id or action == "review" and current.reviewer_member_id != actor.member_id:
                        raise AuthError(403, "FORBIDDEN", "当前已无此任务操作资格")
                    snapshot = TaskView.model_validate(receipt.response_snapshot)
                    return snapshot.model_copy(update={"allowed_actions": []})  # Historical result; GET before the next action.
            now = await session.scalar(select(func.clock_timestamp()))
            old_collaborators = (await collaborators_for(session, [task]))[task.id] if task else []
            old_responsible = {task.owner_member_id, task.reviewer_member_id} if task else set()
            before = {"status": task.status, "lock_version": task.lock_version,
                "owner_member_id": str(task.owner_member_id), "reviewer_member_id": str(task.reviewer_member_id),
                "collaborator_member_ids": [str(item) for item in old_collaborators]} if task else None
            if action == "create":
                if body.due_at <= now: raise AuthError(422, "VALIDATION_ERROR", "截止时间必须晚于当前时间")
                await validate_assignment(session, crew_id, body.owner_member_id, body.reviewer_member_id, body.collaborator_member_ids)
                task = Task(id=uuid4(), crew_id=crew_id, created_by_member_id=actor.member_id,
                    status="pending", lock_version=1, created_at=now, updated_at=now,
                    **body.model_dump(exclude={"collaborator_member_ids", "scene_id"}))
                session.add(task)
                await session.flush()
                collaborators = body.collaborator_member_ids
            else:
                check_action(task, actor, action, body.expected_lock_version)
                collaborators = old_collaborators
                if action == "edit":
                    changes = body.model_dump(exclude_unset=True, exclude={"expected_lock_version", "collaborator_member_ids"})
                    if "due_at" in changes and changes["due_at"] <= now: raise AuthError(422, "VALIDATION_ERROR", "新的截止时间必须晚于当前时间")
                    collaborators = body.collaborator_member_ids if "collaborator_member_ids" in body.model_fields_set else collaborators
                    await validate_assignment(session, crew_id, changes.get("owner_member_id", task.owner_member_id), changes.get("reviewer_member_id", task.reviewer_member_id), collaborators)
                    for field, value in changes.items(): setattr(task, field, value)
                elif action == "start": task.status = "in_progress"
                elif action == "submit":
                    previous = await session.scalar(select(func.max(TaskSubmission.submission_no)).where(TaskSubmission.task_id == task.id, TaskSubmission.crew_id == crew_id))
                    session.add(TaskSubmission(id=uuid4(), crew_id=crew_id, task_id=task.id, submission_no=(previous or 0) + 1,
                        submitted_by=actor.member_id, completion_note=body.completion_note, review_status="pending", submitted_at=now))
                    task.status = "pending_review"
                elif action in {"review", "cancel"}:
                    current = await session.scalar(select(TaskSubmission).where(TaskSubmission.crew_id == crew_id,
                        TaskSubmission.task_id == task.id, TaskSubmission.review_status == "pending").with_for_update())
                    if action == "review":
                        if current is None or current.id != body.submission_id: raise AuthError(409, "INVALID_STATE", "请审核当前待确认提交")
                        current.review_status = "accepted" if body.decision == "accept" else "rejected"
                        current.review_note = body.review_note
                        task.status = "completed" if body.decision == "accept" else "in_progress"
                    else:
                        task.status = "cancelled"; task.cancel_reason = body.reason
                        if current:
                            current.review_status = "cancelled"; current.review_note = None
                    if current:
                        current.reviewed_by = actor.member_id; current.reviewed_at = now
                else: raise ValueError("Unsupported task operation")
                task.lock_version += 1; task.updated_at = now
            if action in {"create", "edit"}:
                await session.execute(delete(TaskCollaborator).where(TaskCollaborator.crew_id == crew_id, TaskCollaborator.task_id == task.id))
                for member_id in collaborators:
                    session.add(TaskCollaborator(crew_id=crew_id, task_id=task.id, member_id=member_id))
            result = task_view(task, collaborators, actor)
            session.add(AuditLog(crew_id=crew_id, actor_type="user", actor_id=user_id, action=f"task.{action}",
                target_type="task", target_id=task.id, result="success", request_id=request_id, before_summary=before,
                after_summary={"status": task.status, "lock_version": task.lock_version,
                    "owner_member_id": str(task.owner_member_id), "reviewer_member_id": str(task.reviewer_member_id),
                    "collaborator_member_ids": [str(item) for item in collaborators]}))
            recipients = sorted({task.owner_member_id, task.reviewer_member_id, task.created_by_member_id, *old_responsible, *collaborators, *old_collaborators})
            session.add(TaskChangeEvent(crew_id=crew_id, task_id=task.id, task_version=task.lock_version,
                kind=action, actor_member_id=actor.member_id, recipient_member_ids=[str(item) for item in recipients]))
            if key_digest:
                session.add(TaskReceipt(crew_id=crew_id, user_id=user_id, task_id=task.id, key_digest=key_digest,
                    request_digest=digest, response_snapshot=result.model_dump(mode="json")))
            await session.flush()
        return result
