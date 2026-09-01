"""
routers/tasks.py — Unified System Task Manager APIs.
Provides live task progress, log streaming, history, and concurrency lock queries.
"""
from fastapi import APIRouter, HTTPException
from services.task_manager_service import task_manager_service

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


@router.get("/active")
async def get_active_tasks():
    """Ultra-lightweight endpoint for top-bar status and global concurrency locks."""
    locks = task_manager_service.get_active_locks()
    active = task_manager_service.list_active(include_logs=False)
    return {
        "active": active,
        "locks": locks,
        "active_count": locks["active_count"],
        "has_running": locks["has_running"],
    }


@router.get("")
@router.get("/")
async def get_all_tasks():
    """Full list of active tasks and recent history for the Task Manager Drawer."""
    return {
        "active": task_manager_service.list_active(include_logs=True),
        "history": task_manager_service.list_history(limit=25),
        "locks": task_manager_service.get_active_locks(),
    }


@router.get("/{task_id}")
async def get_task_detail(task_id: str):
    """Detailed task record including real-time log output buffer."""
    task = task_manager_service.get_task(task_id, include_logs=True)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found.")
    return task


@router.post("/{task_id}/cancel")
async def cancel_task(task_id: str):
    """Request graceful cancellation of a running background task."""
    success = await task_manager_service.cancel(task_id)
    if not success:
        raise HTTPException(status_code=400, detail="Task could not be cancelled or is already finished.")
    return {"success": True, "message": "Task cancellation requested."}
