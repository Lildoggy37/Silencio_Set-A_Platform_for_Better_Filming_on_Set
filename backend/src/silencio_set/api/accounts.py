from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field

from silencio_set.accounts.cache import BrowserSession
from silencio_set.accounts.errors import AuthError, expired_session, unavailable
from silencio_set.accounts.schemas import (
    EmailVerificationRequest, LoginRequest, ProfileUpdate, ProfileView,
    RegistrationRequest, UserView, VerificationInput,
)
from silencio_set.accounts.service import AuthService
from silencio_set.accounts.profile import AccountUnavailable, ProfileIncomplete, require_completed_profile
from silencio_set.core.http import Envelope, ErrorEnvelope, error_response

router = APIRouter(prefix="/api/v1", tags=["accounts"], responses={
    code: {"model": ErrorEnvelope} for code in (400, 401, 403, 409, 413, 415, 422, 429, 500, 503)
})
SESSION_COOKIE = "silencio_session"
COOKIE_PATH = "/api/v1"


class CsrfView(BaseModel):
    csrf_token: str = Field(repr=False)


class VerificationView(BaseModel):
    registration_token: str = Field(repr=False)
    expires_at: datetime


class RegistrationView(BaseModel):
    user_id: UUID
    registered: bool = True


class MailRequestView(BaseModel):
    verification_id: UUID
    expires_at: datetime
    delivery_status: str = "pending_dispatch"
    resend_after_seconds: int


async def auth_service(request: Request) -> AuthService:
    service = getattr(request.app.state, "auth", None)
    if service is None:
        raise unavailable()
    return service


def client_ip(request: Request) -> str:
    # No custom trust of X-Forwarded-For. Run uvicorn with --no-proxy-headers
    # until a trusted reverse proxy is explicitly configured.
    return request.client.host if request.client is not None else "unknown"


def check_origin(request: Request):
    if request.headers.get("origin") not in request.app.state.settings.auth_allowed_origins:
        raise AuthError(403, "ORIGIN_NOT_ALLOWED", "请求来源不被允许")


async def csrf_browser(request: Request, service: AuthService = Depends(auth_service)) -> BrowserSession:
    check_origin(request)
    return await service.cache.validate_csrf(request.cookies.get(SESSION_COOKIE), request.headers.get("x-csrf-token"))


async def completed_user(request: Request, service: AuthService = Depends(auth_service)):
    """Future business routers must use this gate, then crew/object authorization."""
    user = await service.current_user(request.cookies.get(SESSION_COOKIE))
    try:
        require_completed_profile(user)
    except AccountUnavailable:
        raise expired_session() from None
    except ProfileIncomplete:
        raise AuthError(403, "PROFILE_INCOMPLETE", "请先补全昵称再进入剧组业务") from None
    return user


def set_session_cookie(response: Response, browser: BrowserSession, request: Request):
    response.set_cookie(
        SESSION_COOKIE, browser.token.get_secret_value(), max_age=browser.max_age,
        httponly=True, secure=request.app.state.settings.auth_cookie_secure,
        samesite="lax", path=COOKIE_PATH,
    )


async def auth_exception_handler(request: Request, exc: AuthError):
    response = error_response(request.state.request_id, exc.status, exc.code, exc.public_message)
    if exc.retry_after is not None:
        response.headers["Retry-After"] = str(exc.retry_after)
    return response


@router.get("/auth/csrf", response_model=Envelope[CsrfView])
async def csrf(request: Request, response: Response, service: AuthService = Depends(auth_service)):
    if request.headers.get("origin") is not None:
        check_origin(request)
    ip = client_ip(request)
    await service.cache.limit("csrf", ip, ip, ip_limit=60, identity_limit=60)
    existing = request.cookies.get(SESSION_COOKIE)
    browser = await service.cache.prelogin(existing)
    if existing != browser.token.get_secret_value():
        set_session_cookie(response, browser, request)
    return Envelope(data=CsrfView(csrf_token=service.cache.csrf(browser.token.get_secret_value())), request_id=request.state.request_id)


@router.post("/auth/email-verifications", status_code=202, response_model=Envelope[MailRequestView])
async def request_verification(body: EmailVerificationRequest, request: Request, browser: BrowserSession = Depends(csrf_browser)):
    coordinator = getattr(request.app.state, "mail_requests", None)
    if coordinator is None:
        raise AuthError(503, "EMAIL_SERVICE_UNAVAILABLE", "邮件发送服务未启用，暂不能申请验证码")
    acceptance = await coordinator.request(body.email, client_ip(request))
    return Envelope(data=MailRequestView(verification_id=acceptance.verification_id,
        expires_at=datetime.fromtimestamp(acceptance.expires_at, UTC),
        resend_after_seconds=coordinator.cache.settings.auth_resend_interval), request_id=request.state.request_id)


@router.post("/auth/email-verifications/verify", response_model=Envelope[VerificationView])
async def verify_email(body: VerificationInput, request: Request, browser: BrowserSession = Depends(csrf_browser),
                       service: AuthService = Depends(auth_service)):
    exchanged = await service.cache.exchange(body.email, body.verification_id, body.code.get_secret_value(), client_ip(request))
    return Envelope(data=VerificationView(
        registration_token=exchanged.token.get_secret_value(),
        expires_at=datetime.fromtimestamp(exchanged.grant.expires_at, UTC),
    ), request_id=request.state.request_id)


@router.post("/auth/register", response_model=Envelope[RegistrationView], status_code=201,
             responses={200: {"description": "Registration already committed for this token"}})
async def register(body: RegistrationRequest, request: Request, response: Response,
                   browser: BrowserSession = Depends(csrf_browser), service: AuthService = Depends(auth_service)):
    outcome = await service.register(body, client_ip(request))
    response.status_code = 201 if outcome.created else 200
    return Envelope(data=RegistrationView(user_id=outcome.user_id), request_id=request.state.request_id)


@router.post("/auth/login", response_model=Envelope[UserView])
async def login(body: LoginRequest, request: Request, response: Response,
                browser: BrowserSession = Depends(csrf_browser), service: AuthService = Depends(auth_service)):
    user, new_session = await service.login(body, browser, client_ip(request))
    set_session_cookie(response, new_session, request)
    return Envelope(data=user, request_id=request.state.request_id)


@router.post("/auth/logout", status_code=204)
async def logout(request: Request, response: Response, browser: BrowserSession = Depends(csrf_browser),
                 service: AuthService = Depends(auth_service)):
    await service.cache.logout(browser)
    response.delete_cookie(SESSION_COOKIE, path=COOKIE_PATH,
                           secure=request.app.state.settings.auth_cookie_secure, httponly=True, samesite="lax")
    response.status_code = 204


@router.get("/users/me", response_model=Envelope[UserView])
async def me(request: Request, service: AuthService = Depends(auth_service)):
    user = await service.current_user(request.cookies.get(SESSION_COOKIE))
    return Envelope(data=UserView.model_validate(user), request_id=request.state.request_id)


@router.patch("/users/me/profile", response_model=Envelope[ProfileView])
async def profile(body: ProfileUpdate, request: Request, browser: BrowserSession = Depends(csrf_browser),
                  service: AuthService = Depends(auth_service)):
    view = await service.update_profile(request.cookies.get(SESSION_COOKIE), body)
    return Envelope(data=view, request_id=request.state.request_id)
