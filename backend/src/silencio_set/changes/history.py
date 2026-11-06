from sqlalchemy import func, select
from silencio_set.changes.models import ImpactCheck, ImpactIssue, ImpactTransition
from silencio_set.changes.repository import authorize_category
from silencio_set.changes.schemas import RecordedIssue, RecordedIssuePage, ImpactCheckView, ImpactTransitionView, ImpactHistoryPage
from silencio_set.crews.repository import not_found
from silencio_set.resources.repository import resource_actor


def issue_view(row):
    return RecordedIssue(id=row.id,target=row.target_snapshot,status=row.status,first_detected_at=row.first_detected_at,
        last_checked_at=row.last_checked_at,resolved_at=row.resolved_at,reopened_count=row.reopened_count,
        lock_version=row.lock_version,resolution_reason=row.resolution_basis['reason'] if row.resolution_basis else None)


def event_view(row):
    after=row.after_state
    return ImpactTransitionView(id=row.id,check_id=row.check_id,kind=row.kind,recorded_at=row.recorded_at,
        before_status=row.before_state['status'] if row.before_state else None,after_status=after['status'],
        target=after['target'],evidence=after['evidence'],
        resolution_reason=after['resolution_basis']['reason'] if after['resolution_basis'] else None)


async def records(repository,user_id,crew_id,*,category,status='all',page=1,page_size=20):
    async with repository.sessions() as session,session.begin():
        actor=await resource_actor(session,user_id,crew_id,writing=True)
        authorize_category(actor,category)
        latest=await session.scalar(select(ImpactCheck).where(ImpactCheck.crew_id==crew_id,ImpactCheck.category==category)
            .order_by(ImpactCheck.checked_at.desc(),ImpactCheck.id.desc()).limit(1))
        query=select(ImpactIssue).where(ImpactIssue.crew_id==crew_id,ImpactIssue.category==category)
        if status!='all': query=query.where(ImpactIssue.status==status)
        total=await session.scalar(select(func.count()).select_from(query.subquery()))
        rows=(await session.scalars(query.order_by(ImpactIssue.last_checked_at.desc(),ImpactIssue.id).offset((page-1)*page_size).limit(page_size))).all()
        return RecordedIssuePage(items=[issue_view(row) for row in rows],latest_check=ImpactCheckView.model_validate(latest) if latest else None,
            total=total,page=page,page_size=page_size)


async def history(repository,user_id,crew_id,*,category,issue_id,page=1,page_size=20):
    async with repository.sessions() as session,session.begin():
        actor=await resource_actor(session,user_id,crew_id,writing=True)
        authorize_category(actor,category)
        issue=await session.scalar(select(ImpactIssue).where(ImpactIssue.crew_id==crew_id,ImpactIssue.category==category,ImpactIssue.id==issue_id))
        if issue is None: raise not_found()
        query=select(ImpactTransition).where(ImpactTransition.crew_id==crew_id,ImpactTransition.issue_id==issue.id)
        total=await session.scalar(select(func.count()).select_from(query.subquery()))
        rows=(await session.scalars(query.order_by(ImpactTransition.recorded_at.desc(),ImpactTransition.id.desc()).offset((page-1)*page_size).limit(page_size))).all()
        return ImpactHistoryPage(issue=issue_view(issue),items=[event_view(row) for row in rows],total=total,page=page,page_size=page_size)
