import logging

from sqlalchemy.exc import DBAPIError

from silencio_set.accounts.errors import AuthError
from silencio_set.accounts.profile import AccountUnavailable, ProfileIncomplete, require_completed_profile

logger = logging.getLogger(__name__)


class CrewService:
    def __init__(self, repository, organization=None):
        self.repository = repository
        self.organization = organization

    async def execute(self, user, action, request_id, *, crew_id=None, member_id=None, body=None, page=1, page_size=20):
        try:
            # Reused by HTTP today and future internal tools; no client role claims.
            require_completed_profile(user)
            if action == "crew.create":
                return await self.repository.create(user.id, body, request_id)
            if action == "crew.list":
                return await self.repository.list(user.id, page, page_size)
            if action == "crew.read":
                return await self.repository.get(user.id, crew_id)
            if action == "member.list":
                return await self.repository.members(user.id, crew_id, page, page_size)
            if action == "member.roles.replace":
                if self.organization is None:
                    raise AuthError(503, "SERVICE_UNAVAILABLE", "岗位服务暂不可用")
                return await self.organization.replace_roles(user.id, crew_id, member_id, body, request_id)
            if action in {"member.leave", "member.remove"}:
                if self.organization is None:
                    raise AuthError(503, "SERVICE_UNAVAILABLE", "成员服务暂不可用")
                return await self.organization.end_membership(user.id, crew_id,
                    member_id if action == "member.remove" else None, body, request_id)
            raise ValueError("Unsupported internal crew operation")
        except (AuthError, AccountUnavailable, ProfileIncomplete, DBAPIError) as failure:
            if isinstance(failure, AccountUnavailable):
                failure = AuthError(401, "SESSION_EXPIRED", "会话无效或已过期，请重新登录")
            elif isinstance(failure, ProfileIncomplete):
                failure = AuthError(403, "PROFILE_INCOMPLETE", "请先补全昵称再进入剧组业务")
            code = failure.code if isinstance(failure, AuthError) else "SERVICE_UNAVAILABLE"
            try:
                await self.repository.record_failure(user.id, action, member_id or crew_id,
                    "denied" if isinstance(failure, AuthError) else "failed", code, request_id,
                    **({"target_type": "crew_member"} if member_id is not None else {}))
            except Exception:
                logger.error("crew_audit_failed request_id=%s action=%s", request_id, action)
            if isinstance(failure, AuthError):
                raise failure
            raise AuthError(503, "SERVICE_UNAVAILABLE", "剧组服务暂不可用，请稍后检查结果") from None
