"""
routers/settings.py — Panel settings UI and API.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from middleware.csrf import ensure_csrf_token
from services import panel_settings_service

logger = logging.getLogger(__name__)
router = APIRouter(tags=["settings"])
templates = Jinja2Templates(directory="templates")


class PanelSettingsIn(BaseModel):
    panel_domain: str = ""
    allow_ip: bool = True
    ip_port: int = Field(default=80, ge=1, le=65535)
    ensure_dns: bool = False
    session_https_only: bool = False
    security_headers: bool = True
    csrf_enabled: bool = True
    hsts_enabled: bool = False
    session_max_age_days: int = Field(default=7, ge=1, le=365)


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    status = await panel_settings_service.get_status()
    csrf = ensure_csrf_token(request)
    return templates.TemplateResponse(
        "pages/settings/index.html",
        {
            "request": request,
            "active_page": "settings",
            "csrf_token": csrf,
            "s": status,
        },
    )


@router.get("/api/settings")
async def api_get_settings(request: Request):
    status = await panel_settings_service.get_status()
    status["csrf_token"] = ensure_csrf_token(request)
    return status


@router.post("/api/settings/panel")
async def api_save_panel_settings(body: PanelSettingsIn):
    return await panel_settings_service.save_settings(body.model_dump())


@router.post("/api/settings/panel/ssl")
async def api_issue_panel_ssl():
    return await panel_settings_service.issue_panel_ssl()
