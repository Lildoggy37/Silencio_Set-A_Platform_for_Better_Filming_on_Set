from hashlib import sha256
from uuid import UUID, uuid4

from sqlalchemy import delete, func, select

from silencio_set.accounts.errors import AuthError
from silencio_set.accounts.models import User
from silencio_set.call_sheets.domain import FULL_PERMISSIONS, parsed_time, required_members, day_snapshot, require_complete, content_snapshot, content_digest, reset_approval
from silencio_set.call_sheets.models import CallSheet, CallSheetVersion, DraftRecipient, PublishedRecipient, CallSheetReview, CallSheetReceipt, CallSheetEvent
from silencio_set.call_sheets.schemas import SheetView, SheetPage, VersionSummary, VersionPage, VersionView, RecipientView, ReviewView, ReviewPage, RecipientOption, RecipientPage, ExportSnapshot
from silencio_set.crews.models import AuditLog, Crew, CrewMember
from silencio_set.crews.repository import not_found
from silencio_set.plans.models import ShootingPlan, PlanVersion
from silencio_set.plans.repository import scoped, rows_by_ids
from silencio_set.resources.domain import check_version
from silencio_set.resources.repository import resource_actor, require
from silencio_set.tasks.repository import request_digest


def received_versions(crew_id, actor):
    return select(PublishedRecipient.call_sheet_version_id).where(PublishedRecipient.crew_id == crew_id,
        PublishedRecipient.member_id == actor.member_id, PublishedRecipient.membership_joined_at == actor.joined_at)


def require_reader(actor):
    if not FULL_PERMISSIONS & actor.permissions and 'call_sheet.read_related' not in actor.permissions:
        raise AuthError(403, 'FORBIDDEN', '当前没有查看通告的权限')


async def source_current(session, version):
    plan = await scoped(session, ShootingPlan, version.crew_id, version.plan_id)
    source = await scoped(session, PlanVersion, version.crew_id, version.source_plan_version_id)
    return plan.current_published_version_id == source.id and source.status == 'published' and not source.review_reasons


async def require_source(session, version):
    if not await source_current(session, version):
        raise AuthError(409, 'SOURCE_PLAN_STALE', '来源排期已变化或待复核，请按当前正式排期新建通告版本')


async def recipient_rows(session, version):
    model = PublishedRecipient if version.status == 'published' else DraftRecipient
    return (await session.scalars(select(model).where(model.crew_id == version.crew_id,
        model.call_sheet_version_id == version.id).order_by(model.member_id))).all()


async def view(session, version, *, sheet=None, review_id=None, notification_status=None):
    sheet = sheet if sheet is not None else await scoped(session, CallSheet, version.crew_id, version.call_sheet_id)
    required = required_members(version.arrangement_snapshot)
    rows = await recipient_rows(session, version)
    return VersionView(**VersionSummary.model_validate(version).model_dump(), call_sheet=SheetView.model_validate(sheet),
        source_current=await source_current(session, version), gathering_at=version.gathering_at,
        gathering_location=version.gathering_location, contact_note=version.contact_note, notes=version.notes,
        arrangement_snapshot=version.arrangement_snapshot, reviewed_at=version.reviewed_at, published_at=version.published_at,
        recipients=[RecipientView(member_id=row.member_id, display_name=row.display_name,
            required=row.required_at_publish if version.status == 'published' else str(row.member_id) in required) for row in rows],
        review_id=review_id, notification_status=notification_status)


async def active_people(session, crew_id, member_ids):
    people = await rows_by_ids(session, CrewMember, crew_id, set(member_ids))
    enabled = set((await session.scalars(select(User.id).where(User.id.in_({person.user_id for person in people.values()}), User.status == 'active'))).all()) if people else set()
    if any(person.status != 'active' or person.user_id not in enabled for person in people.values()):
        raise AuthError(409, 'RECIPIENT_INACTIVE', '通告接收人已离组或账号不可用，请重新核对')
    return people


async def set_recipients(session, version, member_ids):
    required = required_members(version.arrangement_snapshot)
    if not set(required) <= {str(member_id) for member_id in member_ids}:
        raise AuthError(409, 'REQUIRED_RECIPIENT_MISSING', '不能移除当天拍摄参与人员')
    people = await active_people(session, version.crew_id, member_ids)
    for member_id, source in required.items():
        if people[UUID(member_id)].joined_at != parsed_time(source['membership_joined_at']):
            raise AuthError(409, 'RECIPIENT_SET_INVALID', '来源排期参与人员的入组身份已变化，请先调整正式排期')
    await session.execute(delete(DraftRecipient).where(DraftRecipient.crew_id == version.crew_id, DraftRecipient.call_sheet_version_id == version.id))
    session.add_all([DraftRecipient(crew_id=version.crew_id, call_sheet_version_id=version.id,
        member_id=person.id, membership_joined_at=person.joined_at, display_name=person.display_name) for person in people.values()])


