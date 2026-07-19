"""
routers/auth.py — Login / logout for the panel admin.
"""
import logging
from urllib.parse import unquote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from services import auth_service
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


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, next: str | None = None):
    if request.session.get("user_id"):
        return RedirectResponse(_safe_next(next), status_code=302)
    return templates.TemplateResponse(
        "pages/auth/login.html",
        {
            "request": request,
            "error": None,
            "username": "",
            "next": _safe_next(next),
        },
    )


@router.post("/login", response_class=HTMLResponse)
async def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    next: str = Form("/"),
    db: AsyncSession = Depends(get_db),
):
    next_url = _safe_next(next)
    user = await auth_service.authenticate(db, username, password)
    if user is None:
        return templates.TemplateResponse(
            "pages/auth/login.html",
            {
                "request": request,
                "error": "Invalid username or password",
                "username": username.strip(),
                "next": next_url,
            },
            status_code=401,
        )
    request.session["user_id"] = user.id
    request.session["username"] = user.username
    logger.info("User '%s' logged in", user.username)
    return RedirectResponse(next_url, status_code=303)


@router.post("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)
