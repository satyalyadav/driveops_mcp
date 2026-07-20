"""Security primitives for internet-facing Streamable HTTP deployments."""

from __future__ import annotations

import hmac
import json
import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable
from typing import Any

from mcp.server.auth.provider import AccessToken
from starlette.types import ASGIApp, Message, Receive, Scope, Send


def secret_is_strong(value: str | None) -> bool:
    """Require enough material for generated bearer tokens/access keys."""

    return value is not None and len(value.encode("utf-8")) >= 32


class StaticTokenVerifier:
    """Verify one deployment-scoped bearer token without leaking timing data."""

    def __init__(self, token: str) -> None:
        if not secret_is_strong(token):
            raise ValueError("DRIVEOPS_MCP_AUTH_TOKEN must be at least 32 bytes.")
        self._token = token.encode("utf-8")

    async def verify_token(self, token: str) -> AccessToken | None:
        candidate = token.encode("utf-8")
        if not hmac.compare_digest(candidate, self._token):
            return None
        return AccessToken(
            token=token,
            client_id="driveops-static-client",
            scopes=["driveops"],
            subject="owner",
            claims={"iss": "driveops-static"},
        )


async def _json_response(
    send: Send,
    status: int,
    payload: dict[str, Any],
    headers: list[tuple[bytes, bytes]] | None = None,
) -> None:
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    response_headers = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode("ascii")),
        (b"cache-control", b"no-store"),
    ]
    response_headers.extend(headers or [])
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": response_headers,
        }
    )
    await send({"type": "http.response.body", "body": body})


class RequestBodyLimitMiddleware:
    """Buffer and reject oversized HTTP request bodies before MCP parsing."""

    def __init__(self, app: ASGIApp, max_bytes: int = 2_000_000) -> None:
        self.app = app
        self.max_bytes = max(1, int(max_bytes))

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method") in {"GET", "HEAD"}:
            await self.app(scope, receive, send)
            return

        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        raw_length = headers.get(b"content-length")
        if raw_length:
            try:
                if int(raw_length) > self.max_bytes:
                    await _json_response(send, 413, {"error": "request_too_large"})
                    return
            except ValueError:
                await _json_response(send, 400, {"error": "invalid_content_length"})
                return

        messages: list[Message] = []
        size = 0
        while True:
            message = await receive()
            messages.append(message)
            if message["type"] == "http.disconnect":
                break
            if message["type"] == "http.request":
                size += len(message.get("body", b""))
                if size > self.max_bytes:
                    await _json_response(send, 413, {"error": "request_too_large"})
                    return
                if not message.get("more_body", False):
                    break

        index = 0

        async def replay() -> Message:
            nonlocal index
            if index < len(messages):
                message = messages[index]
                index += 1
                return message
            return await receive()

        await self.app(scope, replay, send)


class RateLimitMiddleware:
    """Small in-process per-peer limiter; hosting edge limits should supplement it."""

    def __init__(
        self,
        app: ASGIApp,
        requests: int = 120,
        window_seconds: int = 60,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.app = app
        self.requests = max(1, int(requests))
        self.window_seconds = max(1, int(window_seconds))
        self.clock = clock
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def _allowed(self, peer: str) -> tuple[bool, int]:
        now = self.clock()
        cutoff = now - self.window_seconds
        with self._lock:
            if peer not in self._hits and len(self._hits) >= 10_000:
                self._hits.pop(next(iter(self._hits)))
            hits = self._hits[peer]
            while hits and hits[0] <= cutoff:
                hits.popleft()
            if len(hits) >= self.requests:
                retry_after = max(1, int(self.window_seconds - (now - hits[0])))
                return False, retry_after
            hits.append(now)
            return True, 0

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        client = scope.get("client")
        peer = str(client[0]) if client else "unknown"
        allowed, retry_after = self._allowed(peer)
        if not allowed:
            await _json_response(
                send,
                429,
                {"error": "rate_limit_exceeded"},
                [(b"retry-after", str(retry_after).encode("ascii"))],
            )
            return
        await self.app(scope, receive, send)


class SecurityHeadersMiddleware:
    """Attach conservative browser and proxy security headers."""

    def __init__(self, app: ASGIApp, *, https: bool) -> None:
        self.app = app
        self.https = https

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                present = {key.lower() for key, _ in headers}
                additions = [
                    (b"cache-control", b"no-store"),
                    (b"x-content-type-options", b"nosniff"),
                    (b"referrer-policy", b"no-referrer"),
                    (b"x-frame-options", b"DENY"),
                    (
                        b"permissions-policy",
                        b"camera=(), microphone=(), geolocation=()",
                    ),
                    (
                        b"content-security-policy",
                        b"default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'",
                    ),
                ]
                if self.https:
                    additions.append(
                        (
                            b"strict-transport-security",
                            b"max-age=31536000; includeSubDomains",
                        )
                    )
                headers.extend(
                    (key, value) for key, value in additions if key not in present
                )
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_headers)


def add_http_security_middleware(
    app: ASGIApp,
    *,
    https: bool,
    max_request_bytes: int,
    rate_limit_requests: int,
    rate_limit_window_seconds: int = 60,
) -> ASGIApp:
    """Compose the public HTTP safeguards around the MCP Starlette app."""

    secured: ASGIApp = app
    secured = RequestBodyLimitMiddleware(secured, max_request_bytes)
    secured = RateLimitMiddleware(
        secured,
        requests=rate_limit_requests,
        window_seconds=rate_limit_window_seconds,
    )
    secured = SecurityHeadersMiddleware(secured, https=https)
    return secured
