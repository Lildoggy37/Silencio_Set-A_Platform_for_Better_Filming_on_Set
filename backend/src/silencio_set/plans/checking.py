from collections import defaultdict
from datetime import timedelta

from sqlalchemy import select, or_, text

from silencio_set.accounts.models import User
from silencio_set.availability.models import AvailabilitySet, MemberAvailability
from silencio_set.creative.models import Breakdown, SceneRevision, SceneResourceNeed
from silencio_set.crews.models import CrewMember
from silencio_set.plans.domain import check_plan, extend_cross_plan
from silencio_set.plans.casting import collect_casting, extend_casting
from silencio_set.creative.source_evidence import current_reviews
from silencio_set.plans.models import ShootingPlan, ShootingSession, SessionMember, SessionResource
from silencio_set.resources.custody_models import Checkout
from silencio_set.resources.models import Resource, Reservation
from silencio_set.scripts.models import Script, ScriptVersion
from silencio_set.plans.repository import rows_by_ids, session_items, scoped


def states(mapping, *fields):
    return [{field: getattr(mapping[key], field) for field in fields} for key in sorted(mapping, key=str)]


async def current_other_items(session, version, items, resources, now):
    if not items:
        return []
    start = min(item['starts_at'] for item in items)
    end = max(item['ends_at'] + timedelta(minutes=item['transition_minutes']) for item in items)
    overlap = (ShootingSession.starts_at < end) & (ShootingSession.ends_at + ShootingSession.transition_minutes * text("INTERVAL '1 minute'") > start)
    consumables = {row.id for row in resources.values() if row.resource_type == 'consumable'}
    if consumables:
        future_stock = (ShootingSession.ends_at > now) & select(SessionResource.session_id).where(
            SessionResource.session_id == ShootingSession.id, SessionResource.resource_id.in_(consumables)).exists()
        overlap = or_(overlap, future_stock)
    rows = (await session.scalars(select(ShootingSession).join(ShootingPlan,
        ShootingPlan.current_published_version_id == ShootingSession.plan_version_id).where(
        ShootingPlan.crew_id == version.crew_id, ShootingPlan.id != version.plan_id, overlap,
    ).order_by(ShootingSession.id))).all()
    if not rows:
        return []
    ids = {row.id for row in rows}
    people = (await session.scalars(select(SessionMember).where(SessionMember.crew_id == version.crew_id, SessionMember.session_id.in_(ids)).order_by(SessionMember.session_id, SessionMember.member_id))).all()
    assets = (await session.scalars(select(SessionResource).where(SessionResource.crew_id == version.crew_id, SessionResource.session_id.in_(ids)).order_by(SessionResource.session_id, SessionResource.resource_id))).all()
    return [{
        'id': row.id, 'plan_version_id': row.plan_version_id, 'starts_at': row.starts_at, 'ends_at': row.ends_at,
        'transition_minutes': row.transition_minutes, 'location_resource_id': row.location_resource_id, 'location_reservation_id': row.location_reservation_id,
        'members': [{'member_id': link.member_id, 'membership_joined_at': link.membership_joined_at} for link in people if link.session_id == row.id],
        'resources': [{'resource_id': link.resource_id, 'reservation_id': link.reservation_id, 'quantity': link.quantity} for link in assets if link.session_id == row.id],
    } for row in rows]


