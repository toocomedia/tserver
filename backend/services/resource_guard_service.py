"""Low-cost host RAM guard for panel-managed heavy operations."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Awaitable, Callable

try:
    import psutil
except ImportError:  # pragma: no cover - deployment dependency
    psutil = None

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.notification import Notification
from models.resource_guard import ResourceGuardPriority, ResourceGuardSettings

PRIORITIES = ("high", "normal", "background")
_DEFAULTS = {"hosted_app": "high", "container_app": "high", "plugin": "normal", "dependency": "normal"}


@dataclass
class GuardOperation:
    token: int
    component_type: str
    component_id: str
    priority: str
    label: str
    cancel: Callable[[], None] | None


class ResourceGuardService:
    def __init__(self) -> None:
        self._operations: dict[int, GuardOperation] = {}
        self._next_token = 1
        self._state = "normal"
        self._last_sample: dict = {"ram_percent": 0.0, "swap_percent": 0.0, "total_bytes": 0}

    @staticmethod
    def sample() -> dict:
        if psutil is None:
            return {"ram_percent": 0.0, "swap_percent": 0.0, "total_bytes": 0}
        ram, swap = psutil.virtual_memory(), psutil.swap_memory()
        return {"ram_percent": round(float(ram.percent), 1), "swap_percent": round(float(swap.percent), 1), "total_bytes": int(ram.total)}

    async def settings(self, db: AsyncSession) -> ResourceGuardSettings:
        item = await db.get(ResourceGuardSettings, 1)
        if item is None:
            item = ResourceGuardSettings(id=1)
            db.add(item)
            await db.flush()
        return item

    async def status(self, db: AsyncSession) -> dict:
        settings = await self.settings(db)
        sample = self.sample()
        self._last_sample = sample
        enabled = settings.mode == "enabled" or (settings.mode == "auto" and sample["total_bytes"] < 2 * 1024 ** 3)
        active = enabled and sample["ram_percent"] >= settings.memory_limit_percent
        state = "active" if active and self._operations else ("unmanaged_warning" if active else "normal")
        return {"mode": settings.mode, "enabled": enabled, "is_low_ram": sample["total_bytes"] < 2 * 1024 ** 3, "limit_percent": settings.memory_limit_percent, "state": state, "ram_percent": sample["ram_percent"], "swap_percent": sample["swap_percent"], "operations": [self._operation_data(item) for item in self._operations.values()]}

    async def save_settings(self, db: AsyncSession, mode: str, limit_percent: int) -> dict:
        if mode not in {"auto", "enabled", "disabled"}:
            raise ValueError("Guard mode must be auto, enabled, or disabled.")
        if not 75 <= limit_percent <= 95:
            raise ValueError("Safe memory limit must be between 75% and 95%.")
        settings = await self.settings(db)
        settings.mode, settings.memory_limit_percent = mode, limit_percent
        await db.flush()
        return await self.status(db)

    async def priority(self, db: AsyncSession, component_type: str, component_id: str) -> str:
        row = await db.scalar(select(ResourceGuardPriority).where(ResourceGuardPriority.component_type == component_type, ResourceGuardPriority.component_id == component_id))
        return row.priority if row else _DEFAULTS.get(component_type, "background")

    async def save_priority(self, db: AsyncSession, component_type: str, component_id: str, priority: str) -> None:
        if component_type not in _DEFAULTS or not component_id or priority not in PRIORITIES:
            raise ValueError("Invalid Resource Guard priority override.")
        row = await db.scalar(select(ResourceGuardPriority).where(ResourceGuardPriority.component_type == component_type, ResourceGuardPriority.component_id == component_id))
        if row is None:
            db.add(ResourceGuardPriority(component_type=component_type, component_id=component_id, priority=priority))
        else:
            row.priority = priority
        await db.flush()

    async def allow_start(self, db: AsyncSession) -> None:
        status = await self.status(db)
        if status["state"] == "active":
            raise RuntimeError(f"Resource Guard is active ({status['ram_percent']}% RAM; safe limit {status['limit_percent']}%).")

    def register(self, component_type: str, component_id: str, priority: str, label: str, cancel: Callable[[], None] | None = None) -> int:
        token = self._next_token
        self._next_token += 1
        self._operations[token] = GuardOperation(token, component_type, component_id, priority, label, cancel)
        return token

    def unregister(self, token: int) -> None:
        self._operations.pop(token, None)

    async def monitor(self) -> None:
        while True:
            try:
                await self._check_once()
            except Exception:
                pass
            await asyncio.sleep(5)

    async def _check_once(self) -> None:
        from database import AsyncSessionLocal
        async with AsyncSessionLocal() as db:
            status = await self.status(db)
            state = status["state"]
            if state != self._state:
                db.add(Notification(type="warning" if state != "normal" else "success", message=self._message(status)))
                await db.commit()
                self._state = state
            if state == "active":
                candidates = sorted(self._operations.values(), key=lambda item: PRIORITIES.index(item.priority), reverse=True)
                if candidates and candidates[0].cancel:
                    candidates[0].cancel()

    @staticmethod
    def _operation_data(item: GuardOperation) -> dict:
        return {"component_type": item.component_type, "component_id": item.component_id, "priority": item.priority, "label": item.label}

    @staticmethod
    def _message(status: dict) -> str:
        if status["state"] == "normal":
            return "Resource Guard returned to normal. Heavy panel actions are available again."
        action = "A panel-managed heavy operation is being cancelled." if status["state"] == "active" else "Usage source is unmanaged; the panel is notifying only."
        return f"Resource Guard: {status['ram_percent']}% RAM used (safe limit {status['limit_percent']}%). {action}"


resource_guard_service = ResourceGuardService()
