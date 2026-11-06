from sqlalchemy.exc import DBAPIError

from silencio_set.accounts.errors import AuthError
from silencio_set.accounts.profile import require_completed_profile, AccountUnavailable, ProfileIncomplete


class PlanService:
    def __init__(self, repository, audits):
        self.repository = repository
        self.audits = audits

    async def execute(self, user, action, request_id, *, crew_id, **options):
        try:
            require_completed_profile(user)
            if action in {'read', 'choices', 'preflight', 'next', 'get_plan'}:
                return await getattr(self.repository, action)(user.id, crew_id, **options)
            if action == 'publish':
                from silencio_set.plans.publication import publish
                return await publish(self.repository, user.id, crew_id, options['version_id'], options['body'], request_id, options['idempotency_key'])
            if action == 'withdraw':
                from silencio_set.plans.withdrawal import withdraw
                return await withdraw(self.repository, user.id, crew_id, options['version_id'], options['body'], request_id, options['idempotency_key'])
            return await self.repository.mutate(user.id, crew_id, action, options.pop('body'), request_id, **options)
        except (AuthError, DBAPIError, AccountUnavailable, ProfileIncomplete) as error:
            if isinstance(error, AccountUnavailable): error = AuthError(401, 'SESSION_EXPIRED', '会话已失效')
            elif isinstance(error, ProfileIncomplete): error = AuthError(403, 'PROFILE_INCOMPLETE', '请先完善昵称')
            await self.audits.record_failure(user.id, f'plan.{action}', crew_id, 'denied' if isinstance(error, AuthError) else 'failed', error.code if isinstance(error, AuthError) else 'SERVICE_UNAVAILABLE', request_id)
            if isinstance(error, AuthError): raise error
            raise AuthError(503, 'SERVICE_UNAVAILABLE', '拍摄计划服务暂不可用') from None
