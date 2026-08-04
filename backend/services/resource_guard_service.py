"""Resource Guard capacity checks and persistent operation monitoring."""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

try:
    import psutil
except ImportError:  # pragma: no cover
    psutil = None

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.guard_operation import GuardOperation
from models.notification import Notification
from models.resource_guard import ResourceGuardPriority, ResourceGuardSettings
from services.resource_guard_profiles import PROFILES

PRIORITIES = ("high", "normal", "background")
ACTIVE_STATUSES = ("running", "cancelling")
_DEFAULTS = {"hosted_app": "high", "container_app": "high", "plugin": "normal", "dependency": "normal"}


@dataclass
class _LegacyOperation:
    token: int
    profile: str
    reserved_mb: int


class ResourceGuardService:
    def __init__(self) -> None:
        self._callbacks: dict[int, Callable[[], None]] = {}
        self._operations: dict[int, _LegacyOperation] = {}
        self._next_token = 1
        self._state = "normal"

    @staticmethod
    def sample() -> dict:
        if psutil is None:
            return {"ram_percent": 0.0, "ram_available_mb": 9999, "swap_percent": 0.0, "total_bytes": 0, "total_mb": 0}
        ram, swap = psutil.virtual_memory(), psutil.swap_memory()
        return {"ram_percent": round(float(ram.percent), 1), "ram_available_mb": int(ram.available // 1048576), "swap_percent": round(float(swap.percent), 1), "total_bytes": int(ram.total), "total_mb": int(ram.total // 1048576)}

    async def settings(self, db: AsyncSession) -> ResourceGuardSettings:
        item = await db.get(ResourceGuardSettings, 1)
        if item is None:
            item = ResourceGuardSettings(id=1)
            db.add(item)
            await db.flush()
        return item

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

    async def status(self, db: AsyncSession) -> dict:
        cfg, sample = await self.settings(db), self.sample()
        enabled = cfg.mode == "enabled" or (cfg.mode == "auto" and sample["total_bytes"] < 2 * 1024 ** 3)
        operations = list((await db.scalars(select(GuardOperation).where(GuardOperation.status.in_((*ACTIVE_STATUSES, "queued"))).order_by(GuardOperation.created_at, GuardOperation.id))).all())
        active = enabled and sample["ram_percent"] >= cfg.memory_limit_percent
        return {"mode": cfg.mode, "enabled": enabled, "is_low_ram": sample["total_bytes"] < 2 * 1024 ** 3, "limit_percent": cfg.memory_limit_percent, "protected_reserve_mb": cfg.protected_reserve_mb, "build_concurrency": cfg.build_concurrency, "state": "active" if active and operations else ("unmanaged_warning" if active else "normal"), **sample, "operations": [self.operation_data(op) for op in operations]}

    async def save_settings(self, db: AsyncSession, mode: str, limit_percent: int, protected_reserve_mb: int | None = None) -> dict:
        if mode not in {"auto", "enabled", "disabled"} or not 75 <= limit_percent <= 95:
            raise ValueError("Guard settings are invalid.")
        cfg = await self.settings(db)
        cfg.mode, cfg.memory_limit_percent = mode, limit_percent
        if protected_reserve_mb is not None:
            if not 100 <= protected_reserve_mb <= 2048:
                raise ValueError("Protected reserve must be 100–2048 MB.")
            cfg.protected_reserve_mb = protected_reserve_mb
        await db.flush()
        return await self.status(db)

    async def preflight(self, db: AsyncSession, profile_name: str) -> dict:
        cfg, sample, prof = await self.settings(db), self.sample(), PROFILES.get(profile_name)
        if prof is None:
            return self._result(False, f"Unknown resource profile '{profile_name}'.", 0, 0, sample, cfg, profile_name)
        enabled = cfg.mode == "enabled" or (cfg.mode == "auto" and sample["total_bytes"] < 2 * 1024 ** 3)
        if not enabled:
            return self._result(True, "Resource Guard is not active on this host.", 9999, prof["ram_mb"], sample, cfg, profile_name)
        active = list((await db.scalars(select(GuardOperation).where(GuardOperation.status.in_(ACTIVE_STATUSES)))).all())
        reserved = sum(op.reserved_mb for op in active) + self._active_reservation_mb()
        safe, required = sample["ram_available_mb"] - cfg.protected_reserve_mb, prof["ram_mb"] + reserved
        builds = sum(1 for op in active if op.profile.startswith("build_")) + self._active_builds()
        if profile_name.startswith("build_") and builds >= cfg.build_concurrency:
            return self._result(False, f"A build is already running. Only {cfg.build_concurrency} concurrent build(s) are allowed.", safe, required, sample, cfg, profile_name, queueable=True)
        if sample["swap_percent"] >= 80:
            return self._result(False, f"Swap pressure is critical ({sample['swap_percent']}%). Wait before starting a heavy operation.", safe, required, sample, cfg, profile_name)
        if safe < required:
            return self._result(False, f"Not enough safe memory. Available: {sample['ram_available_mb']} MB, protected reserve: {cfg.protected_reserve_mb} MB, safe capacity: {safe} MB, required: {required} MB.", safe, required, sample, cfg, profile_name)
        return self._result(True, "Capacity check passed.", safe, required, sample, cfg, profile_name)

    async def allow_start(self, db: AsyncSession) -> None:
        result = await self.preflight(db, "native_light")
        if not result["ok"]:
            raise RuntimeError(result["reason"])

    def register(self, component_type: str, component_id: str, priority: str, label: str, cancel: Callable[[], None] | None = None, *, profile: str = "native_light") -> int:
        token = self._next_token
        self._next_token += 1
        self._operations[token] = _LegacyOperation(token, profile, PROFILES[profile]["ram_mb"])
        return token

    def unregister(self, token: int) -> None:
        self._operations.pop(token, None)

    def _active_reservation_mb(self) -> int:
        return sum(operation.reserved_mb for operation in self._operations.values())

    def _active_builds(self) -> int:
        return sum(1 for operation in self._operations.values() if operation.profile.startswith("build_"))

    async def create_operation(self, db: AsyncSession, *, component_type: str, component_id: str, operation_type: str, priority: str, label: str, profile: str, status: str, deployment_id: int | None = None, preflight: dict | None = None, cancel: Callable[[], None] | None = None) -> GuardOperation:
        op = GuardOperation(component_type=component_type, component_id=component_id, operation_type=operation_type, priority=priority, label=label, profile=profile, reserved_mb=PROFILES[profile]["ram_mb"], status=status, deployment_id=deployment_id, preflight_result=json.dumps(preflight) if preflight else None, started_at=datetime.utcnow() if status == "running" else None)
        db.add(op)
        await db.flush()
        if status == "queued":
            await self._reindex_queue(db)
        if cancel:
            self._callbacks[op.id] = cancel
        return op

    async def finish_operation(self, db: AsyncSession, operation_id: int, status: str) -> None:
        op = await db.get(GuardOperation, operation_id)
        if op is None or op.status in {"succeeded", "failed", "cancelled", "interrupted"}:
            return
        op.status, op.finished_at, op.queue_position = status, datetime.utcnow(), None
        self._callbacks.pop(operation_id, None)
        await self._reindex_queue(db)

    async def cancel_operation(self, db: AsyncSession, operation_id: int, reason: str = "Stopped by the user.") -> GuardOperation | None:
        op = await db.get(GuardOperation, operation_id)
        if op is None:
            return None
        if op.status == "queued":
            op.status, op.finished_at, op.queue_position = "cancelled", datetime.utcnow(), None
            callback = self._callbacks.get(op.id)
            if callback:
                callback()
        elif op.status in ACTIVE_STATUSES:
            op.status, op.cancel_reason = "cancelling", reason
            callback = self._callbacks.get(op.id)
            if callback:
                callback()
        await self._reindex_queue(db)
        return op

    async def queued_operations(self, db: AsyncSession) -> list[GuardOperation]:
        return list((await db.scalars(select(GuardOperation).where(GuardOperation.status == "queued").order_by(GuardOperation.created_at, GuardOperation.id))).all())

    async def recover_interrupted(self, db: AsyncSession) -> None:
        stale = list((await db.scalars(select(GuardOperation).where(GuardOperation.status.in_(ACTIVE_STATUSES)))).all())
        for op in stale:
            op.status, op.finished_at, op.queue_position = "interrupted", datetime.utcnow(), None
        await self._reindex_queue(db)

    async def monitor(self) -> None:
        while True:
            try:
                from database import AsyncSessionLocal
                async with AsyncSessionLocal() as db:
                    status = await self.status(db)
                    await self._record_sample(db, status)
                    if status["state"] != self._state:
                        db.add(Notification(type="warning" if status["state"] != "normal" else "success", message=self._message(status)))
                        self._state = status["state"]
                    if status["state"] == "active":
                        active = list((await db.scalars(select(GuardOperation).where(GuardOperation.status == "running"))).all())
                        if active:
                            await self.cancel_operation(db, sorted(active, key=lambda op: PRIORITIES.index(op.priority), reverse=True)[0].id, "Resource Guard cancelled this operation to protect VPS memory.")
                    await db.commit()
            except Exception:
                pass
            await asyncio.sleep(5)

    async def _record_sample(self, db: AsyncSession, status: dict) -> None:
        used = max(0, status["total_mb"] - status["ram_available_mb"])
        for op in (await db.scalars(select(GuardOperation).where(GuardOperation.status.in_(ACTIVE_STATUSES)))).all():
            op.current_ram_mb, op.current_cpu = used, psutil.cpu_percent() if psutil else 0.0
            op.peak_ram_mb = max(op.peak_ram_mb or 0, used)

    async def _reindex_queue(self, db: AsyncSession) -> None:
        for position, op in enumerate(await self.queued_operations(db), 1):
            op.queue_position = position

    @staticmethod
    def operation_data(op: GuardOperation) -> dict:
        return {key: getattr(op, key) for key in ("id", "component_type", "component_id", "operation_type", "priority", "label", "profile", "reserved_mb", "status", "queue_position", "current_ram_mb", "peak_ram_mb", "current_cpu", "deployment_id", "started_at", "finished_at", "created_at")}

    @staticmethod
    def _result(ok: bool, reason: str, safe: int, required: int, sample: dict, cfg: ResourceGuardSettings, profile: str, queueable: bool = False) -> dict:
        return {"ok": ok, "reason": reason, "safe_capacity_mb": safe, "required_mb": required, "ram_available_mb": sample["ram_available_mb"], "protected_reserve_mb": cfg.protected_reserve_mb, "profile": profile, "queueable": queueable}

    @staticmethod
    def _message(status: dict) -> str:
        if status["state"] == "normal":
            return "Resource Guard returned to normal. Heavy panel actions are available again."
        return f"Resource Guard: {status['ram_percent']}% RAM used ({status['ram_available_mb']} MB available, safe limit {status['limit_percent']}%)."


resource_guard_service = ResourceGuardService()
