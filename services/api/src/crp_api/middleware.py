"""Pure-ASGI middleware: request IDs, security headers and the loopback-only guard."""

from __future__ import annotations

import json
import uuid

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from crp_core.config import is_loopback_host

_SECURITY_HEADERS = [
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
    (b"cache-control", b"no-store"),
]


class RequestContextMiddleware:
    """Assign a request ID, expose it as ``X-Request-ID`` and add baseline security headers."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = uuid.uuid4().hex
        scope.setdefault("state", {})["request_id"] = request_id

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((b"x-request-id", request_id.encode()))
                existing = {name.lower() for name, _ in headers}
                headers.extend(h for h in _SECURITY_HEADERS if h[0] not in existing)
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_headers)


class LoopbackOnlyMiddleware:
    """Reject non-loopback peers and non-loopback Host headers (DNS-rebinding defence).

    Local-token authentication is only safe on a single-user loopback deployment; this guard keeps
    that true even if the process is accidentally reachable from another interface.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return
        client = scope.get("client")
        host_header = _header(scope, b"host")
        if client is None or not is_loopback_host(client[0]):
            await _reject(scope, send, "non_loopback_client", "Only loopback clients are allowed")
            return
        if host_header is None or not is_loopback_host(_strip_port(host_header)):
            await _reject(scope, send, "host_not_allowed", "Host header must name a loopback host")
            return
        await self.app(scope, receive, send)


def _header(scope: Scope, name: bytes) -> str | None:
    for key, value in scope.get("headers", []):
        if key.lower() == name:
            return str(value.decode("latin-1"))
    return None


def _strip_port(host: str) -> str:
    if host.startswith("["):
        return host[1 : host.find("]")] if "]" in host else host
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host


async def _reject(scope: Scope, send: Send, code: str, message: str) -> None:
    request_id = scope.get("state", {}).get("request_id", "unknown")
    body = json.dumps(
        {"code": code, "message": message, "request_id": request_id, "details": {}}
    ).encode()
    if scope["type"] == "websocket":
        await send({"type": "websocket.close", "code": 1008})
        return
    await send(
        {
            "type": "http.response.start",
            "status": 403,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", b"%d" % len(body)),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
