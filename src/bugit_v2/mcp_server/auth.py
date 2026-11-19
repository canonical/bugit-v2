"""Static API key authentication. Pure ASGI so SSE streaming isn't buffered."""

import hmac

from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send


class BearerAuthMiddleware:
    def __init__(self, app: ASGIApp, api_key: str | None) -> None:
        if api_key is None or not api_key.strip():
            raise ValueError("An MCP API key is required; refusing to start without one")
        self.app = app
        self._key = api_key.strip().encode()

    def _authorized(self, scope: Scope) -> bool:
        for name, value in scope.get("headers", []):
            if name == b"authorization":
                scheme, _, token = value.partition(b" ")
                return scheme.lower() == b"bearer" and hmac.compare_digest(
                    token.strip(), self._key
                )
        return False

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and not self._authorized(scope):
            response = PlainTextResponse(
                "Unauthorized",
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)
