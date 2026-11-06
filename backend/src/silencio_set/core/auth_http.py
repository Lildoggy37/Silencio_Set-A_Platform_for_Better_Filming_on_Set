from starlette.types import ASGIApp, Receive, Scope, Send

from silencio_set.core.http import error_response


class AuthRequestMiddleware:
    """Bound auth request bodies and mark all auth responses non-cacheable."""
    def __init__(self, app: ASGIApp, max_bytes: int = 16_384):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http" or not (
            scope["path"].startswith("/api/v1/auth/") or scope["path"].startswith("/api/v1/users/me")
            or scope["path"] == "/api/v1/crews" or scope["path"].startswith("/api/v1/crews/")
            or scope["path"].startswith("/api/v1/invitations/")
        ):
            await self.app(scope, receive, send)
            return

        async def private_send(message):
            if message["type"] == "http.response.start":
                overrides = {b"cache-control", b"pragma", b"referrer-policy", b"x-content-type-options"}
                headers = [(k, v) for k, v in message["headers"] if k.lower() not in overrides]
                message["headers"] = headers + [
                    (b"cache-control", b"no-store"), (b"pragma", b"no-cache"),
                    (b"referrer-policy", b"no-referrer"), (b"x-content-type-options", b"nosniff"),
                ]
            await send(message)

        if scope["method"] not in {"POST", "PATCH", "PUT"}:
            await self.app(scope, receive, private_send)
            return
        messages = []
        size = 0
        parts = scope["path"].split("/")
        is_import = len(parts) == 8 and parts[5] == 'scripts' and parts[7] == 'imports'
        is_photo_upload = len(parts) == 8 and parts[5] == 'locations' and parts[7] == 'photos'
        max_bytes = 2 * 1024 * 1024 if len(parts) > 5 and parts[5] in {"scripts", "breakdowns", "storyboards", "plan-versions", "call-sheet-versions"} else self.max_bytes
        if is_import: max_bytes = 20 * 1024 * 1024 + 65536
        if is_photo_upload: max_bytes = 10 * 1024 * 1024 + 65536
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            size += len(message.get("body", b""))
            if size > max_bytes:
                response = error_response(scope["state"]["request_id"], 413, "PAYLOAD_TOO_LARGE", "请求内容超过限制")
                await response(scope, receive, private_send)
                return
            messages.append(message)
            if not message.get("more_body", False):
                break
        content_type = dict(scope.get("headers", [])).get(b"content-type", b"").split(b";", 1)[0].strip().lower()
        expected_type = b'multipart/form-data' if is_import or is_photo_upload else b'application/json'
        if size and content_type != expected_type:
            response = error_response(scope["state"]["request_id"], 415, "UNSUPPORTED_MEDIA_TYPE", f"请求须使用{expected_type.decode('ascii')}")
            await response(scope, receive, private_send)
            return

        async def replay():
            return messages.pop(0) if messages else await receive()

        await self.app(scope, replay, private_send)
