"""Read all notification sources in one query, applying current source access."""
from sqlalchemy import and_, case, false, func, literal, or_, select, true
from sqlalchemy.orm import Bundle, aliased

from silencio_set.call_sheets.models import CallSheet, CallSheetEvent, CallSheetVersion, PublishedRecipient
from silencio_set.crews.models import CrewMember
from silencio_set.notifications.models import Notification, NotificationRecipient
from silencio_set.notifications.schemas import NotificationView
from silencio_set.plans.models import PlanPublicationEvent, PlanVersion, ShootingPlan, ShootingSession, SessionMember
from silencio_set.tasks.models import Task, TaskChangeEvent
from silencio_set.tasks.repository import require_permission, visible_tasks

CURRENT_PLAN = aliased(PlanVersion, name='current_notification_plan')
SHEET_SOURCE = aliased(PlanVersion, name='notification_sheet_source')
SHEET_PLAN = aliased(ShootingPlan, name='notification_sheet_plan')
SOURCE_FIELDS = ('source_type', 'source_id', 'object_title', 'object_version', 'current_object_version',
                 'is_current', 'source_status', 'can_open_source')
PLAN_READ = frozenset({'plan.read_all', 'plan.edit', 'plan.read_related'})
SHEET_FULL = frozenset({'call_sheet.edit', 'call_sheet.review', 'call_sheet.publish'})
SHEET_READ = SHEET_FULL | {'call_sheet.read_related'}


def current_condition():
    return or_(
        and_(Notification.source_event_id.is_not(None), TaskChangeEvent.task_version == Task.lock_version),
        and_(Notification.plan_event_id.is_not(None), PlanPublicationEvent.kind == 'published', ShootingPlan.current_published_version_id == PlanVersion.id,
             func.jsonb_array_length(PlanVersion.review_reasons) == 0),
        and_(Notification.call_sheet_event_id.is_not(None), CallSheetEvent.kind == 'published',
             CallSheetEvent.object_lock_version == CallSheetVersion.lock_version,
             CallSheet.current_published_version_id == CallSheetVersion.id, CallSheetVersion.validity_status == 'valid',
             SHEET_PLAN.current_published_version_id == SHEET_SOURCE.id, func.jsonb_array_length(SHEET_SOURCE.review_reasons) == 0))


