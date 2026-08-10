"""Audit helpers that intentionally never store file content or secret values."""
from __future__ import annotations

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from models.file_manager_event import FileManagerEvent
from services import login_guard


async def record(
    db: AsyncSession,
    request: Request,
    *,
    app_id: int,
    target_type: str = "container",
    root_id: str,
    relative_path: str,
    action: str,
    result: str,
    size_bytes: int | None = None,
    item_count: int | None = None,
) -> None:
    user_id = request.session.get("user_id")
    db.add(FileManagerEvent(
        user_id=user_id if isinstance(user_id, int) else None,
        app_id=app_id,
        target_type=target_type[:24],
        root_id=root_id[:64],
        relative_path=relative_path[:1024],
        action=action[:32],
        result=result[:16],
        size_bytes=size_bytes,
        item_count=item_count,
        client_ip=login_guard.client_ip(request)[:64],
        request_id=str(getattr(request.state, "request_id", ""))[:36] or None,
    ))
