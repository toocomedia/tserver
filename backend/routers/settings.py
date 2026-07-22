"""
routers/settings.py — Panel settings UI and API.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field

from services import panel_settings_service
from templating import templates

logger = logging.getLogger(__name__)
router = APIRouter(tags=["settings"])


class PanelSettingsIn(BaseModel):
    url_mode: str = "none"
    custom_domain: str = ""
    parent_domain: str = ""
    subdomain_label: str = "panel"
    panel_domain: str = ""
    allow_ip: bool = True
    ip_port: int = Field(default=80, ge=1, le=65535)
    # When hostname changes: also delete old Let's Encrypt cert files
    remove_ssl_on_change: bool = False
    session_https_only: bool = False
    security_headers: bool = True
    hsts_enabled: bool = False
    session_max_age_days: int = Field(default=7, ge=1, le=365)


@router.get("/settings", include_in_schema=False)
async def settings_redirect():
    """Normalize to trailing slash like other section indexes."""
    return RedirectResponse("/settings/", status_code=307)


@router.get("/settings/", response_class=HTMLResponse)
async def settings_page(request: Request):
    try:
        status = await panel_settings_service.get_status()
    except Exception as exc:
        logger.exception("settings status failed")
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
            "session_https_only": False,
            "session_max_age_days": 7,
            "security_headers": True,
            "hsts_enabled": False,
            "urls": {},
            "load_error": str(exc),
        }
    return templates.TemplateResponse(
        "pages/settings/index.html",
        {"request": request, "active_page": "settings", "s": status},
    )


@router.get("/api/settings")
async def api_get_settings():
    return await panel_settings_service.get_status()


@router.post("/api/settings/panel")
async def api_save_panel_settings(body: PanelSettingsIn):
    return await panel_settings_service.save_settings(body.model_dump())


@router.post("/api/settings/panel/ssl/prepare")
async def api_ssl_prepare():
    """SSL step 1 — nginx HTTP ready."""
    return await panel_settings_service.ssl_prepare()


@router.post("/api/settings/panel/ssl/cert")
async def api_ssl_cert():
    """SSL step 2 — certbot (slow)."""
    return await panel_settings_service.ssl_issue_cert()


@router.post("/api/settings/panel/ssl/apply")
async def api_ssl_apply():
    """SSL step 3 — enable HTTPS on panel vhost."""
    return await panel_settings_service.ssl_apply_https()


@router.post("/api/settings/panel/ssl/remove")
async def api_ssl_remove():
    """Disable HTTPS and delete panel certificate files."""
    return await panel_settings_service.remove_panel_ssl()


class PerformanceSettingsIn(BaseModel):
    perf_gzip: bool = False
    perf_static_cache: bool = False


@router.post("/api/settings/performance")
async def api_save_performance(body: PerformanceSettingsIn):
    """Save global nginx performance settings (gzip, static asset cache)."""
    return await panel_settings_service.save_performance_settings(body.model_dump())