def notification_query(crew_id, actor):
    require_permission(actor, 'notification.read_self')
    task_visible = Task.id.in_(visible_tasks(crew_id, actor).with_only_columns(Task.id).correlate(None)) if {'task.read', 'task.manage'} & actor.permissions else false()
    plan_allowed = bool(PLAN_READ & actor.permissions)
    # A removed participant still receives a change notice, but cannot open a
    # new plan version without a current relationship or full plan permission.
    participant = select(SessionMember.member_id).join(ShootingSession, ShootingSession.id == SessionMember.session_id).where(
        ShootingSession.crew_id == crew_id, ShootingSession.plan_version_id == PlanVersion.id,
        SessionMember.member_id == actor.member_id, SessionMember.membership_joined_at == CrewMember.joined_at).correlate(PlanVersion, CrewMember).exists()
    plan_open = true() if {'plan.read_all', 'plan.edit'} & actor.permissions else and_(
        ShootingPlan.current_published_version_id == PlanVersion.id, participant)
    received_sheet = select(PublishedRecipient.member_id).where(PublishedRecipient.crew_id == crew_id,
        PublishedRecipient.call_sheet_version_id == CallSheetVersion.id, PublishedRecipient.member_id == actor.member_id,
        PublishedRecipient.membership_joined_at == CrewMember.joined_at).correlate(CallSheetVersion, CrewMember).exists()
    sheet_allowed = true() if SHEET_FULL & actor.permissions else received_sheet if 'call_sheet.read_related' in actor.permissions else false()
    task_source = Notification.source_event_id.is_not(None)
    plan_source = Notification.plan_event_id.is_not(None)
    source = Bundle('source',
        case((task_source, 'task'), (plan_source, 'plan'), else_='call_sheet').label('source_type'),
        case((task_source, Task.id), (plan_source, PlanVersion.id), else_=CallSheetVersion.id).label('source_id'),
        case((task_source, Task.title), (plan_source, case((plan_open, ShootingPlan.name), else_='拍摄安排')),
             else_=CallSheetVersion.arrangement_snapshot['shooting_date'].astext + literal(' 通告')).label('object_title'),
        case((task_source, TaskChangeEvent.task_version), (plan_source, PlanVersion.version_no), else_=CallSheetEvent.object_lock_version).label('object_version'),
        case((task_source, Task.lock_version), (plan_source, CURRENT_PLAN.version_no), else_=CallSheetVersion.lock_version).label('current_object_version'),
        func.coalesce(current_condition(), false()).label('is_current'),
        case((task_source, case((TaskChangeEvent.task_version == Task.lock_version, 'current'), else_='updated')),
             (plan_source, case((PlanVersion.withdrawn_at.is_not(None), 'withdrawn'),
                                (ShootingPlan.current_published_version_id.is_distinct_from(PlanVersion.id), 'updated'),
                                (func.jsonb_array_length(PlanVersion.review_reasons) > 0, 'review_required'), else_='current')),
             else_=case((CallSheetVersion.validity_status == 'valid', 'current'), else_=CallSheetVersion.validity_status)).label('source_status'),
        case((task_source, true()), (plan_source, plan_open), else_=sheet_allowed).label('can_open_source'))
    return select(Notification, NotificationRecipient, source).join(NotificationRecipient,
        and_(NotificationRecipient.crew_id == Notification.crew_id, NotificationRecipient.notification_id == Notification.id)
    ).join(CrewMember, and_(CrewMember.crew_id == NotificationRecipient.crew_id, CrewMember.id == NotificationRecipient.member_id)
    ).outerjoin(TaskChangeEvent, and_(TaskChangeEvent.crew_id == Notification.crew_id, TaskChangeEvent.id == Notification.source_event_id)
    ).outerjoin(Task, and_(Task.crew_id == Notification.crew_id, Task.id == TaskChangeEvent.task_id)
    ).outerjoin(PlanPublicationEvent, and_(PlanPublicationEvent.crew_id == Notification.crew_id, PlanPublicationEvent.id == Notification.plan_event_id)
    ).outerjoin(PlanVersion, and_(PlanVersion.crew_id == Notification.crew_id, PlanVersion.id == PlanPublicationEvent.plan_version_id)
    ).outerjoin(ShootingPlan, and_(ShootingPlan.crew_id == Notification.crew_id, ShootingPlan.id == PlanVersion.plan_id)
    ).outerjoin(CURRENT_PLAN, and_(CURRENT_PLAN.crew_id == Notification.crew_id, CURRENT_PLAN.id == ShootingPlan.current_published_version_id)
    ).outerjoin(CallSheetEvent, and_(CallSheetEvent.crew_id == Notification.crew_id, CallSheetEvent.id == Notification.call_sheet_event_id)
    ).outerjoin(CallSheetVersion, and_(CallSheetVersion.crew_id == Notification.crew_id, CallSheetVersion.id == CallSheetEvent.call_sheet_version_id)
    ).outerjoin(CallSheet, and_(CallSheet.crew_id == Notification.crew_id, CallSheet.id == CallSheetVersion.call_sheet_id)
    ).outerjoin(SHEET_SOURCE, and_(SHEET_SOURCE.crew_id == Notification.crew_id, SHEET_SOURCE.id == CallSheetVersion.source_plan_version_id)
    ).outerjoin(SHEET_PLAN, and_(SHEET_PLAN.crew_id == Notification.crew_id, SHEET_PLAN.id == CallSheetVersion.plan_id)).where(
        Notification.crew_id == crew_id, NotificationRecipient.member_id == actor.member_id,
        CrewMember.status == 'active', NotificationRecipient.membership_joined_at == CrewMember.joined_at,
        or_(and_(task_source, task_visible), and_(plan_source, plan_allowed),
            and_(Notification.call_sheet_event_id.is_not(None), sheet_allowed)))


def view(row):
    notice, recipient, source = row
    return NotificationView(id=notice.id, event_type=notice.event_type, title=notice.title, summary=notice.summary,
        created_at=notice.created_at, **{name:getattr(source, name) for name in SOURCE_FIELDS},
        requires_confirmation=recipient.requires_confirmation, read_at=recipient.read_at, confirmed_at=recipient.confirmed_at)