async def validate_recipients(session, version):
    rows = await recipient_rows(session, version)
    required = required_members(version.arrangement_snapshot)
    if not set(required) <= {str(row.member_id) for row in rows}:
        raise AuthError(409, 'REQUIRED_RECIPIENT_MISSING', '通告缺少当天拍摄参与人员')
    people = await active_people(session, version.crew_id, {row.member_id for row in rows})
    if any(people[row.member_id].joined_at != row.membership_joined_at for row in rows):
        raise AuthError(409, 'RECIPIENT_SET_INVALID', '通告接收人的入组身份已变化，请重新选择并审核')
    for member_id, source in required.items():
        if people[UUID(member_id)].joined_at != parsed_time(source['membership_joined_at']):
            raise AuthError(409, 'RECIPIENT_SET_INVALID', '来源排期参与人员的入组身份已变化')
    return rows


async def event(session, version, kind, now, recipients=None):
    recipients = await recipient_rows(session, version) if recipients is None else recipients
    session.add(CallSheetEvent(id=uuid4(), crew_id=version.crew_id, call_sheet_version_id=version.id,
        object_lock_version=version.lock_version, kind=kind, created_at=now,
        recipient_snapshots=[dict(member_id=str(row.member_id), membership_joined_at=row.membership_joined_at.isoformat()) for row in recipients]))


