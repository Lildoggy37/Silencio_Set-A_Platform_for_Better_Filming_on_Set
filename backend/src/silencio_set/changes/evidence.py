"""Evidence contains business references, never a replacement business state."""
from pydantic import TypeAdapter
from silencio_set.creative.source_evidence import assessment_status

JSON = TypeAdapter(dict)


def evidence(category, row, review=None, *, has_unfinished=True):
    target = row[0]
    result = {'target_id':target.id, 'target_lock_version':target.lock_version, 'version_no':target.version_no}
    if category in {'breakdowns','storyboards'}:
        _, fixed, script = row
        result.update(source_script_version_id=fixed.id, source_version_no=fixed.version_no,
            working_script_version_id=script.current_version_id, working_revision=script.working_revision,
            source_review_id=review.id if review else None, applicability=assessment_status(fixed.id,script,review))
    elif category == 'plans':
        _, plan = row
        result.update(current_plan_version_id=plan.current_published_version_id, review_reasons=target.review_reasons,
            withdrawn_at=target.withdrawn_at, has_unfinished=has_unfinished)
    else:
        _, sheet, source, plan = row
        result.update(current_call_sheet_version_id=sheet.current_published_version_id, validity_status=target.validity_status,
            source_plan_version_id=source.id, current_plan_version_id=plan.current_published_version_id,
            source_review_reasons=source.review_reasons, has_unfinished=has_unfinished)
    return JSON.dump_python(result,mode='json')


def resolution_reason(category, row, review=None, *, has_unfinished=True):
    if category in {'breakdowns','storyboards'}:
        _, fixed, script = row
        status = assessment_status(fixed.id,script,review)
        return {'current':'source_matches_working','still_applicable':'source_still_applicable'}[status]
    if category == 'plans':
        target, plan = row
        if target.withdrawn_at is not None: return 'plan_withdrawn'
        if plan.current_published_version_id != target.id: return 'plan_superseded'
        return 'shooting_finished' if not has_unfinished else 'plan_review_flags_cleared'
    target, sheet, _, _ = row
    if target.validity_status == 'withdrawn': return 'call_sheet_withdrawn'
    if sheet.current_published_version_id != target.id or target.validity_status == 'superseded': return 'call_sheet_superseded'
    return 'shooting_finished' if not has_unfinished else 'call_sheet_source_current'
