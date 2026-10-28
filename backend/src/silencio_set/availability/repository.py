from uuid import uuid4

from sqlalchemy import and_, delete, func, select

from silencio_set.accounts.errors import AuthError
from silencio_set.accounts.models import User
from silencio_set.availability.models import AvailabilitySet, MemberAvailability
from silencio_set.availability.schemas import AvailabilityInterval, AvailabilityPage, AvailabilityView, TimeWindow
from silencio_set.crews.models import AuditLog, CrewMember
from silencio_set.crews.organization import MAX_VERSION
from silencio_set.crews.repository import not_found
from silencio_set.tasks.repository import require_permission, task_actor

MAX_STORED_INTERVALS = 2000


def replacement_fragments(existing, body):
    """Only overlapping rows are replaced; retain each portion outside [a,b)."""
    result = list(body.intervals)
    for row in existing:
        if row.starts_at < body.range_start:
            result.append(AvailabilityInterval(starts_at=row.starts_at, ends_at=body.range_start, availability=row.availability))
        if row.ends_at > body.range_end:
            result.append(AvailabilityInterval(starts_at=body.range_end, ends_at=row.ends_at, availability=row.availability))
    return sorted(result, key=lambda item: item.starts_at)


def calendar_view(member, collection, rows, window):
    current = collection is not None and collection.membership_joined_at == member.joined_at
    intervals = [AvailabilityInterval(starts_at=max(row.starts_at, window.range_start), ends_at=min(row.ends_at, window.range_end),
        availability=row.availability) for row in rows] if current else []
    intervals.sort(key=lambda item: item.starts_at)
    cursor = window.range_start; unregistered = []
    for interval in intervals:
        if interval.starts_at > cursor: unregistered.append(TimeWindow(starts_at=cursor, ends_at=interval.starts_at))
        cursor = interval.ends_at
    if cursor < window.range_end: unregistered.append(TimeWindow(starts_at=cursor, ends_at=window.range_end))
    return AvailabilityView(member_id=member.id, display_name=member.display_name, membership_joined_at=member.joined_at,
        lock_version=collection.lock_version if collection else 1, updated_at=collection.updated_at if current else None,
        range_start=window.range_start, range_end=window.range_end, intervals=intervals, unregistered=unregistered)


def calendar_query(crew_id, member_ids, window):
    # Collection head and interval content come from the same SELECT snapshot.
    # Old membership data is not exposed after a leave/rejoin cycle.
    return select(CrewMember, AvailabilitySet, MemberAvailability).join(User, User.id == CrewMember.user_id).outerjoin(
        AvailabilitySet, (AvailabilitySet.crew_id == CrewMember.crew_id) & (AvailabilitySet.member_id == CrewMember.id)
    ).outerjoin(MemberAvailability, and_(MemberAvailability.crew_id == AvailabilitySet.crew_id,
        MemberAvailability.member_id == AvailabilitySet.member_id, AvailabilitySet.membership_joined_at == CrewMember.joined_at,
        MemberAvailability.starts_at < window.range_end, MemberAvailability.ends_at > window.range_start)
    ).where(CrewMember.crew_id == crew_id, CrewMember.id.in_(member_ids), CrewMember.status == "active", User.status == "active").order_by(
        CrewMember.joined_at, CrewMember.id, MemberAvailability.starts_at, MemberAvailability.id).execution_options(populate_existing=True)


async def read_calendars(session, crew_id, member_ids, window, limit):
    rows = (await session.execute(calendar_query(crew_id, member_ids, window).limit(limit + 1))).all()
    if len(rows) > limit: raise AuthError(409, "AVAILABILITY_WINDOW_TOO_DENSE", "登记过多，请缩小查询范围或选择单个成员")
    grouped = {}
    for member, collection, interval in rows:
        if member.id not in grouped: grouped[member.id] = [member, collection, []]
        if interval is not None: grouped[member.id][2].append(interval)
    return [calendar_view(member, collection, intervals, window) for member, collection, intervals in grouped.values()]


