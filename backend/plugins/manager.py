"""SRV Panel plugin discovery, lifecycle, dependency, and upload manager."""
from __future__ import annotations

import asyncio
import importlib
import json
import logging
import os
import re
import shutil
import stat
import subprocess
import tempfile
import threading
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI
from jinja2 import ChoiceLoader, FileSystemLoader

import config
from dependencies.registry import CORE_DEPENDENCY_IDS
from services.component_state import component_state_store

logger = logging.getLogger(__name__)

PLUGINS_DIR = Path(__file__).parent.resolve()
PLUGIN_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
PROCESS_NAME_RE = re.compile(r"^[A-Za-z0-9_.+-]{1,64}$")
RESERVED_PLUGIN_IDS = frozenset({"manager", *CORE_DEPENDENCY_IDS})


class PluginUnavailableError(Exception):
    def __init__(self, plugin_id: str, code: str, message: str, status_code: int):
        super().__init__(message)
        self.plugin_id = plugin_id
        self.code = code
        self.message = message
        self.status_code = status_code


class PluginManager:
    MAX_ARCHIVE_FILES = 512
    MAX_EXTRACTED_BYTES = 100 * 1024 * 1024
    SCRIPT_TIMEOUT_SECONDS = 900

    def __init__(self):
        self.plugins: Dict[str, Dict[str, Any]] = {}
        self.mounted_routers: set[str] = set()
        self._operation_locks: dict[str, threading.Lock] = {}
        self._app: FastAPI | None = None

    @staticmethod
    def _service_module(plugin_dir: Path):
        service_file = plugin_dir / "service.py"
        if not service_file.exists():
            return None
        return importlib.import_module(f"plugins.{plugin_dir.name}.service")

    def _find_service(self, plugin_dir: Path, plugin_id: str):
        module = self._service_module(plugin_dir)
        if module is None:
            return None
        for attr in [f"{plugin_id}_service", "service", "maddy_service"]:
            service = getattr(module, attr, None)
            if service is not None:
                return service
        return None

    def _check_plugin_installed(self, plugin_dir: Path, plugin_id: str) -> bool:
        try:
            service = self._find_service(plugin_dir, plugin_id)
            if service is not None and hasattr(service, "is_installed"):
                return bool(service.is_installed())
            return True
        except Exception as exc:
            logger.warning("Could not check installation status for %s: %s", plugin_id, exc)
            return False

    @staticmethod
    def _required_dependencies(data: dict[str, Any]) -> list[str]:
        requires = data.get("requires") or {}
        dependencies = requires.get("dependencies", []) if isinstance(requires, dict) else []
        return dependencies if isinstance(dependencies, list) else []

    def _validate_manifest(self, data: dict[str, Any], plugin_dir: Path) -> str | None:
        plugin_id = data.get("id")
        if not isinstance(plugin_id, str) or not PLUGIN_ID_RE.fullmatch(plugin_id):
            return "Plugin ID must use lowercase letters, numbers, underscores, or hyphens."
        if plugin_id != plugin_dir.name:
            return "Plugin ID must match its folder name."
        if not isinstance(data.get("name"), str) or not data["name"].strip():
            return "Plugin name is required."

        requires = data.get("requires")
        if requires is not None and not isinstance(requires, dict):
            return "requires must be an object."
        dependencies = self._required_dependencies(data)
        if any(not isinstance(item, str) for item in dependencies):
            return "requires.dependencies must contain dependency IDs."
        unknown = sorted(set(dependencies) - CORE_DEPENDENCY_IDS)
        if unknown:
            return f"Unknown dependencies: {', '.join(unknown)}."

        usage = data.get("usage")
        if not isinstance(usage, dict):
            return "usage is required and must be an object."
        process_names = usage.get("process_names", [])
        if not isinstance(process_names, list) or any(
            not isinstance(name, str) or not PROCESS_NAME_RE.fullmatch(name)
            for name in process_names
        ):
            return "usage.process_names must contain safe process names."
        return None

    def _effective(self, plugin: dict[str, Any]) -> dict[str, Any]:
        from dependencies import dependency_manager

        result = dict(plugin)
        plugin_id = result["id"]
        default_enabled = bool(result.get("manifest_enabled", True))
        state = component_state_store.get(
            "plugin", plugin_id, default_enabled=default_enabled
        )
        result["enabled"] = state.desired_enabled
        result["operation"] = state.operation
        result["last_error"] = state.last_error

        requirements = []
        paused_by = []
        for dependency_id in self._required_dependencies(result):
            healthy = dependency_manager.is_healthy(dependency_id)
            requirements.append({"id": dependency_id, "healthy": healthy})
            if not healthy:
                paused_by.append(dependency_id)
        result["dependency_status"] = requirements
        result["paused_by"] = paused_by

        if result.get("manifest_error"):
            effective_status = "invalid"
        elif not result.get("installed", False):
            effective_status = "missing"
        elif state.operation != "idle":
            effective_status = state.operation
        elif not state.desired_enabled:
            effective_status = "disabled"
        elif paused_by:
            effective_status = "paused"
        else:
            effective_status = "active"
        result["effective_status"] = effective_status
        return result

    def discover_plugins(self) -> List[Dict[str, Any]]:
        self.plugins.clear()
        if not PLUGINS_DIR.exists():
            return []

        for item in sorted(PLUGINS_DIR.iterdir(), key=lambda path: path.name):
            manifest_path = item / "plugin.json"
            if not item.is_dir() or not manifest_path.exists():
                continue
            try:
                data = json.loads(manifest_path.read_text(encoding="utf-8"))
                plugin_id = data.get("id", item.name)
                data["id"] = plugin_id
                data["dir_path"] = str(item)
                data["manifest_enabled"] = bool(data.get("enabled", True))
                data["installed"] = self._check_plugin_installed(item, plugin_id)
                data["manifest_error"] = self._validate_manifest(data, item)
                self.plugins[plugin_id] = data
                self._operation_locks.setdefault(plugin_id, threading.Lock())
            except Exception as exc:
                logger.error("Error reading manifest for plugin %s: %s", item.name, exc)
        return [self._effective(plugin) for plugin in self.plugins.values()]

    def state_components(self) -> list[tuple[str, str, bool, str]]:
        if not self.plugins:
            self.discover_plugins()
        return [
            (
                "plugin",
                plugin_id,
                bool(plugin.get("manifest_enabled", True)),
                (
                    "uploaded"
                    if (Path(plugin["dir_path"]) / ".srv-panel-uploaded").exists()
                    else "bundled"
                ),
            )
            for plugin_id, plugin in self.plugins.items()
        ]

    def availability_dependency(self, plugin_id: str):
        def require_available() -> None:
            plugin = self.get_plugin(plugin_id)
            if plugin is None:
                raise PluginUnavailableError(
                    plugin_id, "plugin_missing", "Plugin is not registered.", 404
                )
            status = plugin["effective_status"]
            if status == "active":
                return
            if status == "paused":
                dependencies = ", ".join(plugin["paused_by"])
                raise PluginUnavailableError(
                    plugin_id,
                    "dependency_unavailable",
                    f"Required dependency is unavailable: {dependencies}.",
                    503,
                )
            if status == "disabled":
                raise PluginUnavailableError(
                    plugin_id, "plugin_disabled", "Plugin is disabled.", 409
                )
            if status == "missing":
                raise PluginUnavailableError(
                    plugin_id, "plugin_not_installed", "Plugin is not installed.", 409
                )
            raise PluginUnavailableError(
                plugin_id,
                "plugin_unavailable",
                plugin.get("manifest_error") or plugin.get("last_error") or "Plugin is unavailable.",
                409,
            )

        return require_available

    def init_app(self, app: FastAPI):
        self._app = app
        self.discover_plugins()
        from templating import templates

        template_dirs = [str((config.BASE_DIR / "templates").resolve())]
        for plugin_id, plugin in self.plugins.items():
            plugin_dir = Path(plugin["dir_path"])
            templates_dir = plugin_dir / "templates"
            if templates_dir.exists():
                template_dirs.append(str(templates_dir))

            router_file = plugin_dir / "router.py"
            if not router_file.exists() or plugin.get("manifest_error"):
                continue
            try:
                module = importlib.import_module(f"plugins.{plugin_dir.name}.router")
                if hasattr(module, "router") and plugin_id not in self.mounted_routers:
                    app.include_router(
                        module.router,
                        dependencies=[Depends(self.availability_dependency(plugin_id))],
                    )
                    self.mounted_routers.add(plugin_id)
                    logger.info("Mounted guarded plugin router: %s", plugin_id)
            except Exception as exc:
                logger.error("Failed to load router for plugin %s: %s", plugin_id, exc)

        if template_dirs:
            templates.env.loader = ChoiceLoader([FileSystemLoader(path) for path in template_dirs])

    def get_sidebar_items(self) -> List[Dict[str, Any]]:
        items = []
        for plugin_id in self.plugins:
            plugin = self.get_plugin(plugin_id)
            if not plugin or not plugin.get("sidebar", False):
                continue
            if plugin["effective_status"] not in {"active", "paused"}:
                continue
            items.append(
                {
                    "id": plugin_id,
                    "label": plugin.get("sidebar_label", plugin.get("name")),
                    "route": plugin.get("route_prefix", f"/plugins/{plugin_id}"),
                    "icon": plugin.get("icon", "grid"),
                    "paused": plugin["effective_status"] == "paused",
                }
            )
        return items

    def get_plugin(self, plugin_id: str) -> Optional[Dict[str, Any]]:
        if not self.plugins:
            self.discover_plugins()
        plugin = self.plugins.get(plugin_id)
        return self._effective(plugin) if plugin else None

    def get_service(self, plugin_id: str):
        plugin = self.get_plugin(plugin_id)
        if not plugin:
            return None
        return self._find_service(Path(plugin["dir_path"]), plugin_id)

    def get_dependents(self, dependency_id: str) -> list[dict[str, Any]]:
        dependents = []
        for plugin_id in self.plugins:
            plugin = self.get_plugin(plugin_id)
            if not plugin or dependency_id not in self._required_dependencies(plugin):
                continue
            if not plugin.get("installed", False):
                continue
            dependents.append(
                {
                    "id": plugin_id,
                    "name": plugin.get("name", plugin_id),
                    "enabled": plugin["enabled"],
                    "status": plugin["effective_status"],
                }
            )
        return dependents

    async def _run_lifecycle_hook(self, plugin: dict[str, Any], enabled: bool) -> None:
        service = self._find_service(Path(plugin["dir_path"]), plugin["id"])
        if service is None:
            return
        hook = getattr(service, "resume" if enabled else "pause", None)
        if hook is not None:
            await asyncio.wait_for(asyncio.to_thread(hook), timeout=60)

    async def toggle_plugin(self, plugin_id: str, enabled: bool) -> tuple[bool, str]:
        plugin = self.get_plugin(plugin_id)
        if not plugin:
            return False, "Plugin not found."
        if enabled and not plugin.get("installed", False):
            return False, "Cannot enable plugin before it is installed."

        lock = self._operation_locks.setdefault(plugin_id, threading.Lock())
        if not lock.acquire(blocking=False):
            return False, "Another plugin operation is already running."
        previous = component_state_store.get(
            "plugin", plugin_id, default_enabled=plugin["enabled"]
        )
        operation = "enabling" if enabled else "disabling"
        try:
            await component_state_store.set(
                "plugin", plugin_id, operation=operation, clear_error=True
            )
            try:
                if not plugin.get("paused_by"):
                    await self._run_lifecycle_hook(plugin, enabled)
            except Exception as exc:
                message = f"Plugin lifecycle hook failed: {exc}"
                await component_state_store.set(
                    "plugin",
                    plugin_id,
                    desired_enabled=previous.desired_enabled,
                operation="idle",
                    last_error=message,
                )
                return False, message
            await component_state_store.set(
                "plugin",
                plugin_id,
                desired_enabled=enabled,
                operation="idle",
                clear_error=True,
            )
            return True, "Plugin enabled." if enabled else "Plugin disabled."
        finally:
            lock.release()

    async def run_plugin_script(self, plugin_id: str, action: str) -> tuple[bool, str]:
        if action not in {"install", "uninstall"}:
            return False, "Unsupported plugin action."
        plugin = self.get_plugin(plugin_id)
        if not plugin:
            return False, "Plugin not found."
        if action == "install" and plugin.get("paused_by"):
            if "docker" in plugin["paused_by"]:
                return False, "Docker daemon is not available."
            return False, "A required system dependency is not available."
        script_rel = plugin.get(f"{action}_script")
        if not script_rel:
            return False, f"Plugin has no {action} script."

        plugin_dir = Path(plugin["dir_path"]).resolve()
        script_path = (plugin_dir / str(script_rel)).resolve()
        if plugin_dir not in script_path.parents or not script_path.is_file():
            return False, "Plugin script path is invalid."
        if os.name == "nt":
            return False, "Plugin scripts can only run on Linux."

        lock = self._operation_locks.setdefault(plugin_id, threading.Lock())
        if not lock.acquire(blocking=False):
            return False, "Another plugin operation is already running."
        try:
            await component_state_store.set(
                "plugin", plugin_id, operation=f"{action}ing", clear_error=True
            )
            if action == "uninstall" and "docker" in self._required_dependencies(plugin):
                from dependencies import dependency_manager

                docker_service = dependency_manager.get_service("docker")
                cleanup_ok, cleanup_message = await asyncio.to_thread(
                    docker_service.cleanup_plugin_resources,
                    plugin_id,
                    purge_data=False,
                )
                if not cleanup_ok:
                    await component_state_store.set(
                        "plugin",
                        plugin_id,
                        operation="idle",
                        last_error=cleanup_message,
                    )
                    return False, cleanup_message
            command = ["bash", str(script_path)]
            if hasattr(os, "geteuid") and os.geteuid() != 0 and config.PRIVILEGED_SUDO:
                command = ["sudo", "-n", *command]
            try:
                result = await asyncio.to_thread(
                    subprocess.run,
                    command,
                    capture_output=True,
                    text=True,
                    timeout=self.SCRIPT_TIMEOUT_SECONDS,
                    check=False,
                    shell=False,
                )
            except subprocess.TimeoutExpired:
                message = f"Plugin {action} timed out."
                await component_state_store.set(
                    "plugin", plugin_id, operation="idle", last_error=message
                )
                return False, message
            if result.returncode != 0:
                message = (result.stderr or result.stdout or f"Plugin {action} failed.").strip()
                await component_state_store.set(
                    "plugin", plugin_id, operation="idle", last_error=message
                )
                return False, message

            await component_state_store.set(
                "plugin",
                plugin_id,
                desired_enabled=action == "install",
                operation="idle",
                clear_error=True,
            )
            self.discover_plugins()
            return True, f"Plugin {action} completed."
        finally:
            lock.release()

    async def reconcile_plugins(self) -> None:
        """Refresh active plugin runtimes whose bundled configuration changed."""
        for plugin_id in list(self.plugins):
            plugin = self.get_plugin(plugin_id)
            if not plugin or plugin.get("effective_status") != "active":
                continue
            try:
                service = self._find_service(Path(plugin["dir_path"]), plugin_id)
                check = getattr(service, "needs_reconcile", None)
                if check is None or not await asyncio.to_thread(check):
                    continue
                logger.info("Refreshing outdated plugin runtime: %s", plugin_id)
                success, message = await self.run_plugin_script(plugin_id, "install")
                if success:
                    logger.info("Plugin runtime refreshed: %s", plugin_id)
                else:
                    logger.warning(
                        "Could not refresh plugin runtime %s: %s",
                        plugin_id,
                        message,
                    )
            except Exception as exc:
                logger.warning(
                    "Plugin runtime reconciliation failed for %s: %s",
                    plugin_id,
                    exc,
                )

    async def purge_plugin_data(
        self, plugin_id: str, confirmation: str
    ) -> tuple[bool, str]:
        """Purge labeled plugin volumes only after uninstall and typed confirmation."""
        plugin = self.get_plugin(plugin_id)
        if not plugin:
            return False, "Plugin not found."
        if not plugin.get("data_purge"):
            return False, "This plugin does not expose a data purge action."
        if plugin.get("installed"):
            return False, "Uninstall the plugin before purging its data."
        if confirmation != f"PURGE {plugin_id}":
            return False, f"Type PURGE {plugin_id} to confirm."

        lock = self._operation_locks.setdefault(plugin_id, threading.Lock())
        if not lock.acquire(blocking=False):
            return False, "Another plugin operation is already running."
        try:
            from dependencies import dependency_manager

            docker_service = dependency_manager.get_service("docker")
            cleanup_ok, cleanup_message = await asyncio.to_thread(
                docker_service.cleanup_plugin_resources,
                plugin_id,
                purge_data=True,
            )
            if not cleanup_ok:
                return False, cleanup_message

            service = self._find_service(Path(plugin["dir_path"]), plugin_id)
            hook = getattr(service, "purge_data", None) if service is not None else None
            if hook is not None:
                await asyncio.to_thread(hook)
            return True, f"Plugin data purged: {cleanup_message}."
        except Exception as exc:
            return False, f"Plugin data purge failed: {exc}"
        finally:
            lock.release()

    @staticmethod
    def _safe_archive_path(name: str) -> PurePosixPath:
        normalized = name.replace("\\", "/")
        path = PurePosixPath(normalized)
        if path.is_absolute() or not path.parts or ".." in path.parts:
            raise ValueError("Archive contains an unsafe path.")
        return path

    def upload_plugin_zip(self, zip_filepath: str) -> tuple[bool, str]:
        try:
            with zipfile.ZipFile(zip_filepath, "r") as archive:
                infos = archive.infolist()
                if len(infos) > self.MAX_ARCHIVE_FILES:
                    return False, "Plugin archive contains too many files."
                if sum(info.file_size for info in infos) > self.MAX_EXTRACTED_BYTES:
                    return False, "Plugin archive is too large after extraction."

                paths: list[tuple[zipfile.ZipInfo, PurePosixPath]] = []
                manifests: list[PurePosixPath] = []
                for info in infos:
                    path = self._safe_archive_path(info.filename)
                    mode = (info.external_attr >> 16) & 0xFFFF
                    if stat.S_ISLNK(mode):
                        return False, "Plugin archives cannot contain symbolic links."
                    if path.name.lower() == "dependency.json":
                        return False, "Dependency drivers cannot be uploaded as plugins."
                    paths.append((info, path))
                    if path.name == "plugin.json" and not info.is_dir():
                        manifests.append(path)

                if len(manifests) != 1 or len(manifests[0].parts) != 2:
                    return False, "Archive must contain one <plugin-id>/plugin.json manifest."
                plugin_folder = manifests[0].parts[0]
                if any(path.parts[0] != plugin_folder for _, path in paths):
                    return False, "All plugin files must be inside one plugin folder."

                with tempfile.TemporaryDirectory(prefix=".upload-", dir=PLUGINS_DIR) as temp:
                    stage = Path(temp)
                    for info, path in paths:
                        destination = (stage / Path(*path.parts)).resolve()
                        if stage.resolve() not in destination.parents and destination != stage.resolve():
                            return False, "Archive path escaped the staging directory."
                        if info.is_dir():
                            destination.mkdir(parents=True, exist_ok=True)
                            continue
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        with archive.open(info) as source, destination.open("wb") as target:
                            shutil.copyfileobj(source, target)

                    root = stage / plugin_folder
                    data = json.loads((root / "plugin.json").read_text(encoding="utf-8"))
                    plugin_id = data.get("id")
                    if plugin_id in RESERVED_PLUGIN_IDS:
                        return False, "Plugin ID is reserved by the panel core."
                    if any(key in data for key in ("dependency", "system_dependency")):
                        return False, "Uploaded plugins cannot declare system-driver metadata."
                    if str(data.get("type", "")).lower() in {"system", "dependency"}:
                        return False, "Uploaded plugins cannot claim a system type."
                    error = self._validate_manifest(data, root)
                    if error:
                        return False, error

                    destination = PLUGINS_DIR / plugin_id
                    if destination.exists():
                        return False, "A plugin with this ID already exists."
                    (root / ".srv-panel-uploaded").write_text("", encoding="utf-8")
                    shutil.move(str(root), str(destination))

            self.discover_plugins()
            if self._app is not None:
                self.init_app(self._app)
            return True, "Plugin uploaded successfully."
        except (OSError, ValueError, zipfile.BadZipFile, json.JSONDecodeError) as exc:
            logger.error("Failed to upload plugin zip: %s", exc)
            return False, str(exc)


plugin_manager = PluginManager()
