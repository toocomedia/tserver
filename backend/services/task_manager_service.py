"""Unified background task orchestration, log streaming, and concurrency lock service."""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

MAX_TASK_LOG_LINES = 500
MAX_HISTORY_TASKS = 50


@dataclass
class TaskRecord:
    id: str
    category: str
    action: str
    target_id: str
    label: str
    status: str = "running"  # running, succeeded, failed, cancelled, queued
    progress: int = 0  # 0 - 100
    current_step: str = ""
    started_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None
    error: Optional[str] = None
    logs: List[str] = field(default_factory=list)
    lock_type: Optional[str] = None  # apt, build, exclusive, etc.
    cancel_callback: Optional[Callable[[], None]] = None
    _task_ref: Optional[asyncio.Task] = None

    def add_log(self, text: str) -> None:
        if not text:
            return
        lines = str(text).splitlines()
        for line in lines:
            line_str = line.rstrip()
            if not line_str:
                continue
            ts = time.strftime("%H:%M:%S")
            self.logs.append(f"[{ts}] {line_str}")
            if len(self.logs) > MAX_TASK_LOG_LINES:
                self.logs.pop(0)

    def to_dict(self, include_logs: bool = True) -> Dict[str, Any]:
        data = {
            "id": self.id,
            "category": self.category,
            "action": self.action,
            "target_id": self.target_id,
            "label": self.label,
            "status": self.status,
            "progress": self.progress,
            "current_step": self.current_step,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "elapsed_seconds": int((self.finished_at or time.time()) - self.started_at),
            "error": self.error,
            "lock_type": self.lock_type,
            "can_cancel": bool(self.cancel_callback and self.status == "running"),
        }
        if include_logs:
            data["logs"] = self.logs[-100:]  # last 100 lines for response
        return data


class TaskManagerService:
    def __init__(self) -> None:
        self._active_tasks: Dict[str, TaskRecord] = {}
        self._history_tasks: List[TaskRecord] = []
        self._lock = asyncio.Lock()

    def get_active_locks(self) -> Dict[str, Any]:
        """Ultra-fast, zero-overhead check of currently active locks and running task count."""
        active = list(self._active_tasks.values())
        apt_locked = any(t.lock_type == "apt" and t.status == "running" for t in active)
        build_locked = any(t.lock_type == "build" and t.status == "running" for t in active)
        exclusive_locked = any(t.lock_type == "exclusive" and t.status == "running" for t in active)
        
        running = [t for t in active if t.status == "running"]
        return {
            "active_count": len(running),
            "has_running": bool(running),
            "apt_locked": apt_locked,
            "build_locked": build_locked,
            "exclusive_locked": exclusive_locked,
            "running_summary": [
                {"id": t.id, "label": t.label, "category": t.category, "elapsed": int(time.time() - t.started_at)}
                for t in running[:3]
            ],
        }

    def list_active(self, include_logs: bool = False) -> List[Dict[str, Any]]:
        return [task.to_dict(include_logs=include_logs) for task in self._active_tasks.values()]

    def list_history(self, limit: int = 20) -> List[Dict[str, Any]]:
        return [task.to_dict(include_logs=False) for task in self._history_tasks[:limit]]

    def get_task(self, task_id: str, include_logs: bool = True) -> Optional[Dict[str, Any]]:
        task = self._active_tasks.get(task_id)
        if not task:
            for item in self._history_tasks:
                if item.id == task_id:
                    task = item
                    break
        return task.to_dict(include_logs=include_logs) if task else None

    async def cancel(self, task_id: str) -> bool:
        task = self._active_tasks.get(task_id)
        if not task or task.status != "running":
            return False
        task.status = "cancelled"
        task.finished_at = time.time()
        task.add_log("Operation cancelled by user.")
        if task.cancel_callback:
            try:
                task.cancel_callback()
            except Exception as exc:
                logger.warning("Error in task cancel callback: %s", exc)
        if task._task_ref and not task._task_ref.done():
            task._task_ref.cancel()
        await self._archive_task(task)
        return True

    async def _archive_task(self, task: TaskRecord) -> None:
        async with self._lock:
            self._active_tasks.pop(task.id, None)
            self._history_tasks.insert(0, task)
            if len(self._history_tasks) > MAX_HISTORY_TASKS:
                self._history_tasks.pop()

    def create_task(
        self,
        category: str,
        action: str,
        target_id: str,
        label: str,
        lock_type: Optional[str] = None,
        cancel_callback: Optional[Callable[[], None]] = None,
    ) -> TaskRecord:
        task_id = f"task_{int(time.time())}_{uuid.uuid4().hex[:6]}"
        record = TaskRecord(
            id=task_id,
            category=category,
            action=action,
            target_id=target_id,
            label=label,
            lock_type=lock_type,
            cancel_callback=cancel_callback,
        )
        self._active_tasks[task_id] = record
        return record

    async def spawn(
        self,
        category: str,
        action: str,
        target_id: str,
        label: str,
        runner: Callable[[TaskRecord], Awaitable[tuple[bool, str]]],
        lock_type: Optional[str] = None,
        cancel_callback: Optional[Callable[[], None]] = None,
    ) -> TaskRecord:
        """Spawn a background task, capturing its lifecycle, log output, and status."""
        record = self.create_task(
            category=category,
            action=action,
            target_id=target_id,
            label=label,
            lock_type=lock_type,
            cancel_callback=cancel_callback,
        )
        record.add_log(f"Started {label}...")

        async def _execute():
            try:
                success, message = await runner(record)
                record.finished_at = time.time()
                if success:
                    record.status = "succeeded"
                    record.progress = 100
                    record.add_log(message or "Completed successfully.")
                else:
                    record.status = "failed"
                    record.error = message or "Operation failed."
                    record.add_log(f"Failed: {message}")
            except asyncio.CancelledError:
                record.status = "cancelled"
                record.finished_at = time.time()
                record.add_log("Task execution was cancelled.")
            except Exception as exc:
                logger.error("Unhandled error in background task %s: %s", record.id, exc, exc_info=True)
                record.status = "failed"
                record.error = str(exc)
                record.finished_at = time.time()
                record.add_log(f"Error: {exc}")
            finally:
                await self._archive_task(record)

        coro_task = asyncio.create_task(_execute())
        record._task_ref = coro_task
        return record


task_manager_service = TaskManagerService()
