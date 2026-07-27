"""In-process serialization and cancellation for hosted-app operations."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from fastapi import HTTPException

_locks: dict[int, asyncio.Lock] = {}
_tasks: dict[int, asyncio.Task[None]] = {}


def _lock(app_id: int) -> asyncio.Lock:
    return _locks.setdefault(app_id, asyncio.Lock())


def register_deployment(app_id: int, task: asyncio.Task[None]) -> None:
    _tasks[app_id] = task


def unregister_deployment(app_id: int, task: asyncio.Task[None]) -> None:
    if _tasks.get(app_id) is task:
        _tasks.pop(app_id, None)


def ensure_available(app_id: int) -> None:
    task = _tasks.get(app_id)
    if _lock(app_id).locked() or (task and not task.done()):
        raise HTTPException(409, "A Python app operation is already running.")


async def cancel_deployment(app_id: int) -> None:
    task = _tasks.get(app_id)
    if not task or task.done() or task is asyncio.current_task():
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


async def run(app_id: int, operation: Callable[[], Awaitable[None]], *, wait: bool = False) -> None:
    lock = _lock(app_id)
    if lock.locked() and not wait:
        raise HTTPException(409, "A Python app operation is already running.")
    async with lock:
        await operation()
