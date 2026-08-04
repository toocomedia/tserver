"""Resource Guard — admission, reservation, monitoring, and Safe Install foundation."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Awaitable, Callable

try:
    import psutil
except ImportError:  # pragma: no cover
    psutil = None

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.notification import Notification
from models.resource_guard import ResourceGuardPriority, ResourceGuardSettings
from services.resource_guard_profiles import PROFILES

PRIORITIES = ("high", "normal", "background")
_DEFAULTS = {
    "hosted_app": "high",
    "container_app": "high",
    "plugin": "normal",
    "dependency": "normal",
}


@dataclass
class GuardOperation:
    token: int
    component_type: str
    component_id: str
    priority: str
    label: str
    profile: str
    reserved_mb: int
    cancel: Callable[[], None] | None


class ResourceGuardService:
    def __init__(self) -> None:
        self._operations: dict[int, GuardOperation] = {}
        self._next_token = 1
        self._state = "normal"
        self._last_sample: dict = {}

    # ------------------------------------------------------------------
    # Sampling
    # ------------------------------------------------------------------

    @staticmethod
    def sample() -> dict:
        if psutil is None:
            return {
                "ram_percent": 0.0,
                "ram_available_mb": 9999,
                "swap_percent": 0.0,
                "total_bytes": 0,
                "total_mb": 0,
            }
        ram = psutil.virtual_memory()
        swap = psutil.swap_memory()
        return {
            "ram_percent": round(float(ram.percent), 1),
            "ram_available_mb": int(ram.available // (1024 * 1024)),
            "swap_percent": round(float(swap.percent), 1),
            "total_bytes": int(ram.total),
            "total_mb": int(ram.total // (1024 * 1024)),
        }

    # ------------------------------------------------------------------
    # Settings helpers
    # ------------------------------------------------------------------

    async def settings(self, db: AsyncSession) -> ResourceGuardSettings:
        item = await db.get(ResourceGuardSettings, 1)
        if item is None:
            item = ResourceGuardSettings(id=1)
            db.add(item)
            await db.flush()
        return item

    async def status(self, db: AsyncSession) -> dict:
        cfg = await self.settings(db)
        sample = self.sample()
        self._last_sample = sample
        is_low_ram = sample["total_bytes"] < 2 * 1024 ** 3
        enabled = cfg.mode == "enabled" or (cfg.mode == "auto" and is_low_ram)
        active = enabled and sample["ram_percent"] >= cfg.memory_limit_percent
        state = "active" if active and self._operations else (
            "unmanaged_warning" if active else "normal"
        )
        return {
            "mode": cfg.mode,
            "enabled": enabled,
            "is_low_ram": is_low_ram,
            "limit_percent": cfg.memory_limit_percent,
            "protected_reserve_mb": cfg.protected_reserve_mb,
            "build_concurrency": cfg.build_concurrency,
            "state": state,
            "ram_percent": sample["ram_percent"],
            "ram_available_mb": sample["ram_available_mb"],
            "swap_percent": sample["swap_percent"],
            "total_mb": sample["total_mb"],
            "operations": [self._operation_data(op) for op in self._operations.values()],
        }

    async def save_settings(
        self,
        db: AsyncSession,
        mode: str,
        limit_percent: int,
        protected_reserve_mb: int | None = None,
    ) -> dict:
        if mode not in {"auto", "enabled", "disabled"}:
            raise ValueError("Guard mode must be auto, enabled, or disabled.")
        if not 75 <= limit_percent <= 95:
            raise ValueError("Safe memory limit must be between 75% and 95%.")
        cfg = await self.settings(db)
        cfg.mode = mode
        cfg.memory_limit_percent = limit_percent
        if protected_reserve_mb is not None:
            if not 100 <= protected_reserve_mb <= 2048:
                raise ValueError("Protected reserve must be 100–2048 MB.")
            cfg.protected_reserve_mb = protected_reserve_mb
        await db.flush()
        return await self.status(db)

    # ------------------------------------------------------------------
    # Priority helpers
    # ------------------------------------------------------------------

    async def priority(self, db: AsyncSession, component_type: str, component_id: str) -> str:
        row = await db.scalar(
            select(ResourceGuardPriority).where(
                ResourceGuardPriority.component_type == component_type,
                ResourceGuardPriority.component_id == component_id,
            )
        )
        return row.priority if row else _DEFAULTS.get(component_type, "background")

    async def save_priority(
        self, db: AsyncSession, component_type: str, component_id: str, priority: str
    ) -> None:
        if component_type not in _DEFAULTS or not component_id or priority not in PRIORITIES:
            raise ValueError("Invalid Resource Guard priority override.")
        row = await db.scalar(
            select(ResourceGuardPriority).where(
                ResourceGuardPriority.component_type == component_type,
                ResourceGuardPriority.component_id == component_id,
            )
        )
        if row is None:
            db.add(
                ResourceGuardPriority(
                    component_type=component_type,
                    component_id=component_id,
                    priority=priority,
                )
            )
        else:
            row.priority = priority
        await db.flush()

    # ------------------------------------------------------------------
    # Admission (Slice 1: capacity-based preflight)
    # ------------------------------------------------------------------

    def _active_reservation_mb(self) -> int:
        return sum(op.reserved_mb for op in self._operations.values())

    def _active_builds(self) -> int:
        return sum(
            1 for op in self._operations.values()
            if op.profile.startswith("build_")
        )

    async def preflight(self, db: AsyncSession, profile_name: str) -> dict:
        """
        Return admission decision for a new operation with *profile_name*.

        Keys: ok (bool), reason (str), safe_capacity_mb, required_mb,
              ram_available_mb, protected_reserve_mb.
        """
        cfg = await self.settings(db)
        sample = self.sample()

        # Guard disabled → always allow (containment still applies)
        if cfg.mode == "disabled":
            ram_mb = PROFILES.get(profile_name, {}).get("ram_mb", 0)
            return {
                "ok": True,
                "reason": "Resource Guard is disabled.",
                "safe_capacity_mb": 9999,
                "required_mb": ram_mb,
                "ram_available_mb": sample["ram_available_mb"],
                "protected_reserve_mb": cfg.protected_reserve_mb,
                "profile": profile_name,
            }

        is_low_ram = sample["total_bytes"] < 2 * 1024 ** 3
        enabled = cfg.mode == "enabled" or (cfg.mode == "auto" and is_low_ram)

        if not enabled:
            ram_mb = PROFILES.get(profile_name, {}).get("ram_mb", 0)
            return {
                "ok": True,
                "reason": "Resource Guard is not active on this host.",
                "safe_capacity_mb": 9999,
                "required_mb": ram_mb,
                "ram_available_mb": sample["ram_available_mb"],
                "protected_reserve_mb": cfg.protected_reserve_mb,
                "profile": profile_name,
            }

        prof = PROFILES.get(profile_name)
        if prof is None:
            return {
                "ok": False,
                "reason": f"Unknown resource profile '{profile_name}'.",
                "safe_capacity_mb": 0,
                "required_mb": 0,
                "ram_available_mb": sample["ram_available_mb"],
                "protected_reserve_mb": cfg.protected_reserve_mb,
                "profile": profile_name,
            }

        safe_capacity_mb = sample["ram_available_mb"] - cfg.protected_reserve_mb
        active_reserved_mb = self._active_reservation_mb()
        required_mb = prof["ram_mb"] + active_reserved_mb

        # One-build concurrency check
        if profile_name.startswith("build_") and self._active_builds() >= cfg.build_concurrency:
            return {
                "ok": False,
                "reason": (
                    f"A build is already running. Only {cfg.build_concurrency} "
                    "concurrent build(s) are allowed."
                ),
                "safe_capacity_mb": safe_capacity_mb,
                "required_mb": required_mb,
                "ram_available_mb": sample["ram_available_mb"],
                "protected_reserve_mb": cfg.protected_reserve_mb,
                "profile": profile_name,
            }

        # Swap pressure guard
        if sample["swap_percent"] >= 80:
            return {
                "ok": False,
                "reason": (
                    f"Swap pressure is critical ({sample['swap_percent']}%). "
                    "Wait for pressure to fall before starting a heavy operation."
                ),
                "safe_capacity_mb": safe_capacity_mb,
                "required_mb": required_mb,
                "ram_available_mb": sample["ram_available_mb"],
                "protected_reserve_mb": cfg.protected_reserve_mb,
                "profile": profile_name,
            }

        if safe_capacity_mb < required_mb:
            missing = required_mb - safe_capacity_mb
            return {
                "ok": False,
                "reason": (
                    f"Not enough safe memory. Available: {sample['ram_available_mb']} MB, "
                    f"protected reserve: {cfg.protected_reserve_mb} MB, "
                    f"safe capacity: {safe_capacity_mb} MB, "
                    f"required (profile + active reservations): {required_mb} MB "
                    f"({missing} MB short). "
                    "Free memory or use Safe Install Mode if optional services can be paused."
                ),
                "safe_capacity_mb": safe_capacity_mb,
                "required_mb": required_mb,
                "ram_available_mb": sample["ram_available_mb"],
                "protected_reserve_mb": cfg.protected_reserve_mb,
                "profile": profile_name,
            }

        return {
            "ok": True,
            "reason": "Capacity check passed.",
            "safe_capacity_mb": safe_capacity_mb,
            "required_mb": required_mb,
            "ram_available_mb": sample["ram_available_mb"],
            "protected_reserve_mb": cfg.protected_reserve_mb,
            "profile": profile_name,
        }

    # Keep legacy allow_start for any callers not yet migrated.
    async def allow_start(self, db: AsyncSession) -> None:
        result = await self.preflight(db, "native_light")
        if not result["ok"]:
            raise RuntimeError(result["reason"])

    # ------------------------------------------------------------------
    # Operation registration
    # ------------------------------------------------------------------

    def register(
        self,
        component_type: str,
        component_id: str,
        priority: str,
        label: str,
        cancel: Callable[[], None] | None = None,
        *,
        profile: str = "native_light",
    ) -> int:
        token = self._next_token
        self._next_token += 1
        reserved_mb = PROFILES.get(profile, {}).get("ram_mb", 0)
        self._operations[token] = GuardOperation(
            token=token,
            component_type=component_type,
            component_id=component_id,
            priority=priority,
            label=label,
            profile=profile,
            reserved_mb=reserved_mb,
            cancel=cancel,
        )
        return token

    def unregister(self, token: int) -> None:
        self._operations.pop(token, None)

    # ------------------------------------------------------------------
    # Background monitor
    # ------------------------------------------------------------------

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
                db.add(
                    Notification(
                        type="warning" if state != "normal" else "success",
                        message=self._message(status),
                    )
                )
                await db.commit()
                self._state = state
            if state == "active":
                candidates = sorted(
                    self._operations.values(),
                    key=lambda op: PRIORITIES.index(op.priority),
                    reverse=True,
                )
                if candidates and candidates[0].cancel:
                    candidates[0].cancel()

    @staticmethod
    def _operation_data(op: GuardOperation) -> dict:
        return {
            "component_type": op.component_type,
            "component_id": op.component_id,
            "priority": op.priority,
            "label": op.label,
            "profile": op.profile,
            "reserved_mb": op.reserved_mb,
        }

    @staticmethod
    def _message(status: dict) -> str:
        if status["state"] == "normal":
            return "Resource Guard returned to normal. Heavy panel actions are available again."
        action = (
            "A panel-managed heavy operation is being cancelled."
            if status["state"] == "active"
            else "Usage source is unmanaged; the panel is notifying only."
        )
        return (
            f"Resource Guard: {status['ram_percent']}% RAM used "
            f"({status['ram_available_mb']} MB available, "
            f"safe limit {status['limit_percent']}%). {action}"
        )


resource_guard_service = ResourceGuardService()
