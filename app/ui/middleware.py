"""UI response protections and bounded request buffering before multipart parsing."""

from __future__ import annotations

from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

MAX_BODY_BYTES = 6 * 1024 * 1024
_HEADERS = {
    "content-security-policy": "default-src 'self'; script-src 'self'; style-src 'self'; "
    "img-src 'self'; object-src 'none'; base-uri 'none'; "
    "frame-ancestors 'none'; form-action 'self'",
    "x-content-type-options": "nosniff",
    "referrer-policy": "no-referrer",
    "cache-control": "no-store",
    "x-frame-options": "DENY",
}


class UiSecurityMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith("/ui"):
            await self.app(scope, receive, send)
            return

        async def secured(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = [
                    (key, value)
                    for key, value in message.get("headers", [])
                    if key.decode().lower() not in _HEADERS
                ]
                headers.extend((key.encode(), value.encode()) for key, value in _HEADERS.items())
                message = {**message, "headers": headers}
            await send(message)

        if scope["method"] not in {"POST", "PUT", "PATCH"}:
            await self.app(scope, receive, secured)
            return
        chunks = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunks.extend(message.get("body", b""))
            if len(chunks) > MAX_BODY_BYTES:
                await PlainTextResponse("Request exceeds the UI limit.", status_code=413)(
                    scope, receive, secured
                )
                return
            if not message.get("more_body", False):
                break
        consumed = False

        async def buffered() -> Message:
            nonlocal consumed
            if consumed:
                return await receive()
            consumed = True
            return {"type": "http.request", "body": bytes(chunks), "more_body": False}

        await self.app(scope, buffered, secured)
