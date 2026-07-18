"""
middleware/error_capture.py — Request ID + exception → error_events.

v1 rules:
- Always record unhandled Exception
- Record HTTPException 5xx always
- Record HTTPException 4xx on POST mutations under managed modules
- Skip if request.state.error_recorded is already True
"""
import logging
import traceback
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException

from services import error_service

logger = logging.getLogger(__name__)

_MUTATION_PREFIXES = ("/domains", "/dns", "/ssl", "/proxy", "/admin")


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Attach a short request_id to every request."""

    async def dispatch(self, request: Request, call_next):
        request.state.request_id = uuid.uuid4().hex[:12]
        request.state.error_recorded = False
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response


def _should_record_http(request: Request, status_code: int) -> bool:
    if getattr(request.state, "error_recorded", False):
        return False
    if status_code >= 500:
        return True
    if status_code < 400:
        return False
    # 4xx: only mutation POSTs under panel modules
    if request.method not in ("POST", "PUT", "PATCH", "DELETE"):
        return False
    path = request.url.path
    return any(path.startswith(p) for p in _MUTATION_PREFIXES)


def _detail_str(detail) -> str:
    if detail is None:
        return "HTTP error"
    if isinstance(detail, (list, dict)):
        return str(detail)
    return str(detail)


def register_error_handlers(app: FastAPI) -> None:
    """Install exception handlers that persist errors then re-emit responses."""

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException):
        if _should_record_http(request, exc.status_code):
            await error_service.record(
                level="error" if exc.status_code >= 500 else "warning",
                source=error_service._infer_source(request.url.path),
                operation="http_exception",
                message=_detail_str(exc.detail)[:500],
                detail=_detail_str(exc.detail),
                request=request,
            )

        # Prefer redirect-friendly JSON for API-ish paths; HTML forms use routers
        accept = request.headers.get("accept", "")
        if "text/html" in accept and exc.status_code in (401, 403, 404):
            return JSONResponse(
                status_code=exc.status_code,
                content={"detail": _detail_str(exc.detail)},
            )
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail},
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        if not getattr(request.state, "error_recorded", False):
            await error_service.record_exception(
                exc,
                operation="unhandled",
                request=request,
            )
        logger.exception("Unhandled exception on %s %s", request.method, request.url.path)

        accept = request.headers.get("accept", "")
        if "text/html" in accept:
            # Bounce to error list so operator can inspect
            return RedirectResponse(
                url="/admin/errors/?error=An+unexpected+error+occurred",
                status_code=303,
            )
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error"},
        )
