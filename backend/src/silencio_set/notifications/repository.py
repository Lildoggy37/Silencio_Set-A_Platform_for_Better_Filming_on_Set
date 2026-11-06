from uuid import UUID, uuid4

from sqlalchemy import func, literal, select, union_all

from silencio_set.accounts.errors import AuthError
from silencio_set.crews.models import AuditLog, Crew, CrewMember
from silencio_set.accounts.models import User
from silencio_set.crews.repository import not_found
from silencio_set.notifications.models import Notification, NotificationRecipient
from silencio_set.notifications.schemas import NotificationPage
from silencio_set.notifications.queries import notification_query, view, current_condition
from silencio_set.notifications.publications import consume_publication
from silencio_set.plans.models import PlanPublicationEvent
from silencio_set.call_sheets.models import CallSheetEvent
from silencio_set.tasks.models import Task, TaskChangeEvent
from silencio_set.tasks.repository import collaborators_for, permissions_for, require_permission, task_actor

EVENT_TITLES = {"create": "收到新的筹备任务", "edit": "任务安排已调整", "start": "任务已开始",
    "submit": "任务成果已提交", "complete": "任务已完成", "review": "任务验收有更新", "cancel": "任务已取消"}


def can_receive(task, member_id, permissions, collaborators):
    if "notification.read_self" not in permissions:
        return False
    return "task.manage" in permissions or "task.read" in permissions and member_id in {
        task.owner_member_id, task.reviewer_member_id, task.created_by_member_id, *collaborators}


def needs_confirmation(event, task, member_id):
    if not task.requires_review: return False
    return (event.kind in {"create", "edit"} and member_id in {task.owner_member_id, task.reviewer_member_id}
        or event.kind == "submit" and member_id == task.reviewer_member_id)


class NotificationRepository:
    def __init__(self, sessions):
        self.sessions = sessions

    async def list(self, user_id, crew_id, page, page_size, *, unread=None, needs_acknowledgement=None):
        async with self.sessions() as session:
            actor = await task_actor(session, user_id, crew_id)
            query = notification_query(crew_id, actor)
            if unread is not None:
                query = query.where(NotificationRecipient.read_at.is_(None) if unread else NotificationRecipient.read_at.is_not(None))
            if needs_acknowledgement is not None:
                pending = (NotificationRecipient.requires_confirmation.is_(True) & NotificationRecipient.confirmed_at.is_(None)
                    & current_condition())
                query = query.where(pending if needs_acknowledgement else ~pending)
            total = await session.scalar(select(func.count()).select_from(query.subquery()))
            rows = (await session.execute(query.order_by(Notification.created_at.desc(), Notification.id.desc())
                .offset((page - 1) * page_size).limit(page_size))).all()
            return NotificationPage(items=[view(row) for row in rows], page=page, page_size=page_size, total=total)

    async def get(self, user_id, crew_id, notification_id):
        async with self.sessions() as session:
            actor = await task_actor(session, user_id, crew_id)
            row = (await session.execute(notification_query(crew_id, actor).where(Notification.id == notification_id))).one_or_none()
            if row is None: raise not_found()
            return view(row)

    async def mark(self, user_id, crew_id, notification_id, action, request_id, *, object_version=None):
        async with self.sessions() as session, session.begin():
            actor = await task_actor(session, user_id, crew_id, writing=True)
            row = (await session.execute(notification_query(crew_id, actor).where(Notification.id == notification_id)
                .with_for_update(of=NotificationRecipient))).one_or_none()
            if row is None: raise not_found()
            notice, recipient, source = row
            if action == "acknowledge":
                require_permission(actor, "notification.acknowledge_self")
                if object_version != source.object_version: raise AuthError(409, "VERSION_CONFLICT", "请确认通知所引用的版本")
                if not recipient.requires_confirmation: raise AuthError(409, "CONFIRMATION_NOT_REQUIRED", "该通知不需要确认知悉")
                if recipient.confirmed_at is None and not source.is_current:
                    raise AuthError(409, "NOTIFICATION_OUTDATED", "安排已更新或失效，请查看最新通知与当前安排")
            elif action != "read": raise ValueError("Unsupported notification operation")
            changed = recipient.read_at is None or action == "acknowledge" and recipient.confirmed_at is None
            if changed:
                now = await session.scalar(select(func.clock_timestamp()))
                recipient.read_at = recipient.read_at or now
                if action == "acknowledge": recipient.confirmed_at = recipient.confirmed_at or now
                session.add(AuditLog(crew_id=crew_id, actor_type="user", actor_id=user_id,
                    action=f"notification.{action}", target_type="notification", target_id=notice.id,
                    result="success", request_id=request_id, after_summary={"source_type": source.source_type, "object_version": source.object_version}))
                await session.flush()
            result = view(row)
        return result


