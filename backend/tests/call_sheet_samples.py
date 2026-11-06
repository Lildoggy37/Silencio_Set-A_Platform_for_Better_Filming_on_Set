"""Fictional PDF layout fixtures; no production or user data."""
from datetime import UTC, datetime
from uuid import UUID

from silencio_set.call_sheets.schemas import ExportSnapshot


def sample_export():
    crew_member = '33333333-3333-4333-8333-333333333333'
    version_id = UUID('11111111-1111-4111-8111-111111111111')
    source_id = UUID('22222222-2222-4222-8222-222222222222')
    sheet_id = UUID('44444444-4444-4444-8444-444444444444')
    plan_id = UUID('55555555-5555-4555-8555-555555555555')
    sessions=[]
    for index, (label, place, start, end) in enumerate([
        ('03 · 走廊重逢','教学楼东侧走廊','01:00','03:00'),
        ('04 · 未接来电','一层大厅','03:30','04:30'),
        ('07 · 雨停之前','校园连廊','07:00','09:00')], 1):
        sessions.append(dict(id=f'66666666-6666-4666-8666-{index:012d}', sequence_no=index,
            scene_revision_id='77777777-7777-4777-8777-777777777777', scene_label=label,
            starts_at=f'2026-11-03T{start}:00Z', ends_at=f'2026-11-03T{end}:00Z', transition_minutes=30,
            location_resource_id=None, location_reservation_id=None, location_name=place,
            members=[dict(member_id=crew_member, membership_joined_at='2026-11-01T00:00:00Z', display_name='林雨'),
                     dict(member_id='88888888-8888-4888-8888-888888888888', membership_joined_at='2026-11-01T00:00:00Z', display_name='周屿')],
            resources=[dict(resource_id='99999999-9999-4999-8999-999999999999', quantity=1, reservation_id=None,
                            name='便携收音套装', resource_type='equipment', unit='套')], note='先完成保底镜头，再补充特写。'))
    return ExportSnapshot(exported_at=datetime(2026,11,3,0,15,tzinfo=UTC), version=dict(
        id=version_id, call_sheet_id=sheet_id, source_plan_version_id=source_id, version_no=2, lock_version=4,
        status='published', validity_status='valid', source_current=True,
        call_sheet=dict(id=sheet_id, plan_id=plan_id, shooting_date='2026-11-03', current_published_version_id=version_id),
        gathering_at='2026-11-03T00:30:00Z', gathering_location='教学楼东侧入口',
        contact_note='制片：周屿。请通过剧组群确认到场，临时变化及时沟通。',
        notes='开拍前检查收音与存储卡。\n室外场次如遇降雨，先完成大厅段落，再由制片确认场地变化。\n本页为虚构数据排版样张。',
        arrangement_snapshot=dict(project_name='雨停之前（虚构示例）', plan_name='首日拍摄计划', shooting_date='2026-11-03',
            source_plan_version_id=str(source_id), plan_version_no=3, sessions=sessions, overnight_continuations=[]),
        recipients=[dict(member_id=crew_member,display_name='林雨',required=True),dict(member_id='88888888-8888-4888-8888-888888888888',display_name='周屿',required=True)],
        reviewed_at='2026-11-02T16:00:00Z', published_at='2026-11-02T16:10:00Z'))
