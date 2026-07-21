"""
routers/updates.py — Git update check & deployment API router.
"""
from fastapi import APIRouter, Query
from pydantic import BaseModel
from services import update_service

router = APIRouter(prefix="/api/updates", tags=["updates"])


class AutoUpdateIn(BaseModel):
    enabled: bool


@router.get("/check")
async def check_for_updates(force: bool = Query(default=False)):
    """Check for new git updates on GitHub (cached 24h unless force=true)."""
    return await update_service.check_updates(force=force)


@router.post("/apply")
async def apply_update():
    """Trigger background update and restart of srv-panel."""
    return await update_service.trigger_update()


@router.get("/status")
async def update_status():
    """Check background update process status and live log output."""
    return await update_service.get_update_status()


@router.post("/auto-update")
async def toggle_auto_update(body: AutoUpdateIn):
    """Enable or disable automatic daily background updates."""
    return await update_service.set_auto_update(body.enabled)
