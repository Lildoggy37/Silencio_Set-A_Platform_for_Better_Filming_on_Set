import logging

from sqlalchemy.exc import DBAPIError

from silencio_set.accounts.errors import AuthError
from silencio_set.accounts.profile import AccountUnavailable, ProfileIncomplete, require_completed_profile

logger = logging.getLogger(__name__)


class NotificationService:
    def __init__(self, repository, audit_repository):
        self.repository = repository
        self.audit_repository = audit_repository

    async def execute(self, user, action, request_id, *, crew_id, notification_id=None, **options):
        try:
            require_completed_profile(user)
            if action == "list": return await self.repository.list(user.id, crew_id, **options)
            if action == "get": return await self.repository.get(user.id, crew_id, notification_id)
            return await self.repository.mark(user.id, crew_id, notification_id, action, request_id, **options)
        except (AuthError, AccountUnavailable, ProfileIncomplete, DBAPIError) as failure:
            if isinstance(failure, AccountUnavailable): failure = AuthError(401, "SESSION_EXPIRED", "会话已失效")
            elif isinstance(failure, ProfileIncomplete): failure = AuthError(403, "PROFILE_INCOMPLETE", "请先补全昵称")
            public = isinstance(failure, AuthError)
            try:
                await self.audit_repository.record_failure(user.id, f"notification.{action}", notification_id or crew_id,
                    "denied" if public and failure.status < 500 else "failed", failure.code if public else "SERVICE_UNAVAILABLE",
                    request_id, target_type="notification" if notification_id else "crew")
            except Exception:
                logger.error("notification_audit_failed request_id=%s action=%s", request_id, action)
            if public: raise failure
            raise AuthError(503, "SERVICE_UNAVAILABLE", "通知服务暂不可用，请稍后读取") from None
