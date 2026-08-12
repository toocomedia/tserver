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


def _safe_next(raw: str | None, allow_login: bool = False) -> str:
    if not raw:
        return "/"
    path = unquote(raw)
    if not path.startswith("/") or path.startswith("//"):
        return "/"
    if not allow_login and path.startswith("/login"):
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

    # Enforce server-side concurrency lock to reject duplicate/flooded requests
    with login_guard.InFlightGuard(ip=ip, username=uname) as acquired:
        if not acquired:
            logger.warning("Concurrent login request rejected ip=%s user=%s", ip, uname)
            return _login_page(
                request,
                error="An authentication request is already in progress.",
                username=uname,
                next_url=next_url,
                status_code=429,
                locked=True,
            )

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
        
        if user.is_2fa_enabled:
            request.session["pending_2fa_user_id"] = user.id
            request.session["pending_2fa_next"] = next_url
            return RedirectResponse("/login/2fa", status_code=303)
            
        request.session["user_id"] = user.id
        request.session["username"] = user.username
        logger.info("User '%s' logged in from %s", user.username, ip)
        return RedirectResponse(next_url, status_code=303)


@router.get("/login/2fa", response_class=HTMLResponse)
async def login_2fa_page(request: Request):
    if not request.session.get("pending_2fa_user_id"):
        return RedirectResponse("/login", status_code=302)
    return templates.TemplateResponse(
        "pages/auth/login_2fa.html",
        {"request": request, "error": None},
    )


@router.post("/login/2fa", response_class=HTMLResponse)
@limiter.limit(config.LOGIN_RATE_LIMIT)
async def login_2fa_submit(
    request: Request,
    code: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    user_id = request.session.get("pending_2fa_user_id")
    if not user_id:
        return RedirectResponse("/login", status_code=303)
        
    ip = login_guard.client_ip(request)
    user = await auth_service.get_by_id(db, user_id)
    if not user or not user.is_2fa_enabled:
        request.session.pop("pending_2fa_user_id", None)
        return RedirectResponse("/login", status_code=303)

    # Enforce server-side concurrency lock for 2FA submit
    with login_guard.InFlightGuard(ip=ip, username=user.username) as acquired:
        if not acquired:
            logger.warning("Concurrent 2FA verification request rejected ip=%s user=%s", ip, user.username)
            return templates.TemplateResponse(
                "pages/auth/login_2fa.html",
                {"request": request, "error": "An authentication request is already in progress.", "locked": True},
                status_code=429
            )
            
        if login_guard.is_locked(ip=ip, username=user.username):
            return templates.TemplateResponse(
                "pages/auth/login_2fa.html",
                {"request": request, "error": login_guard.LOCKOUT_MESSAGE, "locked": True},
                status_code=429
            )
            
        if not auth_service.verify_totp_code(user.totp_secret, code):
            login_guard.record_failure(ip=ip, username=user.username)
            locked = login_guard.is_locked(ip=ip, username=user.username)
            return templates.TemplateResponse(
                "pages/auth/login_2fa.html",
                {"request": request, "error": "Invalid 2FA code", "locked": locked},
                status_code=429 if locked else 401
            )
            
        login_guard.clear_failures(ip=ip, username=user.username)
        request.session.pop("pending_2fa_user_id", None)
        next_url = request.session.pop("pending_2fa_next", "/")
        
        request.session["user_id"] = user.id
        request.session["username"] = user.username
        logger.info("User '%s' logged in from %s (2FA verified)", user.username, ip)
        
        return RedirectResponse(next_url, status_code=303)


@router.post("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@router.post("/language")
async def set_language(
    request: Request,
    lang: str = Form(...),
    next: str = Form("/"),
):
    next_url = _safe_next(next, allow_login=True)
    from services.i18n_service import i18n_service
    if lang not in i18n_service.locales:
        lang = "en"
    
    response = RedirectResponse(next_url, status_code=303)
    response.set_cookie(
        "panel_lang", 
        lang, 
        max_age=31536000, 
        path="/", 
        samesite="lax"
    )
    return response
