"""
middleware/csrf.py — Session CSRF tokens for browser POSTs.
"""
from __future__ import annotations

import secrets

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse

import config

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})
_CSRF_SESSION_KEY = "csrf_token"
_HEADER = "x-csrf-token"
_FORM_FIELD = "csrf_token"


def ensure_csrf_token(request: Request) -> str:
    """Return existing session CSRF token or create one."""
    token = request.session.get(_CSRF_SESSION_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        request.session[_CSRF_SESSION_KEY] = token
    return token


def _extract_token(request: Request, body_form_token: str | None = None) -> str | None:
    header = request.headers.get(_HEADER) or request.headers.get("X-CSRF-Token")
    if header:
        return header.strip()
    if body_form_token:
        return body_form_token.strip()
    return None


class CsrfMiddleware(BaseHTTPMiddleware):
    """
    Validates CSRF on unsafe methods when CSRF_ENABLED.
    Accepts header X-CSRF-Token or form field csrf_token.
    Skips /static and health.
    """

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path.startswith("/static") or path == "/api/health":
            return await call_next(request)

        # Always ensure token exists for HTML sessions (GET login, etc.)
        if request.method in _SAFE_METHODS:
            try:
                ensure_csrf_token(request)
            except AssertionError:
                pass
            return await call_next(request)

        if not getattr(config, "CSRF_ENABLED", True):
            return await call_next(request)

        try:
            expected = request.session.get(_CSRF_SESSION_KEY)
        except AssertionError:
            expected = None

        if not expected:
            # No session yet — create and reject (client must reload)
            try:
                ensure_csrf_token(request)
            except AssertionError:
                pass
            return self._reject(request, "CSRF token missing; refresh the page.")

        # Form-urlencoded: need to peek — Starlette consumes body once.
        # Prefer header for fetch(); for HTML forms, read form in route... 
        # Middleware can only reliably check header unless we buffer body.
        provided = request.headers.get(_HEADER) or request.headers.get("X-CSRF-Token")

        if not provided and request.headers.get("content-type", "").startswith(
            "application/x-www-form-urlencoded"
        ):
            # Buffer body for form CSRF, re-inject for downstream
            body = await request.body()
            from urllib.parse import parse_qs

            qs = parse_qs(body.decode("utf-8", errors="replace"), keep_blank_values=True)
            vals = qs.get(_FORM_FIELD) or qs.get("csrfmiddlewaretoken")
            provided = vals[0] if vals else None

            async def receive():
                return {"type": "http.request", "body": body, "more_body": False}

            request = Request(request.scope, receive)

        if not provided or not secrets.compare_digest(str(provided), str(expected)):
            return self._reject(request, "CSRF validation failed.")

        return await call_next(request)

    @staticmethod
    def _reject(request: Request, message: str):
        accept = request.headers.get("accept", "")
        if "application/json" in accept or request.url.path.startswith("/api/"):
            return JSONResponse({"detail": message}, status_code=403)
        return PlainTextResponse(message, status_code=403)
