from sqlalchemy.exc import DBAPIError
from hashlib import sha256
from pathlib import Path
from uuid import uuid4
import anyio
from silencio_set.accounts.errors import AuthError
from silencio_set.accounts.profile import AccountUnavailable, ProfileIncomplete, require_completed_profile
from silencio_set.crews.schemas import clean_text
from silencio_set.scripts.extractor import MAX_BYTES, parse_in_process
from silencio_set.scripts.models import ScriptSourceFile
from silencio_set.scripts.schemas import FileImportIdentity


class ScriptService:
    def __init__(self, repository, audits, storage=None):
        self.repository, self.audits, self.storage = repository, audits, storage
        self.import_limiter = anyio.CapacityLimiter(2)

    async def import_file(self, user, crew_id, script_id, upload, expected_lock_version, request_id, idempotency_key):
        data = await upload.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES: raise AuthError(413, 'DOCUMENT_TOO_LARGE', '文件不能超过20MiB')
        try: name = clean_text((upload.filename or '').replace('\\','/').rsplit('/',1)[-1], 200)
        except ValueError: raise AuthError(422, 'DOCUMENT_INVALID', '请提供有效文件名') from None
        file_format = Path(name).suffix.lower().lstrip('.')
        if file_format not in {'docx','pdf','txt'}: raise AuthError(415, 'DOCUMENT_TYPE_UNSUPPORTED', '仅支持DOCX、PDF和UTF-8 TXT')
        body = FileImportIdentity(expected_lock_version=expected_lock_version, original_name=name,
            file_format=file_format, byte_size=len(data), sha256=sha256(data).hexdigest())
        previous = await self.repository.prepare_import(user.id, crew_id, script_id, body, idempotency_key)
        if previous is not None: return previous
        extracted = await anyio.to_thread.run_sync(parse_in_process, data, file_format, limiter=self.import_limiter)
        source_id = uuid4()
        key = f'{crew_id.hex}/{source_id.hex}.{file_format}'
        await anyio.to_thread.run_sync(self.storage.put, key, data)
        source = ScriptSourceFile(id=source_id, crew_id=crew_id, script_id=script_id, original_name=name,
            file_format=file_format, byte_size=len(data), sha256=body.sha256, storage_key=key,
            page_count=extracted.page_count, extraction_notes=extracted.notes)
        return await self.repository.mutate(user.id, crew_id, 'import_file', body, request_id, idempotency_key=idempotency_key,
            script_id=script_id, source_file=source, extracted=extracted)

    async def execute(self, user, action, request_id, *, crew_id, **options):
        try:
            require_completed_profile(user)
            if action == "read": return await self.repository.read(user.id, crew_id, **options)
            if action == 'source_file': return await self.repository.source_file(user.id, crew_id, **options)
            if action == 'import_file': return await self.import_file(user, crew_id, request_id=request_id, **options)
            return await self.repository.mutate(user.id, crew_id, action, options.pop("body"), request_id, **options)
        except (AuthError, DBAPIError, OSError, AccountUnavailable, ProfileIncomplete) as error:
            if isinstance(error, AccountUnavailable): error = AuthError(401, "SESSION_EXPIRED", "会话已失效")
            elif isinstance(error, ProfileIncomplete): error = AuthError(403, "PROFILE_INCOMPLETE", "请先补全昵称")
            code = error.code if isinstance(error, AuthError) else "SERVICE_UNAVAILABLE"
            await self.audits.record_failure(user.id, f"script.{action}", crew_id, "denied" if isinstance(error, AuthError) else "failed", code, request_id)
            if isinstance(error, AuthError): raise
            raise AuthError(503, "SERVICE_UNAVAILABLE", "剧本服务暂不可用，请读取当前状态") from None
