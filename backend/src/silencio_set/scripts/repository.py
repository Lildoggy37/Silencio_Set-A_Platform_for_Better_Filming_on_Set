from hashlib import sha256
from uuid import uuid4
from sqlalchemy import delete, func, select
from silencio_set.accounts.errors import AuthError
from silencio_set.crews.models import AuditLog
from silencio_set.resources.repository import resource_actor, require
from silencio_set.crews.repository import not_found
from silencio_set.resources.domain import check_version
from silencio_set.scripts.models import Script, ScriptVersion, ScriptParagraph, ScriptReceipt, ScriptSourceFile
from silencio_set.scripts.schemas import ScriptView, ParagraphView, VersionView, VersionSummary, ScriptResult, ScriptPage, VersionPage, SourceFileView
from silencio_set.tasks.repository import request_digest


def paragraphs(crew_id, version_id, text):
    # Explicit text import: non-empty lines become stable numbered paragraphs.
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return [ScriptParagraph(id=uuid4(), crew_id=crew_id, script_version_id=version_id,
        paragraph_no=index, text=line, source_page=None) for index, line in enumerate(lines, 1)]


async def version_view(session, row):
    items = (await session.scalars(select(ScriptParagraph).where(ScriptParagraph.crew_id == row.crew_id,
        ScriptParagraph.script_version_id == row.id).order_by(ScriptParagraph.paragraph_no))).all()
    source = await session.scalar(select(ScriptSourceFile).where(ScriptSourceFile.crew_id == row.crew_id,
        ScriptSourceFile.script_id == row.script_id, ScriptSourceFile.id == row.source_file_id)) if row.source_file_id else None
    return VersionView(**{field:getattr(row, field) for field in VersionView.model_fields if field not in {"paragraphs", "source_file"}},
        source_file=SourceFileView.model_validate(source) if source else None,
        paragraphs=[ParagraphView.model_validate(item) for item in items])


