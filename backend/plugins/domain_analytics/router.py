"""
plugins/domain_analytics/router.py — FastAPI endpoints for Domain Analytics.
"""
from __future__ import annotations

from typing import Optional
from fastapi import APIRouter, Request, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from database import get_db as get_panel_db
from models.domain import Domain
from templating import templates
from plugins.domain_analytics.service import domain_analytics_service
from plugins.domain_analytics.geoip_service import geoip_service
from plugins.domain_analytics.db import get_db

router = APIRouter(prefix="/plugins/domain_analytics", tags=["domain_analytics"])


@router.get("/", response_class=HTMLResponse)
@router.get("", response_class=HTMLResponse)
async def analytics_index(request: Request, db: AsyncSession = Depends(get_panel_db)):
    """Main Analytics page: lists all hosted domains with 24h summary metrics."""
    panel_domains = (await db.execute(select(Domain.name))).scalars().all()
    domain_analytics_service.sync_domains_from_panel(list(panel_domains))
    domain_analytics_service.process_all_active_domains()

    summaries = domain_analytics_service.list_domains_summary()
    status = domain_analytics_service.get_status()

    return templates.TemplateResponse("analytics/index.html", {
        "request": request,
        "active_page": "domain_analytics",
        "domains": summaries,
        "status": status,
    })


@router.get("/domain/{domain_name}", response_class=HTMLResponse)
async def analytics_domain_view(request: Request, domain_name: str, days: int = 7):
    """Detailed analytics view for a single domain. Respects pause status."""
    is_active = domain_analytics_service.get_domain_active_status(domain_name)
    sync_result = domain_analytics_service.process_domain_log(domain_name) if is_active else {
        "success": False, "message": f"Tracking is paused for {domain_name}", "processed_lines": 0
    }
    stats = domain_analytics_service.get_domain_detail(domain_name, days=days)
    stats["sync_result"] = sync_result
    return templates.TemplateResponse("analytics/domain_detail.html", {
        "request": request,
        "active_page": "domain_analytics",
        "domain_name": domain_name,
        "stats": stats,
        "selected_days": days,
    })


@router.get("/api/domain/{domain_name}/stats", response_class=JSONResponse)
async def api_domain_stats(domain_name: str, days: int = 7):
    """JSON API endpoint for dynamic charts."""
    return domain_analytics_service.get_domain_detail(domain_name, days=days)


@router.post("/api/domain/{domain_name}/sync", response_class=JSONResponse)
async def api_sync_domain_logs(domain_name: str):
    """Manual sync trigger for domain logs (forced)."""
    return domain_analytics_service.process_domain_log(domain_name, force=True)


@router.post("/api/domain/{domain_name}/clear", response_class=JSONResponse)
async def api_clear_domain_data(domain_name: str):
    """Clear all historical analytics data for a domain."""
    domain_analytics_service.clear_domain_data(domain_name)
    return {"domain_name": domain_name, "status": "success"}


@router.post("/api/domain/{domain_name}/toggle", response_class=JSONResponse)
async def api_toggle_domain(domain_name: str, payload: dict):
    """Toggle domain analytics tracking ON/OFF."""
    is_active = bool(payload.get("is_active", False))
    domain_analytics_service.toggle_domain(domain_name, is_active=is_active)
    if is_active:
        domain_analytics_service.process_domain_log(domain_name)
    return {"domain_name": domain_name, "is_active": is_active, "status": "success"}


@router.get("/settings", response_class=HTMLResponse)
async def analytics_settings_view(request: Request):
    """Settings page: Optional GeoIP toggle, data retention, database status."""
    settings = geoip_service.get_settings()
    with get_db() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = 'retention_days'").fetchone()
        retention_days = int(row["value"]) if row else 60
    settings["retention_days"] = retention_days

    return templates.TemplateResponse("analytics/settings.html", {
        "request": request,
        "active_page": "domain_analytics",
        "settings": settings,
        "message": request.query_params.get("message"),
        "error": request.query_params.get("error"),
    })


@router.post("/settings/geoip/toggle")
async def api_toggle_geoip(enabled: str = Form(...)):
    """Toggle optional GeoIP tracking on/off."""
    is_on = enabled.lower() in ("1", "true", "on", "yes")
    geoip_service.set_enabled(is_on)
    return RedirectResponse("/plugins/domain_analytics/settings?message=GeoIP+settings+updated", status_code=303)


@router.post("/settings/retention")
async def api_update_retention(retention_days: int = Form(60)):
    """Update data retention period in days."""
    with get_db() as conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES ('retention_days', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(retention_days),)
        )
    return RedirectResponse("/plugins/domain_analytics/settings?message=Retention+period+updated", status_code=303)