async def collect_check(session, version, now):
    crew_id = version.crew_id
    items = await session_items(session, version)
    scenes = await rows_by_ids(session, SceneRevision, crew_id, {item['scene_revision_id'] for item in items})
    breakdowns = await rows_by_ids(session, Breakdown, crew_id, {row.breakdown_id for row in scenes.values()})
    script_versions = await rows_by_ids(session, ScriptVersion, crew_id, {row.script_version_id for row in scenes.values()})
    scripts = await rows_by_ids(session, Script, crew_id, {row.script_id for row in script_versions.values()})
    sources = {row.id: {'status': breakdowns[row.breakdown_id].review_status, 'script_version_id': row.script_version_id, 'current_version_id': scripts[script_versions[row.script_version_id].script_id].current_version_id} for row in scenes.values()}
    reviews = await current_reviews(session, crew_id, 'breakdowns', {row.id:(row, row.script_version_id,
        scripts[script_versions[row.script_version_id].script_id]) for row in breakdowns.values()})
    for row in scenes.values():
        review = reviews.get(row.breakdown_id)
        sources[row.id].update(source_review_id=review.id if review else None,
            source_review_applicable=review is not None and review.conclusion == 'still_applicable')
    people = await rows_by_ids(session, CrewMember, crew_id, {link['member_id'] for item in items for link in item['members']})
    enabled_users = set((await session.scalars(select(User.id).where(User.id.in_({row.user_id for row in people.values()}), User.status == 'active'))).all()) if people else set()
    member_states = {row.id: {'status': row.status if row.user_id in enabled_users else 'disabled', 'joined_at': row.joined_at} for row in people.values()}
    resources = await rows_by_ids(session, Resource, crew_id, {link['resource_id'] for item in items for link in item['resources']} | {item['location_resource_id'] for item in items if item['location_resource_id']})
    bookings = await rows_by_ids(session, Reservation, crew_id, {link['reservation_id'] for item in items for link in item['resources'] if link['reservation_id']} | {item['location_reservation_id'] for item in items if item['location_reservation_id']})
    calendars, needs = defaultdict(list), defaultdict(list)
    calendar_heads, calendar_rows = {}, {}
    if people and items:
        start = min(item['starts_at'] for item in items)
        end = max(item['ends_at'] + timedelta(minutes=item['transition_minutes']) for item in items)
        heads = (await session.scalars(select(AvailabilitySet).where(AvailabilitySet.crew_id == crew_id, AvailabilitySet.member_id.in_(people)))).all()
        calendar_heads = {row.member_id: row for row in heads}
        entries = (await session.scalars(select(MemberAvailability).where(MemberAvailability.crew_id == crew_id,
            MemberAvailability.member_id.in_(people), MemberAvailability.starts_at < end, MemberAvailability.ends_at > start).order_by(MemberAvailability.member_id, MemberAvailability.starts_at, MemberAvailability.id))).all()
        calendar_rows = {row.id: row for row in entries}
        for entry in entries:
            if entry.member_id in calendar_heads and calendar_heads[entry.member_id].membership_joined_at == people[entry.member_id].joined_at:
                calendars[entry.member_id].append(entry)
    if scenes:
        for need in (await session.scalars(select(SceneResourceNeed).where(SceneResourceNeed.crew_id == crew_id, SceneResourceNeed.scene_revision_id.in_(scenes)).order_by(SceneResourceNeed.id))).all():
            needs[need.scene_revision_id].append(need)
    report = check_plan(items, sources=sources, members=member_states, resources=resources, reservations=bookings, calendars=calendars, scene_needs=needs, lock_version=version.lock_version, now=now)
    castings = await collect_casting(session, crew_id, scenes, script_versions)
    extend_casting(report, items, castings)
    others = await current_other_items(session, version, items, resources, now)
    checkouts = (await session.scalars(select(Checkout).where(Checkout.crew_id == crew_id, Checkout.resource_id.in_(resources), Checkout.quantity > Checkout.returned_quantity).order_by(Checkout.id))).all() if resources else []
    extend_cross_plan(report, items, others, resources, bookings, checkouts, now)
    plan = await scoped(session, ShootingPlan, crew_id, version.plan_id)
    snapshot = {
        'plan_id': version.plan_id, 'version_id': version.id, 'status': version.status, 'lock_version': version.lock_version,
        'current_published_version_id': plan.current_published_version_id, 'items': items,
        'sources': [{'id': key, **sources[key]} for key in sorted(sources, key=str)],
        'scripts': states(scripts, 'id', 'current_version_id', 'lock_version', 'working_revision'),
        'members': [{'id': key, **member_states[key]} for key in sorted(member_states, key=str)],
        'resources': states(resources, 'id', 'resource_type', 'status', 'unit', 'total_quantity', 'stock_quantity', 'lock_version'),
        'bookings': states(bookings, 'id', 'resource_id', 'status', 'starts_at', 'ends_at', 'quantity', 'lock_version'),
        'calendar_heads': states(calendar_heads, 'member_id', 'membership_joined_at', 'lock_version'),
        'calendar_rows': states(calendar_rows, 'id', 'member_id', 'starts_at', 'ends_at', 'availability'),
        'other_items': others,
        'castings': castings,
        'scene_needs': states({row.id: row for values in needs.values() for row in values}, 'id', 'scene_revision_id', 'resource_id', 'resource_type', 'name', 'quantity', 'unit'),
        'checkouts': states({row.id: row for row in checkouts}, 'id', 'reservation_id', 'quantity', 'returned_quantity', 'expected_return_at', 'lock_version'),
        'report': report.model_dump(mode='json'),
    }
    return report, snapshot