class ScriptRepository:
    def __init__(self, sessions): self.sessions = sessions

    async def prepare_import(self, user_id, crew_id, script_id, body, key):
        async with self.sessions() as session, session.begin():
            actor = await resource_actor(session, user_id, crew_id, writing=True)
            require(actor, 'script.read'); require(actor, 'script.edit')
            receipt = await session.scalar(select(ScriptReceipt).where(ScriptReceipt.crew_id == crew_id,
                ScriptReceipt.user_id == user_id, ScriptReceipt.key_digest == sha256(key.encode()).hexdigest()))
            if receipt:
                if receipt.membership_joined_at != actor.joined_at: raise AuthError(409, 'MEMBERSHIP_CHANGED', '入组身份已变化')
                if receipt.request_digest != request_digest('import_file', script_id, body): raise AuthError(409, 'IDEMPOTENCY_KEY_REUSED', '同键不能用于不同内容')
                return ScriptResult.model_validate(receipt.response_snapshot)
            script = await session.scalar(select(Script).where(Script.crew_id == crew_id, Script.id == script_id))
            if script is None: raise not_found()
            check_version(script, body.expected_lock_version)
            return None

    async def source_file(self, user_id, crew_id, script_id, version_id):
        async with self.sessions() as session, session.begin():
            actor = await resource_actor(session, user_id, crew_id, writing=True)
            require(actor, 'script.read')
            source = await session.scalar(select(ScriptSourceFile).join(ScriptVersion,
                (ScriptVersion.crew_id == ScriptSourceFile.crew_id) & (ScriptVersion.script_id == ScriptSourceFile.script_id)
                & (ScriptVersion.source_file_id == ScriptSourceFile.id)).where(ScriptVersion.crew_id == crew_id,
                    ScriptVersion.script_id == script_id, ScriptVersion.id == version_id))
            if source is None: raise not_found()
            return source

    async def read(self, user_id, crew_id, *, script_id=None, version_id=None, page=1, page_size=20, versions=False):
        async with self.sessions() as session, session.begin():
            # Consistent source/version snapshot, shared with writes and role changes.
            actor = await resource_actor(session, user_id, crew_id, writing=True)
            require(actor, "script.read")
            query = select(Script).where(Script.crew_id == crew_id)
            if script_id is None:
                total = await session.scalar(select(func.count()).select_from(query.subquery()))
                rows = (await session.scalars(query.order_by(Script.created_at, Script.id).offset((page-1)*page_size).limit(page_size))).all()
                return ScriptPage(items=[ScriptView.model_validate(row) for row in rows], total=total, page=page, page_size=page_size)
            script = await session.scalar(query.where(Script.id == script_id))
            if script is None: raise not_found()
            query = select(ScriptVersion).where(ScriptVersion.crew_id == crew_id, ScriptVersion.script_id == script.id)
            if version_id:
                version = await session.scalar(query.where(ScriptVersion.id == version_id))
                if version is None: raise not_found()
                return ScriptResult(script=ScriptView.model_validate(script), version=await version_view(session, version))
            if versions:
                total = await session.scalar(select(func.count()).select_from(query.subquery()))
                rows = (await session.scalars(query.order_by(ScriptVersion.version_no.desc()).offset((page-1)*page_size).limit(page_size))).all()
                # List headers only; full paragraphs use the individual version endpoint.
                return VersionPage(items=[VersionSummary.model_validate(row) for row in rows], total=total, page=page, page_size=page_size)
            current = await session.scalar(query.where(ScriptVersion.id == script.current_version_id)) if script.current_version_id else None
            return ScriptResult(script=ScriptView.model_validate(script), version=await version_view(session, current) if current else None)

    async def mutate(self, user_id, crew_id, action, body, request_id, *, idempotency_key, script_id=None, version_id=None, source_file=None, extracted=None):
        async with self.sessions() as session, session.begin():
            actor = await resource_actor(session, user_id, crew_id, writing=True)
            require(actor, "script.read")
            require(actor, "script.confirm" if action in {"confirm", "set_working"} else "script.edit")
            digest = request_digest(action, version_id or script_id, body)
            key = sha256(idempotency_key.encode()).hexdigest()
            receipt = await session.scalar(select(ScriptReceipt).where(ScriptReceipt.crew_id == crew_id, ScriptReceipt.user_id == user_id, ScriptReceipt.key_digest == key))
            if receipt:
                if receipt.membership_joined_at != actor.joined_at: raise AuthError(409, "MEMBERSHIP_CHANGED", "入组身份已变化")
                if receipt.request_digest != digest: raise AuthError(409, "IDEMPOTENCY_KEY_REUSED", "同键不能用于不同内容")
                return ScriptResult.model_validate(receipt.response_snapshot)
            now = await session.scalar(select(func.clock_timestamp()))
            script = await session.scalar(select(Script).where(Script.crew_id == crew_id, Script.id == script_id).with_for_update()) if script_id else None
            if action != "create" and script is None: raise not_found()
            version = None
            if action == "create":
                script = Script(id=uuid4(), crew_id=crew_id, title=body.title, created_by=actor.member_id,
                    lock_version=1, current_version_id=None, working_revision=0, created_at=now, updated_at=now)
                session.add(script)
            elif action in {"add_version", "import_file"}:
                check_version(script, body.expected_lock_version)
                number = await session.scalar(select(func.coalesce(func.max(ScriptVersion.version_no), 0)).where(ScriptVersion.crew_id == crew_id, ScriptVersion.script_id == script.id))
                version = ScriptVersion(id=uuid4(), crew_id=crew_id, script_id=script.id, version_no=number+1,
                    status="pending_check", lock_version=1, created_by=actor.member_id, confirmed_by=None, confirmed_at=None, created_at=now,
                    source_file_id=source_file.id if action == 'import_file' else None)
                if action == 'import_file':
                    source_file.created_by = actor.member_id; source_file.created_at = now
                    session.add(source_file); await session.flush()
                session.add(version); await session.flush()
                if action == 'import_file':
                    session.add_all([ScriptParagraph(id=uuid4(), crew_id=crew_id, script_version_id=version.id,
                        paragraph_no=index, text=item.text, source_page=item.source_page) for index,item in enumerate(extracted.paragraphs,1)])
                else: session.add_all(paragraphs(crew_id, version.id, body.text))
                script.lock_version += 1; script.updated_at = now
            elif action == "set_working":
                check_version(script, body.expected_lock_version)
                version = await session.scalar(select(ScriptVersion).where(ScriptVersion.crew_id == crew_id,
                    ScriptVersion.script_id == script.id, ScriptVersion.id == body.version_id))
                if version is None: raise not_found()
                if version.status != "confirmed": raise AuthError(409, "SCRIPT_NOT_CONFIRMED", "只能选择已确认的工作版本")
                if script.current_version_id != version.id:
                    script.working_revision += 1
                    script.current_version_id = version.id
                    from silencio_set.plans.dependencies import mark_plan_review
                    await mark_plan_review(session, crew_id, 'script_changed', now=now, script_id=script.id)
                script.lock_version += 1; script.updated_at = now
            else:
                version = await session.scalar(select(ScriptVersion).where(ScriptVersion.crew_id == crew_id,
                    ScriptVersion.script_id == script.id, ScriptVersion.id == version_id).with_for_update())
                if version is None: raise not_found()
                check_version(version, body.expected_lock_version)
                if version.status != "pending_check": raise AuthError(409, "SCRIPT_VERSION_IMMUTABLE", "已确认正文不可覆盖，请提交新版本")
                if action == "correct":
                    await session.execute(delete(ScriptParagraph).where(ScriptParagraph.crew_id == crew_id, ScriptParagraph.script_version_id == version.id))
                    session.add_all(paragraphs(crew_id, version.id, body.text))
                elif action == "confirm":
                    version.status = "confirmed"; version.confirmed_by = actor.member_id; version.confirmed_at = now
                else: raise ValueError("Unknown script action")
                version.lock_version += 1
            await session.flush()
            result = ScriptResult(script=ScriptView.model_validate(script), version=await version_view(session, version) if version else None)
            session.add(ScriptReceipt(crew_id=crew_id, user_id=user_id, membership_joined_at=actor.joined_at,
                script_id=script.id, key_digest=key, request_digest=digest, response_snapshot=result.model_dump(mode="json")))
            session.add(AuditLog(crew_id=crew_id, actor_type="user", actor_id=user_id, action=f"script.{action}", target_type="script", target_id=script.id,
                request_id=request_id, result="success", after_summary={"script_version":script.lock_version,"version_id":str(version.id) if version else None}))
            await session.flush()
        return result
