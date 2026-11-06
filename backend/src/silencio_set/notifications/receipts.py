"""Read-only publication receipts. A generated inbox item is not proof of delivery."""
from sqlalchemy import select, func

from silencio_set.accounts.errors import AuthError
from silencio_set.accounts.models import User
from silencio_set.call_sheets.models import CallSheet, CallSheetVersion, CallSheetEvent, PublishedRecipient
from silencio_set.crews.models import CrewMember
from silencio_set.notifications.models import Notification, NotificationRecipient
from silencio_set.notifications.publications import identity
from silencio_set.notifications.queries import SHEET_FULL
from silencio_set.notifications.receipt_schemas import ReceiptCounts, ReceiptItem, ReceiptPage
from silencio_set.plans.models import ShootingPlan, PlanVersion, PlanPublicationEvent
from silencio_set.plans.repository import scoped
from silencio_set.resources.repository import resource_actor, require


def authorize(actor, kind):
    require(actor, 'member.read')
    full = {'plan.read_all', 'plan.edit'} if kind == 'plan' else SHEET_FULL
    publisher = 'plan.publish' if kind == 'plan' else 'call_sheet.publish'
    if not full & actor.permissions or not {publisher, 'notification.receipts'} & actor.permissions:
        raise AuthError(403, 'FORBIDDEN', '当前没有查看该安排全部回执的权限')


def plan_status(plan, version):
    if version.withdrawn_at is not None: return 'withdrawn'
    if plan.current_published_version_id != version.id: return 'updated'
    return 'review_required' if version.review_reasons else 'current'


def names_from_plan(version):
    return {identity(person): person['display_name'] for item in version.publication_snapshot['items'] for person in item['members']}


def build_page(*, kind, version, source_status, event, names, required, receipts, people, now, page, page_size):
    intended = sorted({identity(person) for person in event.recipient_snapshots}, key=lambda key: (names[key], str(key[0]), key[1]))
    intended_set = set(intended)
    recorded = {(row.member_id, row.membership_joined_at): row for row in receipts}
    recorded = {key: row for key, row in recorded.items() if key in intended_set}
    count = len(recorded)
    counts = ReceiptCounts(intended=len(intended), generated=count,
        pending_dispatch=len(intended) - count if event.consumed_at is None else 0,
        skipped=len(intended) - count if event.consumed_at is not None else 0,
        read=sum(row.read_at is not None for row in recorded.values()),
        confirmed=sum(row.confirmed_at is not None for row in recorded.values()),
        unconfirmed=sum(row.requires_confirmation and row.confirmed_at is None for row in recorded.values()))
    items = []
    for key in intended[(page - 1) * page_size:page * page_size]:
        member, user_status = people[key[0]]
        identity_status = 'rejoined' if member.joined_at != key[1] else 'left' if member.status != 'active' else 'disabled' if user_status != 'active' else 'active'
        row = recorded.get(key)
        items.append(ReceiptItem(member_id=key[0], membership_joined_at=key[1], display_name=names[key], identity_status=identity_status,
            notification_status='generated' if row else 'pending_dispatch' if event.consumed_at is None else 'skipped',
            requires_confirmation=row.requires_confirmation if row else key in required,
            read_at=row.read_at if row else None, confirmed_at=row.confirmed_at if row else None))
    return ReceiptPage(source_type=kind, source_id=version.id, version_no=version.version_no, source_status=source_status,
        observed_at=now, dispatch_status='pending_dispatch' if event.consumed_at is None else 'processed',
        counts=counts, items=items, total=len(intended), page=page, page_size=page_size)


async def read(repository, user_id, crew_id, *, kind, version_id, page=1, page_size=20):
    async with repository.sessions() as session, session.begin():
        actor = await resource_actor(session, user_id, crew_id, writing=True)
        authorize(actor, kind)
        model = PlanVersion if kind == 'plan' else CallSheetVersion
        version = await scoped(session, model, crew_id, version_id)
        if version.status != 'published':
            raise AuthError(409, 'NOT_PUBLISHED', '该版本尚未发布，没有发布通知回执')
        event_model = PlanPublicationEvent if kind == 'plan' else CallSheetEvent
        source_field = event_model.plan_version_id if kind == 'plan' else event_model.call_sheet_version_id
        event = await session.scalar(select(event_model).where(event_model.crew_id == crew_id,
            source_field == version.id, event_model.kind == 'published'))
        if kind == 'plan':
            plan = await scoped(session, ShootingPlan, crew_id, version.plan_id)
            status = plan_status(plan, version)
            names = {}
            if event.previous_version_id is not None:
                names.update(names_from_plan(await scoped(session, PlanVersion, crew_id, event.previous_version_id)))
            names.update(names_from_plan(version))
            required = set(names_from_plan(version))
            event_filter = Notification.plan_event_id == event.id
        else:
            sheet = await scoped(session, CallSheet, crew_id, version.call_sheet_id)
            source = await scoped(session, PlanVersion, crew_id, version.source_plan_version_id)
            plan = await scoped(session, ShootingPlan, crew_id, version.plan_id)
            status = version.validity_status
            if status == 'valid':
                status = 'superseded' if sheet.current_published_version_id != version.id else 'current' if plan_status(plan, source) == 'current' else 'review_required'
            rows = (await session.scalars(select(PublishedRecipient).where(PublishedRecipient.crew_id == crew_id,
                PublishedRecipient.call_sheet_version_id == version.id))).all()
            names = {(row.member_id, row.membership_joined_at): row.display_name for row in rows}
            required = set(names)
            event_filter = Notification.call_sheet_event_id == event.id
        receipts = (await session.scalars(select(NotificationRecipient).join(Notification,
            (Notification.id == NotificationRecipient.notification_id) & (Notification.crew_id == NotificationRecipient.crew_id)).where(
            Notification.crew_id == crew_id, event_filter))).all()
        intended = sorted({identity(person) for person in event.recipient_snapshots}, key=lambda key: (names[key], str(key[0]), key[1]))
        ids = {key[0] for key in intended[(page - 1) * page_size:page * page_size]}
        people = (await session.execute(select(CrewMember, User.status).join(User, User.id == CrewMember.user_id).where(
            CrewMember.crew_id == crew_id, CrewMember.id.in_(ids)))).all() if ids else []
        now = await session.scalar(select(func.clock_timestamp()))
        return build_page(kind=kind, version=version, source_status=status, event=event, names=names, required=required,
            receipts=receipts, people={member.id:(member, user_status) for member, user_status in people}, now=now, page=page, page_size=page_size)
