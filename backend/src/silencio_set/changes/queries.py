from sqlalchemy import Date, func, or_, select, text

from silencio_set.call_sheets.models import CallSheet, CallSheetVersion
from silencio_set.creative.models import Breakdown, SceneRevision, StoryboardVersion, SourceReviewRecord
from silencio_set.creative.source_evidence import latest_review_query
from silencio_set.plans.models import ShootingPlan, PlanVersion, ShootingSession
from silencio_set.scripts.models import Script, ScriptVersion


def allowed_categories(permissions):
    result = []
    if 'script.read' in permissions:
        if 'scene.read' in permissions: result.append('breakdowns')
        if 'storyboard.read' in permissions: result.append('storyboards')
    if {'plan.read_all', 'plan.edit'} & permissions: result.append('plans')
    if {'call_sheet.edit', 'call_sheet.review', 'call_sheet.publish'} & permissions: result.append('call_sheets')
    return result


def unfinished(version_id, now, shooting_date=None):
    query = select(ShootingSession.id).where(ShootingSession.plan_version_id == version_id,
        ShootingSession.ends_at + ShootingSession.transition_minutes * text("INTERVAL '1 minute'") > now)
    if shooting_date is not None:
        query = query.where(func.timezone('Asia/Shanghai', ShootingSession.starts_at).cast(Date) == shooting_date)
    return query.correlate_except(ShootingSession).exists()


def target_query(category, crew_id):
    if category in {'breakdowns', 'storyboards'}:
        model = Breakdown if category == 'breakdowns' else StoryboardVersion
        query = select(model, ScriptVersion, Script)
        if category == 'storyboards':
            query = query.join(SceneRevision, SceneRevision.id == model.scene_revision_id).join(ScriptVersion, ScriptVersion.id == SceneRevision.script_version_id)
        else: query = query.join(ScriptVersion, ScriptVersion.id == model.script_version_id)
        query = query.join(Script, Script.id == ScriptVersion.script_id)
        return query.where(model.crew_id == crew_id), model
    if category == 'plans':
        return select(PlanVersion, ShootingPlan).join(ShootingPlan, ShootingPlan.id == PlanVersion.plan_id).where(PlanVersion.crew_id == crew_id), PlanVersion
    query = select(CallSheetVersion, CallSheet, PlanVersion, ShootingPlan).join(CallSheet,
        CallSheet.id == CallSheetVersion.call_sheet_id).join(PlanVersion,
        PlanVersion.id == CallSheetVersion.source_plan_version_id).join(ShootingPlan, ShootingPlan.id == CallSheetVersion.plan_id).where(
        CallSheetVersion.crew_id == crew_id)
    return query, CallSheetVersion


def issue_query(category, crew_id, now):
    query, model = target_query(category, crew_id)
    if category in {'breakdowns','storyboards'}:
        conclusion = latest_review_query(category, model, ScriptVersion.id, Script).with_only_columns(SourceReviewRecord.conclusion).scalar_subquery()
        query = query.where(ScriptVersion.id.is_distinct_from(Script.current_version_id), conclusion.is_distinct_from('still_applicable'))
        return query, (Script.updated_at.desc(), model.created_at.desc(), model.id)
    if category == 'plans':
        query = query.where(ShootingPlan.current_published_version_id == PlanVersion.id,
            func.jsonb_array_length(PlanVersion.review_reasons) > 0, unfinished(PlanVersion.id, now))
        return query, (PlanVersion.updated_at.desc(), PlanVersion.id)
    query = query.where(CallSheet.current_published_version_id == CallSheetVersion.id,
        CallSheetVersion.validity_status.in_(['valid', 'review_required']),
        or_(CallSheetVersion.validity_status == 'review_required',
            ShootingPlan.current_published_version_id.is_distinct_from(PlanVersion.id), func.jsonb_array_length(PlanVersion.review_reasons) > 0),
        unfinished(PlanVersion.id, now, CallSheet.shooting_date))
    return query, (CallSheet.shooting_date, CallSheetVersion.id)
