import logging

from sqlalchemy.exc import DBAPIError

from silencio_set.accounts.errors import AuthError
from silencio_set.accounts.profile import AccountUnavailable, ProfileIncomplete, require_completed_profile

logger = logging.getLogger(__name__)


class TaskService:
    def __init__(self, repository, audit_repository):
        self.repository = repository
        self.audit_repository = audit_repository

    async def execute(self, user, action, request_id, *, crew_id, task_id=None, body=None, **options):
        try:
            require_completed_profile(user)
            if action == "list": return await self.repository.list(user.id, crew_id, **options)
            if action == "read": return await self.repository.get(user.id, crew_id, task_id, **options)
            if action == "responsibilities": return await self.repository.responsibilities(user.id, crew_id, **options)
            return await self.repository.mutate(user.id, crew_id, action, body, request_id, task_id=task_id, **options)
        except (AuthError, AccountUnavailable, ProfileIncomplete, DBAPIError) as failure:
            if isinstance(failure, AccountUnavailable): failure = AuthError(401, "SESSION_EXPIRED", "会话已失效")
            elif isinstance(failure, ProfileIncomplete): failure = AuthError(403, "PROFILE_INCOMPLETE", "请先补全昵称")
            is_public = isinstance(failure, AuthError)
            code = failure.code if is_public else "SERVICE_UNAVAILABLE"
            try:
                await self.audit_repository.record_failure(user.id, f"task.{action}", task_id or options.get("member_id") or crew_id,
                    "denied" if is_public and failure.status < 500 else "failed", code, request_id,
                    target_type="task" if task_id else "crew_member" if "member_id" in options else "crew")
            except Exception:
                logger.error("task_audit_failed request_id=%s action=%s", request_id, action)
            if is_public: raise failure
            raise AuthError(503, "SERVICE_UNAVAILABLE", "任务服务暂不可用，请先读取当前状态") from None
