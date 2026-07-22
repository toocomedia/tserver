"""
routers/notifications.py — Notification Center API & UI
"""
from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, delete
from pydantic import BaseModel

from database import get_db
from models.notification import Notification
from templating import templates

router = APIRouter(tags=["notifications"])

@router.get("/notifications", response_class=HTMLResponse)
async def notifications_page(request: Request, db: AsyncSession = Depends(get_db)):
    """Render the Notification Center UI."""
    result = await db.execute(
        select(Notification).order_by(Notification.created_at.desc()).limit(100)
    )
    notifications = result.scalars().all()
    
    return templates.TemplateResponse(
        "pages/notifications/index.html",
        {
            "request": request,
            "active_page": "notifications",
            "notifications": notifications,
        },
    )

@router.get("/api/notifications")
async def api_get_notifications(db: AsyncSession = Depends(get_db)):
    """Fetch recent notifications as JSON."""
    result = await db.execute(
        select(Notification).order_by(Notification.created_at.desc()).limit(50)
    )
    notifications = result.scalars().all()
    
    # Also get unread count
    count_result = await db.execute(
        select(Notification).where(Notification.is_read == False)
    )
    unread_count = len(count_result.scalars().all())
    
    return {
        "notifications": [
            {
                "id": n.id,
                "type": n.type,
                "message": n.message,
                "is_read": n.is_read,
                "created_at": n.created_at.isoformat()
            } for n in notifications
        ],
        "unread_count": unread_count
    }

class MarkReadPayload(BaseModel):
    id: int

@router.post("/api/notifications/read")
async def api_mark_read(payload: MarkReadPayload, db: AsyncSession = Depends(get_db)):
    """Mark a single notification as read."""
    n = await db.scalar(select(Notification).where(Notification.id == payload.id))
    if n:
        n.is_read = True
        await db.commit()
    return {"status": "ok"}

@router.post("/api/notifications/read-all")
async def api_mark_all_read(db: AsyncSession = Depends(get_db)):
    """Mark all notifications as read."""
    await db.execute(
        update(Notification)
        .where(Notification.is_read == False)
        .values(is_read=True)
    )
    await db.commit()
    return {"status": "ok"}

@router.delete("/api/notifications/clear")
async def api_clear_notifications(db: AsyncSession = Depends(get_db)):
    """Delete all notifications."""
    await db.execute(delete(Notification))
    await db.commit()
    return {"status": "ok"}
