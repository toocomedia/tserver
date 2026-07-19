"""
middleware/csrf.py — Session CSRF tokens for browser POSTs.

Uses pure ASGI (not BaseHTTPMiddleware) so request body and session
updates stay reliable under Starlette/FastAPI.
"""
from __future__ import annotations

import secrets
from urllib.parse import parse_qs

from starlette.datastructures import MutableHeaders
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

import config

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})
_CSRF_SESSION_KEY = "csrf_token"
_FORM_FIELD = "csrf_token"
_HEADER_NAMES = ("x-csrf-token", "x-csrftoken")
_COOKIE_NAME = "csrf_token"


def ensure_csrf_token(request: Request) -> str:
    """Return existing session CSRF token or create one."""
    token = request.session.get(_CSRF_SESSION_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        request.session[_CSRF_SESSION_KEY] = token
    return token


def _header_token(scope: Scope) -> str | None:
    headers = {
        k.decode("latin-1").lower(): v.decode("latin-1")
        for k, v in scope.get("headers", [])
    }
    for name in _HEADER_NAMES:
        if name in headers and headers[name].strip():
            return headers[name].strip()
    return None


def _content_type(scope: Scope) -> str:
    for k, v in scope.get("headers", []):
        if k.decode("latin-1").lower() == "content-type":
            return v.decode("latin-1").lower()
    return ""


def _tokens_match(provided: str | None, expected: str | None) -> bool:
    if not provided or not expected:
        return False
    try:
        return secrets.compare_digest(str(provided), str(expected))
    except (TypeError, ValueError):
        return False


def _reject(scope: Scope, message: str) -> JSONResponse | PlainTextResponse:
    path = scope.get("path", "") or ""
    headers = {
        k.decode("latin-1").lower(): v.decode("latin-1")
        for k, v in scope.get("headers", [])
    }
    accept = headers.get("accept", "")
    if "application/json" in accept or path.startswith("/api/"):
        return JSONResponse({"detail": message}, status_code=403)
    return PlainTextResponse(message, status_code=403)


class CsrfMiddleware:
    """
    Validates CSRF on unsafe methods when CSRF_ENABLED.
    Accepts:
      - Header X-CSRF-Token / X-CSRFToken
      - Form field csrf_token (urlencoded or multipart)
    Ensures a session token exists on safe methods and sets a readable
    csrf_token cookie so JS can always attach the header.
    """

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "") or ""
        method = (scope.get("method") or "GET").upper()

        if path.startswith("/static") or path == "/api/health":
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive)

        # ---- Safe methods: ensure token, continue ----
        if method in _SAFE_METHODS:
            try:
                ensure_csrf_token(request)
            except AssertionError:
                pass
            await self._call_with_csrf_cookie(scope, receive, send, request)
            return

        if not getattr(config, "CSRF_ENABLED", True):
            await self.app(scope, receive, send)
            return

        try:
            expected = request.session.get(_CSRF_SESSION_KEY)
        except AssertionError:
            expected = None

        if not expected:
            try:
                expected = ensure_csrf_token(request)
            except AssertionError:
                expected = None
            if not expected:
                response = _reject(scope, "CSRF token missing; refresh the page.")
                await response(scope, receive, send)
                return
            # Token was just created — client could not have sent it
            response = _reject(scope, "CSRF token missing; refresh the page.")
            await response(scope, receive, send)
            return

        provided = _header_token(scope)

        # Buffer body once so we can parse form tokens and still pass body downstream
        body = b""
        more = True
        while more:
            message = await receive()
            if message["type"] != "http.request":
                continue
            body += message.get("body", b"")
            more = message.get("more_body", False)

        if not provided:
            ctype = _content_type(scope)
            if "application/x-www-form-urlencoded" in ctype:
                qs = parse_qs(body.decode("utf-8", errors="replace"), keep_blank_values=True)
                vals = qs.get(_FORM_FIELD) or qs.get("csrfmiddlewaretoken")
                provided = vals[0] if vals else None
            elif "multipart/form-data" in ctype:
                # Lightweight parse: look for name="csrf_token" part
                provided = _multipart_field(body, _FORM_FIELD)

        # Note: csrf_token cookie is for JS to read and put in form/header only.
        # Never accept the cookie alone as proof (would disable CSRF).

        if not _tokens_match(provided, expected):
            response = _reject(scope, "CSRF validation failed.")
            await response(scope, receive, send)
            return

        async def receive_replay() -> Message:
            return {"type": "http.request", "body": body, "more_body": False}

        await self._call_with_csrf_cookie(scope, receive_replay, send, request)

    async def _call_with_csrf_cookie(
        self, scope: Scope, receive: Receive, send: Send, request: Request
    ) -> None:
        try:
            token = ensure_csrf_token(request)
        except AssertionError:
            token = ""

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start" and token:
                headers = MutableHeaders(scope=message)
                # Readable by JS (not HttpOnly). Path=/ so all forms/fetch can use it.
                secure = " Secure" if getattr(config, "SESSION_HTTPS_ONLY", False) else ""
                headers.append(
                    "Set-Cookie",
                    f"{_COOKIE_NAME}={token}; Path=/; SameSite=Lax{secure}",
                )
            await send(message)

        await self.app(scope, receive, send_wrapper)


def _multipart_field(body: bytes, field_name: str) -> str | None:
    """Best-effort extract of a simple text field from multipart body."""
    try:
        text = body.decode("utf-8", errors="replace")
    except Exception:
        return None
    marker = f'name="{field_name}"'
    idx = text.find(marker)
    if idx < 0:
        marker = f"name='{field_name}'"
        idx = text.find(marker)
    if idx < 0:
        return None
    # After headers blank line
    rest = text[idx + len(marker) :]
    # Skip to double newline
    sep = rest.find("\r\n\r\n")
    if sep < 0:
        sep = rest.find("\n\n")
        if sep < 0:
            return None
        rest = rest[sep + 2 :]
    else:
        rest = rest[sep + 4 :]
    # Value until next boundary
    end = rest.find("\r\n--")
    if end < 0:
        end = rest.find("\n--")
    if end < 0:
        end = len(rest)
    return rest[:end].strip() or None
