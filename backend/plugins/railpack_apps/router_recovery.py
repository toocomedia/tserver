"""On-demand recovery endpoints for interrupted Apps Engine creation."""
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from services import container_app_orphan_recovery_service as recovery

router = APIRouter(prefix="/recovery")


@router.get("/orphans")
async def orphans(db: AsyncSession = Depends(get_db)):
    return JSONResponse({"items": await recovery.list_orphans(db)})


@router.post("/orphans/{app_id}/remove")
async def remove_orphan(app_id: int, db: AsyncSession = Depends(get_db)):
    result = await recovery.remove_orphan(db, app_id)
    return JSONResponse({"success": True, **result})
