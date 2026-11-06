from sqlalchemy import func, select

from silencio_set.accounts.errors import AuthError
from silencio_set.changes.queries import allowed_categories, issue_query
from silencio_set.changes.schemas import ChangeIssue, ChangeIssuePage
from silencio_set.creative.source_evidence import assessment_status, current_reviews
from silencio_set.resources.repository import resource_actor


def authorize_category(actor, category):
    allowed = allowed_categories(actor.permissions)
    if category not in allowed:
        raise AuthError(403, 'FORBIDDEN', '当前没有查看该类别变更问题的权限')
    return allowed


def creative_item(category, target, fixed, script, review):
    status = assessment_status(fixed.id, script, review)
    name = '场戏拆解' if category == 'breakdowns' else '文字分镜'
    return ChangeIssue(id=target.id, category=category, parent_id=script.id,
        source_id=target.script_version_id if category == 'breakdowns' else target.scene_revision_id,
        title=f'{script.title} · {name}第{target.version_no}版', status=status,
        reason_codes=[{'no_working_version':'working_version_missing', 'needs_revision':'source_needs_revision'}.get(status, 'script_changed')])


def arrangement_item(category, row):
    if category == 'plans':
        version, plan = row
        return ChangeIssue(id=version.id, category=category, parent_id=plan.id, source_id=None,
            title=f'{plan.name} · 第{version.version_no}版', status='review_required', reason_codes=version.review_reasons)
    version, sheet, source, plan = row
    reasons = ['call_sheet_review_required'] if version.validity_status == 'review_required' else []
    if plan.current_published_version_id != source.id: reasons.append('source_plan_changed')
    if source.review_reasons: reasons.append('source_plan_review_required')
    return ChangeIssue(id=version.id, category=category, parent_id=plan.id, source_id=source.id,
        container_id=sheet.id, current_plan_version_id=plan.current_published_version_id,
        title=f'{plan.name} · {sheet.shooting_date}通告第{version.version_no}版', status='review_required', reason_codes=reasons)


async def items_for_rows(session, crew_id, category, rows):
    if category in {'breakdowns', 'storyboards'}:
        reviews = await current_reviews(session, crew_id, category, {target.id:(target, fixed.id, script) for target, fixed, script in rows})
        return [creative_item(category, target, fixed, script, reviews.get(target.id)) for target, fixed, script in rows], reviews
    return [arrangement_item(category, row) for row in rows], {}


class ChangeRepository:
    def __init__(self, sessions): self.sessions = sessions

    async def list(self, user_id, crew_id, *, category, page=1, page_size=20):
        async with self.sessions() as session, session.begin():
            actor = await resource_actor(session, user_id, crew_id, writing=True)
            allowed = authorize_category(actor, category)
            now = await session.scalar(select(func.clock_timestamp()))
            query, order = issue_query(category, crew_id, now)
            total = await session.scalar(select(func.count()).select_from(query.subquery()))
            rows = (await session.execute(query.order_by(*order).offset((page-1)*page_size).limit(page_size))).all()
            items, _ = await items_for_rows(session, crew_id, category, rows)
            return ChangeIssuePage(category=category, allowed_categories=allowed, observed_at=now, items=items, total=total, page=page, page_size=page_size)
