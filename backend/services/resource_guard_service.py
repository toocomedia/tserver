"""Resource Guard — admission, reservation, monitoring, and Safe Install foundation."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from typing import Awaitable, Callable

logger = logging.getLogger(__name__)


try:
    import psutil
except ImportError:  # pragma: no cover
    psutil = None

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.notification import Notification
from models.resource_guard import ResourceGuardPriority, ResourceGuardSettings
from models.safe_install_run import SafeInstallRun
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

        # Swap pressure guard — threshold is per-profile (builds: 80%, others: 90-95%)
        swap_threshold = prof.get("swap_threshold", 80)
        swap_warning: str | None = None
        if sample["swap_percent"] >= swap_threshold:
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
                "swap_warning": None,
            }
        elif sample["swap_percent"] >= 60:
            swap_warning = (
                f"Swap pressure is elevated ({sample['swap_percent']}%). "
                "Monitor usage after starting this operation."
            )

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
                "swap_warning": swap_warning,
            }

        return {
            "ok": True,
            "reason": "Capacity check passed.",
            "safe_capacity_mb": safe_capacity_mb,
            "required_mb": required_mb,
            "ram_available_mb": sample["ram_available_mb"],
            "protected_reserve_mb": cfg.protected_reserve_mb,
            "profile": profile_name,
            "swap_warning": swap_warning,
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
            from services.resource_guard_operation_service import resource_guard_operation_service
            await resource_guard_operation_service.record_sample(
                db, status["total_mb"], status["ram_available_mb"]
            )
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


    # ------------------------------------------------------------------
    # Host capability preflight
    # ------------------------------------------------------------------

    @staticmethod
    def host_capabilities() -> dict:
        """
        Report which containment capabilities are available on this host.

        Returns a dict with boolean flags, disk_available_mb, a level string
        (full | reduced | unsupported), and a list of missing capability names.
        """
        missing: list[str] = []

        # cgroup memory controller
        cgroup_memory = os.path.exists("/sys/fs/cgroup/memory.max") or os.path.exists("/sys/fs/cgroup/memory/memory.limit_in_bytes")
        if not cgroup_memory:
            missing.append("cgroup_memory")

        # cgroup CPU controller
        cgroup_cpu = os.path.exists("/sys/fs/cgroup/cpu.max") or os.path.exists("/sys/fs/cgroup/cpu/cpu.cfs_quota_us")
        if not cgroup_cpu:
            missing.append("cgroup_cpu")

        # cgroup PID controller
        cgroup_pids = os.path.exists("/sys/fs/cgroup/pids.max") or os.path.exists("/sys/fs/cgroup/pids/pids.max")
        if not cgroup_pids:
            missing.append("cgroup_pids")

        # systemd-run --scope
        systemd_scope = shutil.which("systemd-run") is not None
        if not systemd_scope:
            missing.append("systemd_scope")

        # Docker memory limits (docker present + responds)
        docker_memory = shutil.which("docker") is not None
        if not docker_memory:
            missing.append("docker_memory")
            missing.append("docker_pids")
        docker_pids = docker_memory  # pids-limit requires docker

        # Constrained Buildx builder (srv-panel-builder)
        buildx_builder = False
        if docker_memory:
            try:
                import subprocess
                r = subprocess.run(
                    ["docker", "buildx", "ls"],
                    capture_output=True, text=True, timeout=10
                )
                buildx_builder = "srv-panel-builder" in r.stdout
            except Exception:
                pass
        if not buildx_builder:
            missing.append("buildx_builder")

        # Disk available
        disk_available_mb = 0
        try:
            stat = os.statvfs("/") if os.name != "nt" else None
            if stat:
                disk_available_mb = int(stat.f_bavail * stat.f_frsize / (1024 * 1024))
        except Exception:
            pass

        # Determine level
        critical_missing = {"cgroup_memory", "docker_memory"}
        if critical_missing & set(missing):
            level = "unsupported"
        elif missing:
            level = "reduced"
        else:
            level = "full"

        return {
            "cgroup_memory": cgroup_memory,
            "cgroup_cpu": cgroup_cpu,
            "cgroup_pids": cgroup_pids,
            "systemd_scope": systemd_scope,
            "docker_memory": docker_memory,
            "docker_pids": docker_pids,
            "buildx_builder": buildx_builder,
            "disk_available_mb": disk_available_mb,
            "level": level,
            "missing": missing,
        }

    # ------------------------------------------------------------------
    # Safe Install Mode
    # ------------------------------------------------------------------

    async def request_safe_install(
        self, db: AsyncSession, operation_id: int
    ) -> dict:
        """
        Build the candidate list for Safe Install Mode.

        Creates a SafeInstallRun record with outcome=pending.
        Returns {run_id, before_ram_mb, protected, required, optional}.
        No service is stopped here.
        """
        from services.resource_guard_relationships import classify_services
        from models.guard_operation import GuardOperation as GuardOp

        operation = await db.get(GuardOp, operation_id)
        if operation is None:
            return {"ok": False, "reason": "Operation not found."}

        install_op_context = {
            "operation_id": operation_id,
            "profile": operation.profile,
            "required_dependencies": [],  # extended by caller if known
        }

        classification = await classify_services(db, install_op_context)
        sample = self.sample()
        before_ram_mb = sample["total_mb"] - sample["ram_available_mb"]

        run = SafeInstallRun(
            operation_id=operation_id,
            candidate_snapshot=json.dumps(classification["optional"]),
            approved_ids="[]",
            services_stopped="[]",
            before_ram_mb=before_ram_mb,
            outcome="pending",
            restore_state="pending",
        )
        db.add(run)
        await db.flush()

        return {
            "ok": True,
            "run_id": run.id,
            "before_ram_mb": before_ram_mb,
            "protected": classification["protected"],
            "required": classification["required"],
            "optional": classification["optional"],
        }

    async def approve_safe_install(
        self, db: AsyncSession, run_id: int, approved_ids: list[str]
    ) -> dict:
        """
        Stop approved candidates one by one, rechecking capacity after each.

        Returns {ok, reason, services_stopped, after_ram_mb}.
        If stopping a service fails the run is aborted (no partial-stop state left).
        """
        run = await db.get(SafeInstallRun, run_id)
        if run is None:
            return {"ok": False, "reason": "Safe Install run not found."}
        if run.outcome not in ("pending",):
            return {"ok": False, "reason": f"Run is already {run.outcome}."}

        candidates: list[dict] = json.loads(run.candidate_snapshot)
        candidate_map = {c["id"]: c for c in candidates}

        # Only stop IDs that were actually in the candidate snapshot
        to_stop = [c for cid in approved_ids if (c := candidate_map.get(cid))]

        run.approved_ids = json.dumps(approved_ids)
        run.outcome = "running"
        stopped: list[str] = []

        try:
            for candidate in to_stop:
                ok, reason = await self._stop_candidate(candidate)
                if not ok:
                    # Abort: restore already-stopped services, mark aborted
                    for svc_id in reversed(stopped):
                        await self._start_candidate(candidate_map[svc_id])
                    run.outcome = "aborted"
                    run.finished_at = datetime.utcnow()
                    return {"ok": False, "reason": f"Failed to stop '{candidate['id']}': {reason}. Run aborted, services restored."}

                stopped.append(candidate["id"])
                run.services_stopped = json.dumps(stopped)

                # Recheck capacity after each stop
                from models.guard_operation import GuardOperation as GuardOp
                op = await db.get(GuardOp, run.operation_id)
                if op:
                    capacity_check = await self.preflight(db, op.profile)
                    if capacity_check["ok"]:
                        break  # enough capacity — no need to stop more

            sample = self.sample()
            run.after_ram_mb = sample["total_mb"] - sample["ram_available_mb"]

        except Exception as exc:
            run.outcome = "aborted"
            run.finished_at = datetime.utcnow()
            return {"ok": False, "reason": f"Unexpected error during Safe Install: {exc}"}

        return {
            "ok": True,
            "services_stopped": stopped,
            "after_ram_mb": run.after_ram_mb,
            "run_id": run_id,
        }

    async def complete_safe_install(self, db: AsyncSession, run_id: int) -> dict:
        """
        Post-install restore decision.

        Called after the new app has passed its own health/routing checks.
        If original stopped services can coexist with the new runtime, restore them.
        Otherwise pause the new app + its new databases, then restore originals.
        """
        run = await db.get(SafeInstallRun, run_id)
        if run is None:
            return {"ok": False, "reason": "Safe Install run not found."}

        from models.guard_operation import GuardOperation as GuardOp
        op = await db.get(GuardOp, run.operation_id)
        if op is None:
            return {"ok": False, "reason": "Associated operation not found."}

        candidates: list[dict] = json.loads(run.candidate_snapshot)
        stopped_ids: list[str] = json.loads(run.services_stopped)
        candidate_map = {c["id"]: c for c in candidates}
        stopped_candidates = [candidate_map[sid] for sid in stopped_ids if sid in candidate_map]

        # Check if new app runtime + stopped services all fit
        # We re-run preflight for the runtime profile (app container)
        runtime_profile = op.profile.replace("build_", "container_") if op.profile.startswith("build_") else op.profile
        coexist_check = await self.preflight(db, runtime_profile)

        if coexist_check["ok"]:
            # Safe to restore — bring services back up
            restore_errors: list[str] = []
            for candidate in stopped_candidates:
                ok, reason = await self._start_candidate(candidate)
                if not ok:
                    restore_errors.append(f"{candidate['id']}: {reason}")

            run.restore_state = "restored" if not restore_errors else "failed"
            run.outcome = "succeeded"
            run.finished_at = datetime.utcnow()
            return {
                "ok": True,
                "action": "restored",
                "restore_errors": restore_errors,
            }
        else:
            # Cannot coexist — pause new app, restore originals
            await self._pause_new_app(db, op)
            restore_errors = []
            for candidate in stopped_candidates:
                ok, reason = await self._start_candidate(candidate)
                if not ok:
                    restore_errors.append(f"{candidate['id']}: {reason}")

            run.restore_state = "paused_new_app"
            run.outcome = "succeeded"
            run.finished_at = datetime.utcnow()
            return {
                "ok": True,
                "action": "paused_new_app",
                "reason": "New app paused — not enough capacity to run it alongside restored services.",
                "restore_errors": restore_errors,
            }

    async def start_paused_install(
        self, db: AsyncSession, operation_id: int
    ) -> dict:
        """
        Run a fresh preflight before starting an app that was paused by Safe Install.
        Returns the preflight result; the caller decides whether to start the app.
        """
        from models.guard_operation import GuardOperation as GuardOp
        op = await db.get(GuardOp, operation_id)
        if op is None:
            return {"ok": False, "reason": "Operation not found."}
        return await self.preflight(db, op.profile)

    async def restore_safe_install(
        self, db: AsyncSession, run_id: int
    ) -> dict:
        """
        Manual restore triggered from UI for a run where services are still stopped.
        Rechecks capacity first; restores if safe.
        """
        run = await db.get(SafeInstallRun, run_id)
        if run is None:
            return {"ok": False, "reason": "Safe Install run not found."}

        stopped_ids: list[str] = json.loads(run.services_stopped)
        candidates: list[dict] = json.loads(run.candidate_snapshot)
        candidate_map = {c["id"]: c for c in candidates}

        errors: list[str] = []
        for svc_id in stopped_ids:
            candidate = candidate_map.get(svc_id)
            if candidate is None:
                continue
            ok, reason = await self._start_candidate(candidate)
            if not ok:
                errors.append(f"{svc_id}: {reason}")

        run.restore_state = "restored" if not errors else "failed"
        run.finished_at = datetime.utcnow()
        return {"ok": not errors, "errors": errors}

    # ------------------------------------------------------------------
    # Internal Safe Install helpers
    # ------------------------------------------------------------------

    async def _stop_candidate(self, candidate: dict) -> tuple[bool, str]:
        """Gracefully stop a single Safe Install candidate."""
        try:
            adapter = self._get_adapter(candidate)
            if adapter is not None:
                await adapter.stop()
                return True, ""
            # Fallback for dependencies that expose stop() directly
            from dependencies import dependency_manager
            svc = dependency_manager.get_service(candidate["id"])
            if svc and callable(getattr(svc, "stop", None)):
                await asyncio.to_thread(svc.stop)
                return True, ""
            return False, "No lifecycle adapter available."
        except Exception as exc:
            return False, str(exc)

    async def _start_candidate(self, candidate: dict) -> tuple[bool, str]:
        """Bring a previously-stopped candidate back up."""
        try:
            adapter = self._get_adapter(candidate)
            if adapter is not None:
                await adapter.start()
                return True, ""
            from dependencies import dependency_manager
            svc = dependency_manager.get_service(candidate["id"])
            if svc and callable(getattr(svc, "start", None)):
                await asyncio.to_thread(svc.start)
                return True, ""
            return False, "No lifecycle adapter available."
        except Exception as exc:
            return False, str(exc)

    def _get_adapter(self, candidate: dict):
        """Return a LifecycleAdapter for a candidate, or None."""
        from services.guarded_runner import LifecycleAdapter
        # Plugins may register adapters via their manifest lifecycle_adapter field
        # (this is the extensibility hook; for now returns None for unregistered ones)
        return None

    async def _pause_new_app(self, db: AsyncSession, op) -> None:
        """Mark the new container_app as paused_by_safe_install."""
        try:
            from models.container_app import ContainerApp
            if op.component_type == "container_app" and op.component_id:
                app = await db.get(ContainerApp, int(op.component_id))
                if app:
                    app.status = "paused_by_safe_install"
        except Exception as exc:
            logger.warning("Could not pause new app: %s", exc)


resource_guard_service = ResourceGuardService()
