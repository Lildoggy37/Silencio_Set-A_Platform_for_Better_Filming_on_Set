from sqlalchemy.exc import DBAPIError

from silencio_set.accounts.errors import AuthError
from silencio_set.accounts.profile import (
    AccountUnavailable,
    ProfileIncomplete,
    require_completed_profile,
)


class CreativeService:
    def __init__(self, repository, audits):
        self.repository = repository
        self.audits = audits

    async def execute(self, user, action, request_id, *, crew_id, kind, **options):
        try:
            require_completed_profile(user)
            if action in {'source_assessment', 'source_impacts', 'source_history', 'source_review'}:
                from silencio_set.creative import source_reviews
                if action == 'source_review':
                    return await source_reviews.review(self.repository, user.id, crew_id, kind, options.pop('body'), request_id, **options)
                return await source_reviews.read(self.repository, user.id, crew_id, kind, history=action=='source_history', **options)
            if kind == 'casting':
                from silencio_set.creative import casting
                if action == 'read':
                    return await casting.read(self.repository, user.id, crew_id, **options)
                return await casting.save(self.repository, user.id, crew_id, options.pop('body'), request_id, **options)
            if action == 'read':
                return await self.repository.read(user.id, crew_id, kind, **options)
            return await self.repository.mutate(
                user.id, crew_id, kind, action, options.pop('body'), request_id, **options
            )
        except (AuthError, DBAPIError, AccountUnavailable, ProfileIncomplete) as error:
            if isinstance(error, AccountUnavailable):
                error = AuthError(401, 'SESSION_EXPIRED', '会话已失效')
            elif isinstance(error, ProfileIncomplete):
                error = AuthError(403, 'PROFILE_INCOMPLETE', '请先完善昵称')
            denied = isinstance(error, AuthError)
            await self.audits.record_failure(
                user.id, f'{kind}.{action}', crew_id,
                'denied' if denied else 'failed',
                error.code if denied else 'SERVICE_UNAVAILABLE', request_id,
            )
            if denied:
                raise error
            raise AuthError(503, 'SERVICE_UNAVAILABLE', '创作资料服务暂不可用') from None
