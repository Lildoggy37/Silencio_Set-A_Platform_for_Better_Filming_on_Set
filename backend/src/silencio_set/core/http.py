import logging
import re
from http import HTTPStatus
from typing import Generic, TypeVar
from uuid import uuid4

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException
from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger(__name__)
T = TypeVar("T")


class Envelope(BaseModel, Generic[T]):
    data: T
    request_id: str


class ErrorBody(BaseModel):
    code: str
    message: str
    details: dict = Field(default_factory=dict)


class ErrorEnvelope(BaseModel):
    error: ErrorBody
    request_id: str


def error_response(request_id: str, status: int, code: str, message: str, details=None):
    return JSONResponse(
        status_code=status,
        headers={"Cache-Control": "no-store"},
        content={
            "error": {"code": code, "message": message, "details": details or {}},
            "request_id": request_id,
        },
    )


class RequestContextMiddleware:
    """Keep request IDs on success, validation failures and unexpected failures."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        supplied = dict(scope.get("headers", [])).get(b"x-request-id", b"").decode("latin-1")
        request_id = supplied if re.fullmatch(r"[a-f0-9]{32}", supplied) else uuid4().hex
        scope.setdefault("state", {})["request_id"] = request_id
        started = False

        async def traced_send(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                headers = [(k, v) for k, v in message["headers"] if k.lower() != b"x-request-id"]
                message["headers"] = headers + [(b"x-request-id", request_id.encode("ascii"))]
            await send(message)

        try:
            await self.app(scope, receive, traced_send)
        except Exception as exc:
            # Do not log bodies, credentials, SQL or external exception messages.
            logger.error("request_failed request_id=%s exception_type=%s", request_id, type(exc).__name__)
            if started:
                raise
            response = error_response(request_id, 500, "INTERNAL_ERROR", "服务内部错误，请稍后重试")
            await response(scope, receive, traced_send)


async def http_exception_handler(request: Request, exc: HTTPException):
    codes = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED", 401: "UNAUTHENTICATED", 403: "FORBIDDEN"}
    response = error_response(
        request.state.request_id,
        exc.status_code,
        codes.get(exc.status_code, "HTTP_ERROR"),
        HTTPStatus(exc.status_code).phrase,
    )
    if exc.headers:
        response.headers.update(exc.headers)
    return response


async def validation_exception_handler(request: Request, exc: RequestValidationError):
    # Omit rejected input, validators' contexts and dynamic error messages.
    issues = [{"location": list(e["loc"]), "type": e["type"]} for e in exc.errors()]
    return error_response(
        request.state.request_id, 422, "VALIDATION_ERROR", "请求参数不符合要求", {"issues": issues}
    )
