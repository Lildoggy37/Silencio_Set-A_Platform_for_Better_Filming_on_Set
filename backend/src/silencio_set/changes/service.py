from sqlalchemy.exc import DBAPIError
from silencio_set.accounts.errors import AuthError
from silencio_set.accounts.profile import require_completed_profile, AccountUnavailable, ProfileIncomplete


class ChangeService:
    def __init__(self, repository, audits): self.repository, self.audits = repository, audits

    async def list(self, user, crew_id, request_id, **options):
        return await self.execute(user,'list',crew_id,request_id,**options)

    async def execute(self,user,action,crew_id,request_id,**options):
        try:
            require_completed_profile(user)
            if action=='list': return await self.repository.list(user.id,crew_id,**options)
            if action=='check':
                from silencio_set.changes.checking import check
                return await check(self.repository,user.id,crew_id,options.pop('category'),options.pop('body'),request_id,options.pop('idempotency_key'))
            from silencio_set.changes import history
            return await getattr(history,action)(self.repository,user.id,crew_id,**options)
        except (AuthError, AccountUnavailable, ProfileIncomplete, DBAPIError) as error:
            if isinstance(error, AccountUnavailable): error = AuthError(401, 'SESSION_EXPIRED', '会话已失效')
            elif isinstance(error, ProfileIncomplete): error = AuthError(403, 'PROFILE_INCOMPLETE', '请先完善昵称')
            denied = isinstance(error, AuthError)
            await self.audits.record_failure(user.id, f'changes.{action}', crew_id, 'denied' if denied else 'failed',
                error.code if denied else 'SERVICE_UNAVAILABLE', request_id)
            if denied: raise error
            raise AuthError(503, 'SERVICE_UNAVAILABLE', '变更清单暂不可用') from None
