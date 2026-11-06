from sqlalchemy import select

from silencio_set.call_sheets.domain import shooting_day
from silencio_set.call_sheets.models import CallSheet, CallSheetVersion
from silencio_set.call_sheets.repository import event
from silencio_set.resources.domain import check_version


async def invalidate_for_plan(session, crew_id, plan_id, source, now):
    rows = (await session.execute(select(CallSheet, CallSheetVersion).join(CallSheetVersion,
        CallSheetVersion.id == CallSheet.current_published_version_id).where(
        CallSheet.crew_id == crew_id, CallSheet.plan_id == plan_id, CallSheetVersion.source_plan_version_id != source.id))).all()
    dates = {shooting_day(item) for item in source.publication_snapshot['items']}
    affected = []
    for sheet, version in rows:
        validity = 'review_required' if sheet.shooting_date in dates else 'withdrawn'
        affected.append(version.id)
        if version.validity_status == validity: continue
        check_version(version, version.lock_version)
        version.validity_status = validity; version.lock_version += 1; version.updated_at = now
        await event(session, version, validity, now)
    return affected


async def mark_source_review(session, crew_id, source_version_id, now):
    rows = (await session.scalars(select(CallSheetVersion).join(CallSheet,
        CallSheet.current_published_version_id == CallSheetVersion.id).where(CallSheetVersion.crew_id == crew_id,
        CallSheetVersion.source_plan_version_id == source_version_id, CallSheetVersion.validity_status == 'valid'))).all()
    for version in rows:
        check_version(version, version.lock_version)
        version.validity_status = 'review_required'; version.lock_version += 1; version.updated_at = now
        await event(session, version, 'review_required', now)


async def withdraw_for_plan(session, crew_id, plan_id, now):
    rows = (await session.scalars(select(CallSheetVersion).join(CallSheet,
        CallSheet.current_published_version_id == CallSheetVersion.id).where(CallSheet.crew_id == crew_id,
        CallSheet.plan_id == plan_id, CallSheetVersion.validity_status != 'withdrawn'))).all()
    for version in rows:
        check_version(version, version.lock_version)
        version.validity_status = 'withdrawn'; version.lock_version += 1; version.updated_at = now
        await event(session, version, 'withdrawn', now)
    return [version.id for version in rows]
