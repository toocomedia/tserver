"""
routers/settings.py — Panel settings UI and API.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from services import panel_settings_service

logger = logging.getLogger(__name__)
router = APIRouter(tags=["settings"])
templates = Jinja2Templates(directory="templates")


class PanelSettingsIn(BaseModel):
    # none = IP only | custom = external FQDN | subdomain = label under managed domain
    url_mode: str = "none"
    custom_domain: str = ""
    parent_domain: str = ""
    subdomain_label: str = "panel"
    # legacy alias still accepted by service
    panel_domain: str = ""
    allow_ip: bool = True
    ip_port: int = Field(default=80, ge=1, le=65535)
    session_https_only: bool = False
    security_headers: bool = True
    hsts_enabled: bool = False
    session_max_age_days: int = Field(default=7, ge=1, le=365)


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    try:
        status = await panel_settings_service.get_status()
    except Exception as exc:
        logger.exception("settings get_status failed")
        # Degraded page so Settings always opens (e.g. LE permission edge cases)
        status = {
            "server_ip": "",
            "panel_domain": "",
            "url_mode": "none",
            "parent_domain": "",
            "subdomain_label": "panel",
            "managed_domains": [],
            "allow_ip": True,
            "ip_port": 80,
            "app_port": 8000,
            "ssl_active": False,
            "dns_ok": None,
            "certbot_email": "",
            "session_https_only": False,
            "session_max_age": 604800,
            "session_max_age_days": 7,
            "security_headers": True,
            "hsts_enabled": False,
            "urls": {"ip_http": None, "domain_http": None, "domain_https": None},
            "restart_hint": str(exc),
            "load_error": str(exc),
        }
    return templates.TemplateResponse(
        "pages/settings/index.html",
        {
            "request": request,
            "active_page": "settings",
            "s": status,
        },
    )


@router.get("/api/settings")
async def api_get_settings(request: Request):
    return await panel_settings_service.get_status()


@router.post("/api/settings/panel")
async def api_save_panel_settings(body: PanelSettingsIn):
    return await panel_settings_service.save_settings(body.model_dump())


@router.post("/api/settings/panel/ssl")
async def api_issue_panel_ssl():
    return await panel_settings_service.issue_panel_ssl()
