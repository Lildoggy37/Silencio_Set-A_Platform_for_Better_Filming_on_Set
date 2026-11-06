"""A source review only applies to the exact target and working-script epoch."""
from sqlalchemy import select
from silencio_set.creative.models import SourceReviewRecord


def basis_fields(target, fixed_id, script):
    return {'target_lock_version': target.lock_version, 'source_script_version_id': fixed_id,
            'working_script_version_id': script.current_version_id, 'working_revision': script.working_revision}


def latest_review_query(kind, target, fixed_id, script):
    field = SourceReviewRecord.breakdown_id if kind == 'breakdowns' else SourceReviewRecord.storyboard_version_id
    return select(SourceReviewRecord).where(SourceReviewRecord.crew_id == target.crew_id, field == target.id,
        target.review_status == 'confirmed',
        *(getattr(SourceReviewRecord, key) == value for key, value in basis_fields(target, fixed_id, script).items())
    ).order_by(SourceReviewRecord.reviewed_at.desc(), SourceReviewRecord.id.desc()).limit(1).correlate_except(SourceReviewRecord)


async def current_reviews(session, crew_id, kind, contexts):
    # contexts: target ID -> (target, fixed script version ID, current Script).
    stale = {key: value for key, value in contexts.items()
             if value[0].review_status == 'confirmed' and value[2].current_version_id is not None
             and value[1] != value[2].current_version_id}
    if not stale:
        return {}
    target_field = SourceReviewRecord.breakdown_id if kind == 'breakdowns' else SourceReviewRecord.storyboard_version_id
    rows = (await session.scalars(select(SourceReviewRecord).where(SourceReviewRecord.crew_id == crew_id,
        target_field.in_(stale), SourceReviewRecord.working_script_version_id.in_({value[2].current_version_id for value in stale.values()}),
        SourceReviewRecord.working_revision.in_({value[2].working_revision for value in stale.values()}),
        SourceReviewRecord.target_lock_version.in_({value[0].lock_version for value in stale.values()})
    ).order_by(SourceReviewRecord.reviewed_at.desc(), SourceReviewRecord.id.desc()))).all()
    result = {}
    field = 'breakdown_id' if kind == 'breakdowns' else 'storyboard_version_id'
    for row in rows:
        key = getattr(row, field)
        target, fixed_id, script = stale[key]
        if all(getattr(row, field) == value for field, value in basis_fields(target, fixed_id, script).items()):
            result.setdefault(key, row)
    return result


def assessment_status(fixed_id, script, review):
    if script.current_version_id is None: return 'no_working_version'
    if script.current_version_id == fixed_id: return 'current'
    return review.conclusion if review else 'review_required'
