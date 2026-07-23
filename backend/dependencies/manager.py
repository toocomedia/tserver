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
    ) -> dict[str, Any] | None:
        service = self.get_service(dependency_id)
        if service is None:
            return None
        status = service.get_status(force=force)
        state = component_state_store.get("dependency", dependency_id)
        status.update(self._metadata[dependency_id])
        status["desired_enabled"] = state.desired_enabled
        status["operation"] = state.operation
        status["install_origin"] = (
            "external"
            if status.get("installed") and state.install_origin == "bundled"
            else state.install_origin
        )
        status["last_error"] = (
            None if status.get("healthy") else (state.last_error or status.get("error"))
        )
        status["effective_state"] = (
            state.operation
            if state.operation != "idle"
            else ("disabled" if not state.desired_enabled else status["state"])
        )
        return status

    def get_all_statuses(self, *, force: bool = False) -> list[dict[str, Any]]:
        return [
            self.get_status(dep_id, force=force)
            for dep_id in self._services
        ]

    def is_healthy(self, dependency_id: str) -> bool:
        status = self.get_status(dependency_id)
        return bool(
            status
            and status["desired_enabled"]
            and status["operation"] == "idle"
            and status["healthy"]
        )

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
        if not lock.acquire(blocking=False):
            return False, "Another dependency operation is already running."

        operation = "enabling" if enabled else "disabling"
        current = component_state_store.get("dependency", dependency_id)
        try:
            await component_state_store.set(
                "dependency",
                dependency_id,
                operation=operation,
                clear_error=True,
            )
            success, message = await asyncio.to_thread(service.toggle, enabled)
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
                desired_enabled=enabled,
                operation="idle",
                clear_error=True,
            )
            return True, message
        finally:
            lock.release()

    async def install(self, dependency_id: str) -> tuple[bool, str]:
        service = self.get_service(dependency_id)
        lock = self._operation_locks.get(dependency_id)
        if service is None or lock is None:
            return False, "Unknown dependency."
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