class AvailabilityRepository:
    def __init__(self, sessions):
        self.sessions = sessions

    async def own(self, user_id, crew_id, window):
        async with self.sessions() as session:
            actor = await task_actor(session, user_id, crew_id)
            require_permission(actor, "availability.self")
            items = await read_calendars(session, crew_id, [actor.member_id], window, 500)
            if not items: raise not_found()
            return items[0]

    async def list(self, user_id, crew_id, window, page, page_size, *, member_id=None):
        async with self.sessions() as session:
            actor = await task_actor(session, user_id, crew_id)
            require_permission(actor, "availability.read_all")
            query = select(CrewMember.id).join(User, User.id == CrewMember.user_id).where(
                CrewMember.crew_id == crew_id, CrewMember.status == "active", User.status == "active")
            if member_id is not None: query = query.where(CrewMember.id == member_id)
            total = await session.scalar(select(func.count()).select_from(query.subquery()))
            ids = (await session.scalars(query.order_by(CrewMember.joined_at, CrewMember.id).offset((page - 1) * page_size).limit(page_size))).all()
            items = await read_calendars(session, crew_id, ids, window, 1000) if ids else []
            return AvailabilityPage(items=items, total=total, page=page, page_size=page_size)

    async def replace(self, user_id, crew_id, body, request_id):
        async with self.sessions() as session, session.begin():
            actor = await task_actor(session, user_id, crew_id, writing=True)
            require_permission(actor, "availability.self")
            member = await session.scalar(select(CrewMember).where(CrewMember.crew_id == crew_id,
                CrewMember.id == actor.member_id, CrewMember.status == "active").with_for_update())
            if member is None: raise not_found()
            if member.joined_at != body.expected_membership_joined_at:
                raise AuthError(409, "MEMBERSHIP_CHANGED", "入组状态已变化，请重新读取本人档期")
            collection = await session.scalar(select(AvailabilitySet).where(AvailabilitySet.crew_id == crew_id,
                AvailabilitySet.member_id == member.id).with_for_update())
            version = collection.lock_version if collection else 1
            if version != body.expected_lock_version: raise AuthError(409, "VERSION_CONFLICT", "档期已变化，请重新读取后确认")
            if version >= MAX_VERSION: raise AuthError(409, "VERSION_EXHAUSTED", "档期版本达到上限")
            now = await session.scalar(select(func.clock_timestamp()))
            fresh_epoch = collection is None or collection.membership_joined_at != member.joined_at
            overlap = (MemberAvailability.crew_id == crew_id) & (MemberAvailability.member_id == member.id)
            overlap = overlap & (MemberAvailability.starts_at < body.range_end) & (MemberAvailability.ends_at > body.range_start)
            existing = (await session.scalars(select(MemberAvailability).where(overlap).order_by(MemberAvailability.starts_at))).all() if not fresh_epoch else []
            fragments = replacement_fragments(existing, body)
            total = await session.scalar(select(func.count()).select_from(MemberAvailability).where(
                MemberAvailability.crew_id == crew_id, MemberAvailability.member_id == member.id)) if not fresh_epoch else 0
            if total - len(existing) + len(fragments) > MAX_STORED_INTERVALS:
                raise AuthError(409, "AVAILABILITY_CAPACITY", "登记条目达到上限，请先合并或清理已有登记")
            if collection is None:
                collection = AvailabilitySet(crew_id=crew_id, member_id=member.id, membership_joined_at=member.joined_at, lock_version=1, updated_at=now)
                session.add(collection)
                await session.flush()
            # A fresh membership explicitly starts a new calendar. The global
            # collection version never resets; the epoch rejects stale pages.
            deletion = (MemberAvailability.crew_id == crew_id) & (MemberAvailability.member_id == member.id) if fresh_epoch else overlap
            await session.execute(delete(MemberAvailability).where(deletion))
            for interval in fragments:
                session.add(MemberAvailability(id=uuid4(), crew_id=crew_id, member_id=member.id,
                    **interval.model_dump(), lock_version=1, created_at=now, updated_at=now))
            collection.membership_joined_at = member.joined_at
            collection.lock_version = version + 1; collection.updated_at = now
            session.add(AuditLog(crew_id=crew_id, actor_type="user", actor_id=user_id, action="availability.replace",
                target_type="crew_member", target_id=member.id, result="success", request_id=request_id,
                before_summary={"lock_version": version, "replaced_intervals": len(existing), "membership_reset": fresh_epoch},
                after_summary={"lock_version": collection.lock_version, "inserted_intervals": len(fragments),
                    "range_start": body.range_start.isoformat(), "range_end": body.range_end.isoformat()}))
            await session.flush()
            result = calendar_view(member, collection, body.intervals, body)
        return result