class NotificationConsumer:
    """One local PostgreSQL transaction per event. No broker/SMTP side effects."""
    def __init__(self, sessions):
        self.sessions = sessions

    async def consume_once(self):
        async with self.sessions() as session, session.begin():
            pending = union_all(
                select(TaskChangeEvent.id, TaskChangeEvent.crew_id, literal('task').label('kind'), TaskChangeEvent.created_at)
                    .where(TaskChangeEvent.dispatch_status == 'pending'),
                select(PlanPublicationEvent.id, PlanPublicationEvent.crew_id, literal('plan').label('kind'), PlanPublicationEvent.created_at)
                    .where(PlanPublicationEvent.consumed_at.is_(None)),
                select(CallSheetEvent.id, CallSheetEvent.crew_id, literal('call_sheet').label('kind'), CallSheetEvent.created_at)
                    .where(CallSheetEvent.consumed_at.is_(None))).subquery()
            candidate = (await session.execute(select(pending.c.id, pending.c.crew_id, pending.c.kind)
                .order_by(pending.c.created_at, pending.c.id, pending.c.kind).limit(1))).one_or_none()
            if candidate is None: return False
            event_id, crew_id, kind = candidate
            # Acquire the crew BEFORE the event/member rows. Task edits, exits
            # and role changes cannot interleave with recipient selection.
            crew = await session.scalar(select(Crew).where(Crew.id == crew_id).with_for_update())
            if crew is None: return False
            if kind != 'task':
                return await consume_publication(session, crew_id, event_id, kind)
            event = await session.scalar(select(TaskChangeEvent).where(TaskChangeEvent.id == event_id,
                TaskChangeEvent.crew_id == crew_id, TaskChangeEvent.dispatch_status == "pending").with_for_update(skip_locked=True))
            if event is None: return False
            try:
                ids = {UUID(value) for value in event.recipient_member_ids} if isinstance(event.recipient_member_ids, list) and len(event.recipient_member_ids) <= 45 else None
            except (ValueError, TypeError, AttributeError):
                ids = None
            if ids is None or event.kind not in EVENT_TITLES:
                event.dispatch_status = "failed"
                return True
            # A committed notification implies a committed dispatched event.
            # This lookup is defensive for manual recovery; never add receivers
            # to an already formed notification or revive old read receipts.
            existing = await session.scalar(select(Notification.id).where(Notification.source_event_id == event.id))
            if existing is not None:
                event.dispatch_status = "dispatched"
                return True
            task = await session.scalar(select(Task).where(Task.id == event.task_id, Task.crew_id == crew_id))
            if task is None or not 1 <= event.task_version <= task.lock_version:
                event.dispatch_status = "failed"
                return True
            collaborators = (await collaborators_for(session, [task]))[task.id]
            members = (await session.scalars(select(CrewMember).join(User, User.id == CrewMember.user_id).where(
                CrewMember.crew_id == crew_id, CrewMember.id.in_(ids), CrewMember.status == "active", User.status == "active",
                CrewMember.joined_at <= event.created_at))).all()
            permissions = await permissions_for(session, members)
            recipients = [member for member in members if can_receive(task, member.id, permissions[member.id], collaborators)]
            notice = Notification(id=uuid4(), crew_id=crew_id, source_event_id=event.id,
                event_key=f"task:{event.task_id}:{event.task_version}", event_type=f"task.{event.kind}",
                title=EVENT_TITLES[event.kind], summary="筹备任务有更新，请核对通知版本并查看当前任务。", created_at=event.created_at)
            session.add(notice)
            await session.flush()
            for member in recipients:
                session.add(NotificationRecipient(crew_id=crew_id, notification_id=notice.id, member_id=member.id,
                    membership_joined_at=member.joined_at, requires_confirmation=needs_confirmation(event, task, member.id)))
            event.dispatch_status = "dispatched"
            await session.flush()
        return True
