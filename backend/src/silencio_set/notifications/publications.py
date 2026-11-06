"""Create private notices from committed plan and call sheet events."""
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import func, select

from silencio_set.accounts.models import User
from silencio_set.call_sheets.models import CallSheetEvent, PublishedRecipient
from silencio_set.crews.models import CrewMember
from silencio_set.notifications.models import Notification, NotificationRecipient
from silencio_set.notifications.queries import PLAN_READ, SHEET_READ
from silencio_set.plans.models import PlanPublicationEvent, PlanVersion
from silencio_set.tasks.repository import permissions_for

SHEET_TITLES = {'published':'每日通告已发布', 'review_required':'通告来源有变化，请重新核对',
                'withdrawn':'拍摄日已取消，原通告已撤回', 'superseded':'原通告已被新版替代'}


def identity(person):
    return UUID(person['member_id']), datetime.fromisoformat(person['membership_joined_at'].replace('Z','+00:00'))


async def consume_publication(session, crew_id, event_id, kind):
    # Caller holds the crew coordinator before acquiring this event lock.
    model = PlanPublicationEvent if kind == 'plan' else CallSheetEvent
    event = await session.scalar(select(model).where(model.crew_id == crew_id, model.id == event_id,
        model.consumed_at.is_(None)).with_for_update(skip_locked=True))
    if event is None: return False
    candidates = {identity(person) for person in event.recipient_snapshots}
    members = (await session.scalars(select(CrewMember).join(User, User.id == CrewMember.user_id).where(
        CrewMember.crew_id == crew_id, CrewMember.id.in_({member for member,_ in candidates}),
        CrewMember.status == 'active', User.status == 'active'))).all()
    permissions = await permissions_for(session, members)
    source_permissions = PLAN_READ if kind == 'plan' else SHEET_READ
    members = [member for member in members if (member.id,member.joined_at) in candidates
               and 'notification.read_self' in permissions[member.id] and source_permissions & permissions[member.id]]
    if kind == 'plan':
        version = await session.scalar(select(PlanVersion).where(PlanVersion.crew_id == crew_id, PlanVersion.id == event.plan_version_id))
        required = set() if event.kind == 'withdrawn' else {identity(person) for item in version.publication_snapshot['items'] for person in item['members']}
        source = dict(plan_event_id=event.id, source_event_id=None, call_sheet_event_id=None)
        title, event_type, summary = '正式拍摄排期已发布', 'plan.published', '拍摄安排有更新，请核对本人当前安排；不再参与新版的成员请联系制片确认调整。'
        if event.kind == 'withdrawn':
            title, event_type, summary = '正式拍摄排期已撤回', 'plan.withdrawn', '原拍摄安排已取消，请停止依据原排期及关联通告准备，后续安排以新发布版本为准。'
    else:
        recipients = (await session.scalars(select(PublishedRecipient).where(PublishedRecipient.crew_id == crew_id,
            PublishedRecipient.call_sheet_version_id == event.call_sheet_version_id))).all()
        published = {(row.member_id,row.membership_joined_at) for row in recipients}
        members = [member for member in members if (member.id,member.joined_at) in published]
        required = published if event.kind == 'published' else set()
        source = dict(call_sheet_event_id=event.id, source_event_id=None, plan_event_id=None)
        title, event_type = SHEET_TITLES[event.kind], f'call_sheet.{event.kind}'
        summary = '请查看通告并核对集合与拍摄安排。' if event.kind == 'published' else '原通告状态已变化，请核对最新通告；待复核、撤回或被替代的通告不能作为有效拍摄安排。'
    notice = Notification(id=uuid4(), crew_id=crew_id, **source, event_key=f'{kind}:{event.id}',
        event_type=event_type, title=title, summary=summary, created_at=event.created_at)
    session.add(notice)
    await session.flush()
    session.add_all([NotificationRecipient(crew_id=crew_id, notification_id=notice.id, member_id=member.id,
        membership_joined_at=member.joined_at, requires_confirmation=(member.id,member.joined_at) in required) for member in members])
    event.consumed_at = await session.scalar(select(func.clock_timestamp()))
    await session.flush()
    return True
