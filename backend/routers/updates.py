"""
routers/updates.py — Git update check & deployment API router.
"""
from fastapi import APIRouter, Query
from services import update_service

router = APIRouter(prefix="/api/updates", tags=["updates"])


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
