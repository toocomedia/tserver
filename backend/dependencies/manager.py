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
        status = (
            cached_status()
            if cached and cached_status is not None
            else service.get_status(force=force)
        )
        state = component_state_store.get("dependency", dependency_id)
        status.update(self._metadata[dependency_id])
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
        status["install_origin"] = (
            "external"
            if status.get("installed") and state.install_origin == "bundled"
            else state.install_origin
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
                status["state"]
                if status["healthy"]
                else ("disabled" if not state.desired_enabled else status["state"])
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

    def is_healthy(self, dependency_id: str) -> bool:
        status = self.get_status(dependency_id)
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
        return {
            "dependency_id": dependency_id,
            "action": action,
            "dependents": dependents,
            "unmanaged_containers": [
                item for item in containers if not item["panel_managed"]
            ],
            "blocked": action == "uninstall" and bool(dependents),
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


dependency_manager = DependencyManager()
