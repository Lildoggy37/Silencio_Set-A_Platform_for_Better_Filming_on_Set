from uuid import uuid4
from silencio_set.changes.models import ImpactIssue, ImpactTransition
from silencio_set.resources.domain import check_version

TARGET_FIELDS = {'breakdowns':'breakdown_id', 'storyboards':'storyboard_version_id', 'plans':'plan_version_id', 'call_sheets':'call_sheet_version_id'}


def state(row):
    return {'status':row.status, 'target':row.target_snapshot, 'evidence':row.evidence,
            'resolution_basis':row.resolution_basis, 'lock_version':row.lock_version, 'reopened_count':row.reopened_count}


def transition(row, check, kind, before):
    return ImpactTransition(id=uuid4(),crew_id=row.crew_id,issue_id=row.id,check_id=check.id,kind=kind,
                            before_state=before,after_state=state(row),recorded_at=check.checked_at)


def observe(row, item, basis, check):
    snapshot = item.model_dump(mode='json')
    before = state(row) if row else None
    if row is None:
        row = ImpactIssue(id=uuid4(),crew_id=check.crew_id,category=check.category,target_id=item.id,
            **{TARGET_FIELDS[check.category]:item.id},status='open',lock_version=1,reopened_count=0,
            first_detected_at=check.checked_at,resolved_at=None,resolution_basis=None)
        kind = 'detected'
    elif row.status == 'resolved':
        check_version(row,row.lock_version)
        row.status='open'; row.resolved_at=None; row.resolution_basis=None
        row.reopened_count+=1; row.lock_version+=1; kind='reopened'
    elif row.target_snapshot != snapshot or row.evidence != basis:
        check_version(row,row.lock_version)
        row.lock_version+=1; kind='updated'
    else: kind=None
    row.target_snapshot=snapshot; row.evidence=basis
    row.last_checked_at=check.checked_at; row.last_check_id=check.id
    return row, transition(row,check,kind,before) if kind else None


def resolve(row, basis, reason, check):
    before=state(row)
    check_version(row,row.lock_version)
    row.status='resolved'; row.resolved_at=check.checked_at
    row.evidence=basis; row.resolution_basis={'reason':reason,'check_id':str(check.id),'checked_at':check.checked_at.isoformat()}
    row.last_checked_at=check.checked_at; row.last_check_id=check.id; row.lock_version+=1
    return transition(row,check,'resolved',before)
