import json
from datetime import datetime, time
from hashlib import sha256
from zoneinfo import ZoneInfo

from pydantic import TypeAdapter

from silencio_set.accounts.errors import AuthError

JSON = TypeAdapter(dict)
SHANGHAI = ZoneInfo('Asia/Shanghai')
FULL_PERMISSIONS = frozenset({'call_sheet.edit', 'call_sheet.review', 'call_sheet.publish'})


def parsed_time(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def shooting_day(item):
    return parsed_time(item['starts_at']).astimezone(SHANGHAI).date()


def day_snapshot(source, sheet_date, *, project_name, plan_name):
    items = source.publication_snapshot['items']
    selected = [item for item in items if shooting_day(item) == sheet_date]
    if not selected:
        raise AuthError(409, 'CALL_SHEET_NO_SESSIONS', '该日期没有正式拍摄场次')
    day_start = datetime.combine(sheet_date, time.min, tzinfo=SHANGHAI)
    continuation = [dict(scene_label=item['scene_label'], starts_at=item['starts_at'], ends_at=item['ends_at']) for item in items
                    if shooting_day(item) < sheet_date and parsed_time(item['ends_at']) > day_start]
    return dict(project_name=project_name, plan_name=plan_name, shooting_date=sheet_date.isoformat(),
                source_plan_version_id=str(source.id), plan_version_no=source.version_no, sessions=selected,
                overnight_continuations=continuation)


def required_members(snapshot):
    return {person['member_id']: person for item in snapshot['sessions'] for person in item['members']}


def require_complete(version):
    first_start = min(parsed_time(item['starts_at']) for item in version.arrangement_snapshot['sessions'])
    if version.gathering_at is None or version.gathering_location is None or not version.gathering_location.strip():
        raise AuthError(409, 'CALL_SHEET_INCOMPLETE', '请补充集合时间和集合地点后再送审')
    if version.gathering_at > first_start:
        raise AuthError(409, 'GATHERING_AFTER_SHOOT', '集合时间不能晚于当天第一场拍摄开始时间')


def content_snapshot(version, recipients):
    return JSON.dump_python(dict(source_plan_version_id=version.source_plan_version_id,
        arrangement_snapshot=version.arrangement_snapshot, gathering_at=version.gathering_at,
        gathering_location=version.gathering_location, contact_note=version.contact_note, notes=version.notes,
        recipients=[dict(member_id=row.member_id, membership_joined_at=row.membership_joined_at, display_name=row.display_name)
                    for row in sorted(recipients, key=lambda row: str(row.member_id))]), mode='json')


def content_digest(version, recipients):
    return sha256(json.dumps(content_snapshot(version, recipients), sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def reset_approval(version):
    version.status = 'draft'
    version.reviewed_by = None
    version.reviewed_at = None
    version.approved_content_digest = None
