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
    locked: bool = False,
):
    return templates.TemplateResponse(
        "pages/auth/login.html",
        {
            "request": request,
            "error": error,
            "username": username,
            "next": next_url,
            # When true, form controls + Sign in are disabled (rate limit / lockout).
            "locked": locked,
        },
        status_code=status_code,
    )


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, next: str | None = None):
    if request.session.get("user_id"):
        return RedirectResponse(_safe_next(next), status_code=302)
    next_url = _safe_next(next)
    ip = login_guard.client_ip(request)
    # IP-only lockout still applies on refresh (username unknown on GET).
    if login_guard.is_locked(ip=ip, username=""):
        return _login_page(
            request,
            error=login_guard.LOCKOUT_MESSAGE,
            username="",
            next_url=next_url,
            status_code=429,
            locked=True,
        )
    return _login_page(
        request,
        error=None,
        username="",
        next_url=next_url,
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
            locked=True,
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
        locked = login_guard.is_locked(ip=ip, username=uname)
        err = (
            login_guard.LOCKOUT_MESSAGE
            if locked
            else "Invalid username or password"
        )
        code = 429 if locked else 401
        return _login_page(
            request,
            error=err,
            username=uname,
            next_url=next_url,
            status_code=code,
            locked=locked,
        )

    login_guard.clear_failures(ip=ip, username=user.username)

    if user.totp_enabled:
        request.session["partial_user_id"] = user.id
        request.session["partial_next_url"] = next_url
        logger.info("User '%s' credentials verified, awaiting 2FA code from %s", user.username, ip)
        return RedirectResponse("/login/2fa", status_code=303)

    request.session["user_id"] = user.id
    request.session["username"] = user.username
    logger.info("User '%s' logged in from %s", user.username, ip)
    return RedirectResponse(next_url, status_code=303)


@router.get("/login/2fa", response_class=HTMLResponse)
async def login_2fa_page(request: Request, db: AsyncSession = Depends(get_db)):
    if request.session.get("user_id"):
        return RedirectResponse("/", status_code=302)
    
    partial_user_id = request.session.get("partial_user_id")
    if not partial_user_id:
        return RedirectResponse("/login", status_code=302)
        
    user = await auth_service.get_by_id(db, partial_user_id)
    if not user or not user.totp_enabled:
        request.session.pop("partial_user_id", None)
        return RedirectResponse("/login", status_code=302)

    return templates.TemplateResponse(
        "pages/auth/2fa_verify.html",
        {
            "request": request,
            "error": None,
            "username": user.username,
        },
    )


@router.post("/login/2fa", response_class=HTMLResponse)
@limiter.limit(config.LOGIN_RATE_LIMIT)
async def login_2fa_submit(
    request: Request,
    code: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    partial_user_id = request.session.get("partial_user_id")
    if not partial_user_id:
        return RedirectResponse("/login", status_code=302)

    user = await auth_service.get_by_id(db, partial_user_id)
    if not user or not user.totp_enabled:
        request.session.pop("partial_user_id", None)
        return RedirectResponse("/login", status_code=302)

    ip = login_guard.client_ip(request)
    valid = await auth_service.verify_user_2fa(db, user, code)
    if not valid:
        logger.warning("2FA verification failed for user '%s' from %s", user.username, ip)
        return templates.TemplateResponse(
            "pages/auth/2fa_verify.html",
            {
                "request": request,
                "error": "Invalid verification code or recovery key",
                "username": user.username,
            },
            status_code=401,
        )

    # 2FA succeeded! Complete authentication
    request.session.pop("partial_user_id", None)
    next_url = _safe_next(request.session.pop("partial_next_url", "/"))
    
    request.session["user_id"] = user.id
    request.session["username"] = user.username
    logger.info("User '%s' completed 2FA login from %s", user.username, ip)
    return RedirectResponse(next_url, status_code=303)


@router.post("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)

