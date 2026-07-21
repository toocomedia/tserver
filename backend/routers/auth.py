"""
routers/auth.py — Login / logout for the panel admin.
"""
import logging
from urllib.parse import unquote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

import config
from database import get_db
from middleware.limiter import limiter
from services import auth_service
from services import login_guard
from templating import templates

logger = logging.getLogger(__name__)
router = APIRouter(tags=["auth"])


def _safe_next(raw: str | None) -> str:
    if not raw:
        return "/"
    path = unquote(raw)
    if not path.startswith("/") or path.startswith("//"):
        return "/"
    if path.startswith("/login"):
        return "/"
    return path


def _login_page(
    request: Request,
    *,
    error: str | None,
    username: str,
    next_url: str,
    status_code: int = 200,
):
    return templates.TemplateResponse(
        "pages/auth/login.html",
        {
            "request": request,
            "error": error,
            "username": username,
            "next": next_url,
        },
        status_code=status_code,
    )


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, next: str | None = None):
    if request.session.get("user_id"):
        return RedirectResponse(_safe_next(next), status_code=302)
    return _login_page(
        request,
        error=None,
        username="",
        next_url=_safe_next(next),
    )


@router.post("/login", response_class=HTMLResponse)
@limiter.limit(config.LOGIN_RATE_LIMIT)
async def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    next: str = Form("/"),
    db: AsyncSession = Depends(get_db),
):
    next_url = _safe_next(next)
    ip = login_guard.client_ip(request)
    uname = username.strip()

    if login_guard.is_locked(ip=ip, username=uname):
        logger.warning("Login blocked (lockout) ip=%s user=%s", ip, uname)
        return _login_page(
            request,
            error=login_guard.LOCKOUT_MESSAGE,
            username=uname,
            next_url=next_url,
            status_code=429,
        )

    user = await auth_service.authenticate(db, username, password)
    if user is None:
        triggered = login_guard.record_failure(ip=ip, username=uname)
        logger.warning(
            "Login failed ip=%s user=%s lockout=%s",
            ip,
            uname,
            triggered,
        )
        err = (
            login_guard.LOCKOUT_MESSAGE
            if login_guard.is_locked(ip=ip, username=uname)
            else "Invalid username or password"
        )
        code = 429 if err == login_guard.LOCKOUT_MESSAGE else 401
        return _login_page(
            request,
            error=err,
            username=uname,
            next_url=next_url,
            status_code=code,
        )

    login_guard.clear_failures(ip=ip, username=user.username)
    request.session["user_id"] = user.id
    request.session["username"] = user.username
    logger.info("User '%s' logged in from %s", user.username, ip)
    return RedirectResponse(next_url, status_code=303)


@router.post("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)