class CallSheetRepository:
    def __init__(self, sessions):
        self.sessions = sessions

    async def export_snapshot(self, user_id, crew_id, version_id, expected_lock_version):
        async with self.sessions() as session, session.begin():
            actor = await resource_actor(session, user_id, crew_id, writing=True)
            require(actor, 'call_sheet.export')
            require_reader(actor)
            version = await scoped(session, CallSheetVersion, crew_id, version_id)
            if not FULL_PERMISSIONS & actor.permissions:
                visible = await session.scalar(select(received_versions(crew_id, actor).where(PublishedRecipient.call_sheet_version_id == version_id).exists()))
                if version.status != 'published' or not visible: raise not_found()
            check_version(version, expected_lock_version)
            return ExportSnapshot(version=await view(session, version), exported_at=await session.scalar(select(func.clock_timestamp())))

    async def read(self, user_id, crew_id, *, plan_id=None, sheet_id=None, version_id=None, history=False, page=1, page_size=20):
        async with self.sessions() as session, session.begin():
            actor = await resource_actor(session, user_id, crew_id, writing=True)
            require_reader(actor)
            full = bool(FULL_PERMISSIONS & actor.permissions)
            if version_id:
                version = await scoped(session, CallSheetVersion, crew_id, version_id)
                if not full:
                    visible = await session.scalar(select(received_versions(crew_id, actor).where(PublishedRecipient.call_sheet_version_id == version_id).exists()))
                    if version.status != 'published' or not visible: raise not_found()
                if not history: return await view(session, version)
                if not full: raise AuthError(403, 'FORBIDDEN', '当前没有查看通告审核历史的权限')
                query = select(CallSheetReview).where(CallSheetReview.crew_id == crew_id, CallSheetReview.call_sheet_version_id == version_id)
                schema, item_schema, order = ReviewPage, ReviewView, CallSheetReview.reviewed_at.desc()
            elif sheet_id:
                await scoped(session, CallSheet, crew_id, sheet_id)
                query = select(CallSheetVersion).where(CallSheetVersion.crew_id == crew_id, CallSheetVersion.call_sheet_id == sheet_id)
                if not full: query = query.where(CallSheetVersion.id.in_(received_versions(crew_id, actor)), CallSheetVersion.status == 'published')
                schema, item_schema, order = VersionPage, VersionSummary, CallSheetVersion.version_no.desc()
            else:
                await scoped(session, ShootingPlan, crew_id, plan_id)
                query = select(CallSheet).where(CallSheet.crew_id == crew_id, CallSheet.plan_id == plan_id)
                if not full:
                    related = select(CallSheetVersion.call_sheet_id).where(CallSheetVersion.id.in_(received_versions(crew_id, actor)), CallSheetVersion.crew_id == crew_id)
                    query = query.where(CallSheet.id.in_(related))
                schema, item_schema, order = SheetPage, SheetView, CallSheet.shooting_date.desc()
            total = await session.scalar(select(func.count()).select_from(query.subquery()))
            rows = (await session.scalars(query.order_by(order, query.column_descriptions[0]['entity'].id).offset((page - 1) * page_size).limit(page_size))).all()
            return schema(items=[item_schema.model_validate(row) for row in rows], total=total, page=page, page_size=page_size)

    async def recipient_options(self, user_id, crew_id, version_id, page, page_size):
        async with self.sessions() as session, session.begin():
            actor = await resource_actor(session, user_id, crew_id, writing=True)
            require(actor, 'call_sheet.edit'); require(actor, 'member.read')
            version = await scoped(session, CallSheetVersion, crew_id, version_id)
            required = required_members(version.arrangement_snapshot)
            query = select(CrewMember).join(User, User.id == CrewMember.user_id).where(CrewMember.crew_id == crew_id, CrewMember.status == 'active', User.status == 'active')
            total = await session.scalar(select(func.count()).select_from(query.subquery()))
            rows = (await session.scalars(query.order_by(CrewMember.display_name, CrewMember.id).offset((page - 1) * page_size).limit(page_size))).all()
            return RecipientPage(items=[RecipientOption(member_id=row.id, display_name=row.display_name, required=str(row.id) in required) for row in rows], total=total, page=page, page_size=page_size)

    async def mutate(self, user_id, crew_id, action, body, request_id, *, idempotency_key, target_id=None):
        async with self.sessions() as session, session.begin():
            actor = await resource_actor(session, user_id, crew_id, writing=True)
            require(actor, 'call_sheet.review' if action == 'review' else 'call_sheet.publish' if action == 'publish' else 'call_sheet.edit')
            key = sha256(idempotency_key.encode()).hexdigest()
            digest = request_digest(f'call_sheet.{action}', target_id, body)
            receipt = await session.scalar(select(CallSheetReceipt).where(CallSheetReceipt.crew_id == crew_id, CallSheetReceipt.user_id == user_id, CallSheetReceipt.key_digest == key))
            if receipt:
                if receipt.membership_joined_at != actor.joined_at: raise AuthError(409, 'MEMBERSHIP_CHANGED', '入组身份已变化')
                if receipt.request_digest != digest: raise AuthError(409, 'IDEMPOTENCY_KEY_REUSED', '同键不能用于不同内容')
                return VersionView.model_validate(receipt.response_snapshot)
            now = await session.scalar(select(func.clock_timestamp()))
            review_id, notification_status = None, None
            if action in {'create', 'version'}:
                if not {'plan.read_all', 'plan.edit'} & actor.permissions: raise AuthError(403, 'FORBIDDEN', '生成通告需要完整来源排期查看权限')
                source = await scoped(session, PlanVersion, crew_id, body.source_plan_version_id)
                plan = await scoped(session, ShootingPlan, crew_id, source.plan_id)
                if source.status != 'published' or plan.current_published_version_id != source.id or source.review_reasons:
                    raise AuthError(409, 'SOURCE_PLAN_STALE', '请选择当前且无需复核的正式排期')
                if action == 'create':
                    sheet = await session.scalar(select(CallSheet).where(CallSheet.crew_id == crew_id, CallSheet.plan_id == plan.id, CallSheet.shooting_date == body.shooting_date))
                    if sheet is not None: raise AuthError(409, 'CALL_SHEET_EXISTS', '该日期已有通告，请进入原通告创建新版本')
                    sheet = CallSheet(id=uuid4(), crew_id=crew_id, plan_id=plan.id, shooting_date=body.shooting_date, current_published_version_id=None, created_at=now)
                    session.add(sheet); await session.flush()
                else:
                    sheet = await scoped(session, CallSheet, crew_id, target_id)
                    if sheet.plan_id != source.plan_id: raise not_found()
                crew = await session.scalar(select(Crew).where(Crew.id == crew_id))
                snapshot = day_snapshot(source, sheet.shooting_date, project_name=crew.project_name, plan_name=plan.name)
                last = await session.scalar(select(func.coalesce(func.max(CallSheetVersion.version_no), 0)).where(CallSheetVersion.call_sheet_id == sheet.id))
                version = CallSheetVersion(id=uuid4(), crew_id=crew_id, call_sheet_id=sheet.id, plan_id=plan.id, source_plan_version_id=source.id,
                    version_no=last + 1, lock_version=1, status='draft', validity_status=None, arrangement_snapshot=snapshot,
                    gathering_at=None, gathering_location=None, contact_note=None, notes=None, approved_content_digest=None,
                    created_by=actor.member_id, reviewed_by=None, reviewed_at=None, published_by=None, published_at=None, created_at=now, updated_at=now)
                if action == 'version' and body.copy_from:
                    original = await scoped(session, CallSheetVersion, crew_id, body.copy_from)
                    if original.call_sheet_id != sheet.id: raise not_found()
                    for field in ('gathering_at', 'gathering_location', 'contact_note', 'notes'): setattr(version, field, getattr(original, field))
                session.add(version); await session.flush()
                await set_recipients(session, version, [UUID(member_id) for member_id in required_members(snapshot)])
            else:
                version = await scoped(session, CallSheetVersion, crew_id, target_id)
                sheet = await scoped(session, CallSheet, crew_id, version.call_sheet_id)
                check_version(version, body.expected_lock_version)
                if action in {'edit', 'recipients'}:
                    if version.status not in {'draft', 'approved'}: raise AuthError(409, 'INVALID_STATE', '仅草稿或已审核通告可修改；已发布须新建版本')
                    if action == 'edit':
                        for field, value in body.model_dump(exclude_unset=True, exclude={'expected_lock_version'}).items(): setattr(version, field, value)
                    else:
                        require(actor, 'member.read')
                        await set_recipients(session, version, body.member_ids)
                    reset_approval(version)
                elif action == 'submit':
                    if version.status != 'draft': raise AuthError(409, 'INVALID_STATE', '仅草稿可送审')
                    await require_source(session, version); require_complete(version); await validate_recipients(session, version)
                    version.status = 'pending_review'
                elif action == 'review':
                    if version.status != 'pending_review': raise AuthError(409, 'INVALID_STATE', '仅待审核通告可审核')
                    recipients = await recipient_rows(session, version)
                    if body.decision == 'accept':
                        await require_source(session, version); require_complete(version); recipients = await validate_recipients(session, version)
                    review_id = uuid4()
                    session.add(CallSheetReview(id=review_id, crew_id=crew_id, call_sheet_version_id=version.id, reviewed_by=actor.member_id,
                        reviewed_at=now, decision=body.decision, review_note=body.review_note, reviewed_lock_version=version.lock_version, content_snapshot=content_snapshot(version, recipients)))
                    if body.decision == 'accept':
                        version.status = 'approved'; version.reviewed_by = actor.member_id; version.reviewed_at = now
                        version.approved_content_digest = content_digest(version, recipients)
                    else: reset_approval(version)
                elif action == 'publish':
                    if version.status not in {'draft', 'pending_review', 'approved'}: raise AuthError(409, 'INVALID_STATE', '仅未发布通告可预览发布')
                    if sheet.current_published_version_id != body.expected_current_published_version_id: raise AuthError(409, 'CURRENT_VERSION_CHANGED', '当前通告版本已变化，请重新读取')
                    await require_source(session, version); require_complete(version); recipients = await validate_recipients(session, version)
                    if version.status in {'draft', 'pending_review'}:
                        require(actor, 'call_sheet.review')
                        review_id = uuid4()
                        session.add(CallSheetReview(id=review_id, crew_id=crew_id, call_sheet_version_id=version.id,
                            reviewed_by=actor.member_id, reviewed_at=now, decision='accept', review_note='发布前预览确认',
                            reviewed_lock_version=version.lock_version, content_snapshot=content_snapshot(version, recipients)))
                        version.reviewed_by = actor.member_id; version.reviewed_at = now
                        version.approved_content_digest = content_digest(version, recipients)
                    if version.approved_content_digest != content_digest(version, recipients): raise AuthError(409, 'REVIEW_REQUIRED', '审核依据与当前内容或接收人不一致')
                    if sheet.current_published_version_id:
                        previous = await scoped(session, CallSheetVersion, crew_id, sheet.current_published_version_id)
                        check_version(previous, previous.lock_version)
                        previous.validity_status = 'superseded'; previous.lock_version += 1; previous.updated_at = now
                        await event(session, previous, 'superseded', now)
                    required = required_members(version.arrangement_snapshot)
                    session.add_all([PublishedRecipient(crew_id=crew_id, call_sheet_version_id=version.id, member_id=row.member_id,
                        membership_joined_at=row.membership_joined_at, display_name=row.display_name, required_at_publish=str(row.member_id) in required, published_at=now) for row in recipients])
                    version.status = 'published'; version.validity_status = 'valid'; version.published_by = actor.member_id; version.published_at = now
                    sheet.current_published_version_id = version.id
                    notification_status = 'pending_dispatch'
                else: raise ValueError('Unknown call sheet action')
                version.lock_version += 1; version.updated_at = now
                if action == 'publish': await event(session, version, 'published', now, recipients)
            await session.flush()
            result = await view(session, version, sheet=sheet, review_id=review_id, notification_status=notification_status)
            session.add(CallSheetReceipt(crew_id=crew_id, user_id=user_id, member_id=actor.member_id, membership_joined_at=actor.joined_at,
                key_digest=key, request_digest=digest, response_snapshot=result.model_dump(mode='json')))
            session.add(AuditLog(crew_id=crew_id, actor_type='user', actor_id=user_id, action=f'call_sheet.{action}', target_type='call_sheet_version',
                target_id=version.id, result='success', request_id=request_id, after_summary={'status':version.status, 'lock_version':version.lock_version}))
            await session.flush()
        return result
