"""Registry-backed dependency status and lifecycle orchestration."""
from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path
from typing import Any

from dependencies.registry import DEPENDENCY_REGISTRY
from services.component_state import component_state_store


class DependencyManager:
    def __init__(self) -> None:
        self._services = {
            dep_id: service_class()
            for dep_id, service_class in DEPENDENCY_REGISTRY.items()
        }
        self._metadata = self._load_metadata()
        self._operation_locks = {
            dep_id: threading.Lock() for dep_id in self._services
        }

    @staticmethod
    def _load_metadata() -> dict[str, dict[str, Any]]:
        root = Path(__file__).parent
        metadata: dict[str, dict[str, Any]] = {}
        for dep_id in DEPENDENCY_REGISTRY:
            path = root / dep_id / "dependency.json"
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("id") != dep_id:
                raise RuntimeError(f"Dependency metadata ID mismatch for {dep_id}")
            metadata[dep_id] = data
        return metadata

    def state_components(self) -> list[tuple[str, str, bool, str]]:
        return [("dependency", dep_id, True, "bundled") for dep_id in self._services]

    def get_service(self, dependency_id: str):
        return self._services.get(dependency_id)

    def get_status(
        self,
        dependency_id: str,
        *,
        force: bool = False,
        cached: bool = False,
    ) -> dict[str, Any] | None:
        service = self.get_service(dependency_id)
        if service is None:
            return None
        cached_status = getattr(service, "get_cached_status", None)
        raw_status = (
            cached_status()
            if cached and cached_status is not None
            else service.get_status(force=force)
        )
        status = dict(raw_status) if isinstance(raw_status, dict) else {}
        state = component_state_store.get("dependency", dependency_id)
        status.update(self._metadata.get(dependency_id, {}))
        icon = str(status.get("icon") or "")
        status["icon"] = (
            f"/dependencies/assets/{dependency_id}"
            if icon and "/" not in icon and "\\" not in icon
            else "/static/images/dependency-placeholder.svg"
        )
        # Docker remains the existing controllable daemon; Git/Python are core tools.
        status.setdefault("can_toggle", dependency_id == "docker")
        status["desired_enabled"] = state.desired_enabled
        status["operation"] = state.operation
        # A dependency that owns several independently installed runtimes can
        # report a more precise origin (for example panel-managed, external,
        # or mixed).  Single-runtime dependencies retain the existing state
        # based convention.
        reported_origin = status.get("install_origin")
        status["install_origin"] = (
            str(reported_origin)
            if reported_origin
            else (
                "external"
                if status.get("installed") and state.install_origin == "bundled"
                else state.install_origin
            )
        )
        # Native MariaDB is safe to control only after the panel installed its
        # localhost-only configuration.  A detected external installation is
        # intentionally read-only from the panel.
        if dependency_id == "mariadb" and status["install_origin"] != "panel_managed":
            status["can_toggle"] = False
        update_getter = getattr(service, "get_cached_update_status", None)
        update = (
            update_getter()
            if callable(update_getter)
            else {
                "state": "not_supported",
                "available": False,
                "candidate_version": None,
                "source": None,
                "message": "This dependency does not support panel updates.",
                "last_checked": None,
                "major_change": False,
            }
        )
        status["update"] = update
        status["update_confirmation"] = getattr(service, "update_confirmation", None)
        status["can_check_update"] = bool(
            status.get("installed")
            and status["install_origin"] == "panel_managed"
            and callable(getattr(service, "check_update", None))
        )
        status["can_update"] = bool(
            status["can_check_update"]
            and update.get("available")
            and not update.get("major_change")
            and state.operation == "idle"
            and callable(getattr(service, "update", None))
        )
        expected_offline = not state.desired_enabled and state.operation == "idle"
        status["last_error"] = (
            None
            if status.get("healthy") or expected_offline
            else (state.last_error or status.get("error"))
        )
        # The service's live health is authoritative.  Docker may be started
        # by systemd or directly by an administrator after the panel saved an
        # older "disabled" preference.
        status["effective_state"] = (
            state.operation
            if state.operation != "idle"
            else (
                status.get("state", "unknown")
                if status.get("healthy")
                else ("disabled" if not state.desired_enabled else status.get("state", "unknown"))
            )
        )
        return status

    def get_all_statuses(
        self,
        *,
        force: bool = False,
        cached: bool = False,
    ) -> list[dict[str, Any]]:
        return [
            self.get_status(dep_id, force=force, cached=cached)
            for dep_id in self._services
        ]

    def is_healthy(self, dependency_id: str, *, cached: bool = True) -> bool:
        status = self.get_status(dependency_id, cached=cached)
        # The desired-state flag controls the panel toggle only.  It can be
        # stale after a reboot or manual systemctl action and must not block a
        # plugin when its actual dependency is healthy.
        return bool(status and status["operation"] == "idle" and status["healthy"])

    def get_dependent_plugins(self, dependency_id: str) -> list[dict[str, Any]]:
        from plugins.manager import plugin_manager

        return plugin_manager.get_dependents(dependency_id)

    def precheck(self, dependency_id: str, action: str) -> dict[str, Any] | None:
        service = self.get_service(dependency_id)
        if service is None:
            return None
        dependents = self.get_dependent_plugins(dependency_id)
        containers = service.list_containers() if action == "uninstall" else []
        status = self.get_status(dependency_id, cached=True) or {}
        blocked = action == "uninstall" and bool(dependents)
        reason = None
        if action == "update" and not status.get("can_update"):
            blocked = True
            reason = str((status.get("update") or {}).get("message") or "MariaDB update is unavailable.")
        return {
            "dependency_id": dependency_id,
            "action": action,
            "dependents": dependents,
            "unmanaged_containers": [
                item for item in containers if not item["panel_managed"]
            ],
            "blocked": blocked,
            "reason": reason,
            "update": status.get("update") if action == "update" else None,
        }

    async def toggle(self, dependency_id: str, enabled: bool) -> tuple[bool, str]:
        service = self.get_service(dependency_id)
        lock = self._operation_locks.get(dependency_id)
        if service is None or lock is None:
            return False, "Unknown dependency."
        if enabled:
            from database import AsyncSessionLocal
            from services.resource_guard_service import resource_guard_service
            async with AsyncSessionLocal() as db:
                try:
                    await resource_guard_service.allow_start(db)
                except RuntimeError as exc:
                    return False, str(exc)
        if not self.get_status(dependency_id, force=True).get("can_toggle"):
            return False, "This core runtime cannot be started or stopped from the panel."
        if not lock.acquire(blocking=False):
            return False, "Another dependency operation is already running."

        operation = "enabling" if enabled else "disabling"
        current = component_state_store.get("dependency", dependency_id)
        paused_apps = False
        try:
            await component_state_store.set(
                "dependency",
                dependency_id,
                operation=operation,
                clear_error=True,
            )
            if dependency_id == "postgresql" and not enabled:
                await self._pause_postgresql_apps()
                paused_apps = True
            success, message = await asyncio.to_thread(service.toggle, enabled)
            if not success:
                await component_state_store.set(
                    "dependency",
                    dependency_id,
                    desired_enabled=current.desired_enabled,
                    operation="idle",
                    last_error=message,
                )
                if paused_apps:
                    await self._resume_postgresql_apps()
                return False, message

            await component_state_store.set(
                "dependency",
                dependency_id,
                desired_enabled=enabled,
                operation="idle",
                clear_error=True,
            )
            if dependency_id == "postgresql" and enabled:
                await self._resume_postgresql_apps()
            return True, message
        except Exception as exc:
            await component_state_store.set(
                "dependency",
                dependency_id,
                desired_enabled=current.desired_enabled,
                operation="idle",
                last_error=str(exc),
            )
            if paused_apps:
                await self._resume_postgresql_apps()
            return False, str(exc)
        finally:
            lock.release()

    @staticmethod
    async def _pause_postgresql_apps() -> None:
        from database import AsyncSessionLocal
        from services import app_dependency_service

        async with AsyncSessionLocal() as db:
            await app_dependency_service.ensure_dependents_idle(db, "postgresql")
            await app_dependency_service.pause_dependents(db, "postgresql")
            await db.commit()

    @staticmethod
    async def _resume_postgresql_apps() -> None:
        from database import AsyncSessionLocal
        from services import app_dependency_service

        async with AsyncSessionLocal() as db:
            await app_dependency_service.resume_paused_dependents(db, "postgresql")
            await db.commit()

    async def install(self, dependency_id: str) -> tuple[bool, str]:
        service = self.get_service(dependency_id)
        lock = self._operation_locks.get(dependency_id)
        if service is None or lock is None:
            return False, "Unknown dependency."
        from database import AsyncSessionLocal
        from services.resource_guard_service import resource_guard_service
        async with AsyncSessionLocal() as db:
            try:
                await resource_guard_service.allow_start(db)
            except RuntimeError as exc:
                return False, str(exc)
        if not hasattr(service, "install"):
            return False, "This dependency does not support panel installation."
        if not lock.acquire(blocking=False):
            return False, "Another dependency operation is already running."

        current = component_state_store.get("dependency", dependency_id)
        try:
            await component_state_store.set(
                "dependency", dependency_id, operation="installing", clear_error=True
            )
            success, message = await asyncio.to_thread(service.install)
            if not success:
                await component_state_store.set(
                    "dependency",
                    dependency_id,
                    desired_enabled=current.desired_enabled,
                    operation="idle",
                    last_error=message,
                )
                return False, message

            await component_state_store.set(
                "dependency",
                dependency_id,
                desired_enabled=True,
                operation="idle",
                install_origin="panel_managed",
                clear_error=True,
            )
            return True, message
        finally:
            lock.release()

    async def check_update(self, dependency_id: str) -> tuple[bool, str]:
        service = self.get_service(dependency_id)
        lock = self._operation_locks.get(dependency_id)
        status = self.get_status(dependency_id, force=True)
        if service is None or lock is None or status is None:
            return False, "Unknown dependency."
        if status.get("install_origin") != "panel_managed":
            return False, "Only panel-managed dependencies can check updates from the panel."
        checker = getattr(service, "check_update", None)
        if not callable(checker):
            return False, "This dependency does not support panel update checks."
        if not lock.acquire(blocking=False):
            return False, "Another dependency operation is already running."
        current = component_state_store.get("dependency", dependency_id)
        try:
            await component_state_store.set(
                "dependency", dependency_id, operation="checking_update", clear_error=True
            )
            success, message = await asyncio.to_thread(checker)
            await component_state_store.set(
                "dependency",
                dependency_id,
                desired_enabled=current.desired_enabled,
                operation="idle",
                last_error=None if success else message,
                clear_error=success,
            )
            return success, message
        except Exception as exc:
            message = str(exc)
            await component_state_store.set(
                "dependency",
                dependency_id,
                desired_enabled=current.desired_enabled,
                operation="idle",
                last_error=message,
            )
            return False, message
        finally:
            lock.release()

    async def update(self, dependency_id: str) -> tuple[bool, str]:
        service = self.get_service(dependency_id)
        lock = self._operation_locks.get(dependency_id)
        status = self.get_status(dependency_id, cached=True)
        if service is None or lock is None or status is None:
            return False, "Unknown dependency."
        if not status.get("can_update"):
            return False, str((status.get("update") or {}).get("message") or "Dependency update is unavailable.")
        updater = getattr(service, "update", None)
        if not callable(updater):
            return False, "This dependency does not support panel updates."
        if not lock.acquire(blocking=False):
            return False, "Another dependency operation is already running."
        current = component_state_store.get("dependency", dependency_id)
        try:
            await component_state_store.set(
                "dependency", dependency_id, operation="updating", clear_error=True
            )
            success, message = await asyncio.to_thread(updater)
            await component_state_store.set(
                "dependency",
                dependency_id,
                desired_enabled=current.desired_enabled,
                operation="idle",
                last_error=None if success else message,
                clear_error=success,
            )
            return success, message
        except Exception as exc:
            message = str(exc)
            await component_state_store.set(
                "dependency",
                dependency_id,
                desired_enabled=current.desired_enabled,
                operation="idle",
                last_error=message,
            )
            return False, message
        finally:
            lock.release()


dependency_manager = DependencyManager()
