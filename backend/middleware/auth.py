"""
middleware/auth.py — Require a logged-in panel admin on protected routes.
"""
from urllib.parse import quote

from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware

# Paths that do not require authentication.
_PUBLIC_EXACT = frozenset({"/api/health"})


def is_public_path(path: str) -> bool:
    if path in _PUBLIC_EXACT:
        return True
    if path in ("/login", "/logout"):
        return True
    if path.startswith("/static"):
        return True
    return False


def wants_json(request: Request) -> bool:
    accept = request.headers.get("accept", "")
    if "application/json" in accept and "text/html" not in accept:
        return True
    path = request.url.path
    return path.startswith("/api/") and path not in _PUBLIC_EXACT


class AuthMiddleware(BaseHTTPMiddleware):
    """Redirect unauthenticated browsers to /login; return 401 for API-ish clients."""

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if is_public_path(path):
            return await call_next(request)

        try:
            user_id = request.session.get("user_id")
        except AssertionError:
            # SessionMiddleware not installed — treat as logged out
            user_id = None
        if user_id:
            return await call_next(request)

        if wants_json(request):
            return JSONResponse(
                {"detail": "Not authenticated"},
                status_code=401,
            )

        next_path = path
        if request.url.query:
            next_path = f"{path}?{request.url.query}"
        # Open-redirect safe: relative path only
        if not next_path.startswith("/") or next_path.startswith("//"):
            next_path = "/"
        return RedirectResponse(
            url=f"/login?next={quote(next_path, safe='/?&=')}",
            status_code=302,
        )
