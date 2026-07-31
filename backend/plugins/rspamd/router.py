"""
backend/plugins/rspamd/router.py — APIRouter for Rspamd Spam Filter plugin.
Exposes Spam Filter dashboard UI and management APIs for action thresholds,
service controls, installation, and Maddy Mail Server integration.
"""
import os
import logging
import subprocess
from pathlib import Path
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from templating import templates
from plugins.rspamd.service import rspamd_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/plugins/rspamd", tags=["rspamd"])
SCRIPT_DIR = Path(__file__).parent / "scripts"


class ThresholdRequest(BaseModel):
    reject: float = Field(..., ge=1.0, le=100.0, description="Reject score threshold")
    add_header: float = Field(..., ge=0.5, le=50.0, description="Junk / Add header score threshold")


class ServiceActionRequest(BaseModel):
    action: str = Field(..., description="Action to perform: start, stop, or restart")


class SyncMaddyRequest(BaseModel):
    enable: bool = Field(..., description="Whether to enable or disable Maddy Rspamd check")


# ---------------------------------------------------------------------------
# UI Page
# ---------------------------------------------------------------------------

@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Render Rspamd Spam Filter Management Dashboard."""
    from plugins.manager import plugin_manager
    plugin_info = plugin_manager.get_plugin("rspamd")
    plugin_version = plugin_info["version"] if plugin_info else "1.0.0"

    status = rspamd_service.get_status()
    stats = rspamd_service.get_stats()
    thresholds = rspamd_service.get_thresholds()

    return templates.TemplateResponse(
        "rspamd.html",
        {
            "request": request,
            "active_page": "plugins",
            "plugin_version": plugin_version,
            "status": status,
            "stats": stats,
            "thresholds": thresholds,
        },
    )


# ---------------------------------------------------------------------------
# Install / Uninstall Endpoints
# ---------------------------------------------------------------------------

@router.post("/api/install")
async def install_rspamd(request: Request):
    """Trigger Rspamd installation script via privileged helper."""
    if os.name == "nt":
        return JSONResponse({"status": "ok", "message": "Mock install on Windows."})

    res = rspamd_service.install()
    if not res.get("success"):
        raise HTTPException(status_code=500, detail=res.get("error", "Installation failed"))
    return RedirectResponse("/plugins/rspamd/", status_code=303)


@router.post("/api/uninstall")
async def uninstall_rspamd(request: Request):
    """Trigger Rspamd uninstallation script via privileged helper."""
    if os.name == "nt":
        return JSONResponse({"status": "ok", "message": "Mock uninstall on Windows."})

    res = rspamd_service.uninstall()
    if not res.get("success"):
        raise HTTPException(status_code=500, detail=res.get("error", "Uninstallation failed"))
    return RedirectResponse("/plugins/rspamd/", status_code=303)


# ---------------------------------------------------------------------------
# API Management Endpoints
# ---------------------------------------------------------------------------

@router.get("/api/status", response_class=JSONResponse)
async def get_status_api():
    """Get real-time Rspamd daemon status, Maddy integration status, and spam statistics."""
    status = rspamd_service.get_status()
    stats = rspamd_service.get_stats()
    thresholds = rspamd_service.get_thresholds()
    return {
        "status": status,
        "stats": stats,
        "thresholds": thresholds,
    }


@router.post("/api/action", response_class=JSONResponse)
async def control_service_api(payload: ServiceActionRequest):
    """Start, stop, or restart Rspamd daemon."""
    res = rspamd_service.control_service(payload.action)
    if not res.get("success"):
        raise HTTPException(status_code=400, detail=res.get("error", "Service action failed"))
    return res


@router.post("/api/thresholds", response_class=JSONResponse)
async def update_thresholds_api(payload: ThresholdRequest):
    """Update Rspamd spam score action thresholds."""
    if payload.add_header >= payload.reject:
        raise HTTPException(status_code=400, detail="Junk / Add Header score must be strictly less than Reject score.")
    
    res = rspamd_service.update_thresholds(payload.reject, payload.add_header)
    if not res.get("success"):
        raise HTTPException(status_code=400, detail=res.get("error", "Failed to update thresholds"))
    return res


@router.post("/api/sync-maddy", response_class=JSONResponse)
async def sync_maddy_api(payload: SyncMaddyRequest):
    """Enable or disable Rspamd integration inside Maddy Mail Server."""
    res = rspamd_service.sync_maddy_integration(payload.enable)
    if not res.get("success"):
        raise HTTPException(status_code=400, detail=res.get("error", "Failed to sync Maddy integration"))
    return res
