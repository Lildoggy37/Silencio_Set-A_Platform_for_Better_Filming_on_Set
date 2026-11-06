from sqlalchemy.exc import DBAPIError
import anyio

from silencio_set.accounts.errors import AuthError
from silencio_set.accounts.profile import require_completed_profile, AccountUnavailable, ProfileIncomplete
from silencio_set.call_sheets.pdf import render_in_process


class CallSheetService:
    def __init__(self, repository, audits):
        self.repository, self.audits = repository, audits
        self.pdf_limiter = anyio.CapacityLimiter(2)

    async def execute(self, user, action, request_id, *, crew_id, **options):
        try:
            require_completed_profile(user)
            if action == 'export_pdf':
                snapshot = await self.repository.export_snapshot(user.id, crew_id, **options)
                content = await anyio.to_thread.run_sync(render_in_process, snapshot, limiter=self.pdf_limiter)
                filename = f'SilencioSet-{snapshot.version.call_sheet.shooting_date}-v{snapshot.version.version_no}-{str(snapshot.version.id)[:8]}.pdf'
                return content, filename
            if action in {'read', 'recipient_options'}:
                return await getattr(self.repository, action)(user.id, crew_id, **options)
            return await self.repository.mutate(user.id, crew_id, action, options.pop('body'), request_id, **options)
        except (AuthError, DBAPIError, OSError, AccountUnavailable, ProfileIncomplete) as error:
            if isinstance(error, AccountUnavailable): error = AuthError(401, 'SESSION_EXPIRED', '会话已失效')
            elif isinstance(error, ProfileIncomplete): error = AuthError(403, 'PROFILE_INCOMPLETE', '请先完善昵称')
            elif isinstance(error, OSError): error = AuthError(503, 'PDF_EXPORT_FAILED', 'PDF生成失败，请保留网页版并稍后重试')
            await self.audits.record_failure(user.id, f'call_sheet.{action}', crew_id,
                'denied' if isinstance(error, AuthError) else 'failed', error.code if isinstance(error, AuthError) else 'SERVICE_UNAVAILABLE', request_id)
            if isinstance(error, AuthError): raise error
            raise AuthError(503, 'SERVICE_UNAVAILABLE', '通告服务暂不可用') from None
