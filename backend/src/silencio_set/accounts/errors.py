class AuthError(Exception):
    def __init__(self, status: int, code: str, message: str, *, retry_after: int | None = None):
        super().__init__(code)
        self.status = status
        self.code = code
        self.public_message = message
        self.retry_after = retry_after


def unavailable() -> AuthError:
    return AuthError(503, "AUTH_SERVICE_UNAVAILABLE", "认证服务暂不可用，请稍后重试")


def invalid_grant() -> AuthError:
    return AuthError(400, "REGISTRATION_TOKEN_INVALID", "注册凭证无效或已过期，请重新验证邮箱")


def expired_session() -> AuthError:
    return AuthError(401, "SESSION_EXPIRED", "会话无效或已过期，请重新登录")
