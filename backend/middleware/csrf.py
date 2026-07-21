"""
middleware/csrf.py — Session synchronizer-token CSRF for state-changing requests.
"""
from __future__ import annotations

import hmac
import re
import secrets
from urllib.parse import parse_qs

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request as StarletteRequest

from middleware.auth import wants_json

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})
_SESSION_KEY = "csrf_token"
_HEADER_NAMES = ("x-csrf-token", "x-csrftoken")
_MULTIPART_TOKEN = re.compile(
    r'name=["\']csrf_token["\']\r?\n\r?\n([^\r\n]+)',
    re.IGNORECASE,
)


def ensure_csrf_token(request: Request) -> str:
    """Return existing session CSRF token or create one."""
    token = request.session.get(_SESSION_KEY)
    if not token or not isinstance(token, str):
        token = secrets.token_urlsafe(32)
        request.session[_SESSION_KEY] = token
    return token


def _tokens_match(expected: str, provided: str | None) -> bool:
    if not provided or not expected:
        return False
    try:
        return hmac.compare_digest(expected, provided)
    except (TypeError, ValueError):
        return False


def _header_token(request: Request) -> str | None:
    for name in _HEADER_NAMES:
        val = request.headers.get(name)
        if val:
            return val.strip()
    val = request.headers.get("X-CSRF-Token")
    return val.strip() if val else None


def _token_from_body(body: bytes, content_type: str) -> str | None:
    if not body:
        return None
    ct = content_type.lower()
    if "application/x-www-form-urlencoded" in ct:
        qs = parse_qs(body.decode("utf-8", errors="ignore"), keep_blank_values=True)
        vals = qs.get("csrf_token")
        return vals[0].strip() if vals else None
    if "multipart/form-data" in ct:
        text = body.decode("utf-8", errors="ignore")
        m = _MULTIPART_TOKEN.search(text)
        return m.group(1).strip() if m else None
    return None


def _request_with_body(request: Request, body: bytes) -> Request:
    """Rebuild request so downstream Form() can still read the body."""
    sent = False

    async def receive():
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        return {"type": "http.request", "body": b"", "more_body": False}

    return StarletteRequest(request.scope, receive)


class CSRFMiddleware(BaseHTTPMiddleware):
    """Require CSRF token on POST/PUT/PATCH/DELETE (session-based)."""

    async def dispatch(self, request: Request, call_next):
        if request.method in _SAFE_METHODS:
            return await call_next(request)

        path = request.url.path
        if path.startswith("/static") or path.startswith("/api/updates/"):
            return await call_next(request)

        try:
            expected = request.session.get(_SESSION_KEY)
        except AssertionError:
            expected = None

        if not expected:
            expected = ensure_csrf_token(request)

        provided = _header_token(request)
        if provided:
            if not _tokens_match(str(expected), provided):
                return self._reject(request)
            return await call_next(request)

        # No header — read form body, then re-inject for route handlers.
        body = await request.body()
        content_type = request.headers.get("content-type") or ""
        provided = _token_from_body(body, content_type)
        if not _tokens_match(str(expected), provided):
            return self._reject(request)

        return await call_next(_request_with_body(request, body))

    @staticmethod
    def _reject(request: Request):
        detail = "CSRF validation failed"
        if wants_json(request):
            return JSONResponse({"detail": detail}, status_code=403)
        return HTMLResponse(
            "<!DOCTYPE html><html><body><h1>403</h1>"
            f"<p>{detail}</p>"
            '<p><a href="/">Back</a></p></body></html>',
            status_code=403,
        )
