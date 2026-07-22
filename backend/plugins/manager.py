"""
backend/plugins/manager.py — SRV-Panel Dynamic Plugin Architecture Manager.

Scans backend/plugins/ for subdirectories containing plugin.json.
Auto-mounts plugin routers, exposes Jinja template paths, and handles plugin lifecycle.
No core code modifications required when adding or uploading plugins!
"""
import os
import json
import logging
import importlib
import shutil
import subprocess
import zipfile
from pathlib import Path
from typing import Dict, Any, List, Optional
from fastapi import FastAPI
from jinja2 import FileSystemLoader, ChoiceLoader

logger = logging.getLogger(__name__)

PLUGINS_DIR = Path(__file__).parent.resolve()


class PluginManager:
    def __init__(self):
        self.plugins: Dict[str, Dict[str, Any]] = {}
        self.mounted_routers: set[str] = set()

    def _check_plugin_installed(self, plugin_dir: Path, plugin_id: str) -> bool:
        """Check if plugin's service reports that it is installed on system."""
        service_file = plugin_dir / "service.py"
        if not service_file.exists():
            return True  # Pure UI plugins are considered installed

        try:
            mod = importlib.import_module(f"plugins.{plugin_dir.name}.service")
            for attr in ["maddy_service", "service", f"{plugin_id}_service"]:
                svc = getattr(mod, attr, None)
                if svc and hasattr(svc, "is_installed"):
                    return svc.is_installed()
        except Exception as exc:
            logger.warning("Could not check installation status for plugin %s: %s", plugin_id, exc)

        return True

    def discover_plugins(self) -> List[Dict[str, Any]]:
        """Scan backend/plugins/ for plugin.json manifests and check installation status."""
        self.plugins.clear()
        if not PLUGINS_DIR.exists():
            return []

        for item in PLUGINS_DIR.iterdir():
            if item.is_dir():
                manifest_path = item / "plugin.json"
                if manifest_path.exists():
                    try:
                        with open(manifest_path, "r", encoding="utf-8") as f:
                            data = json.load(f)
                            plugin_id = data.get("id", item.name)
                            data["id"] = plugin_id
                            data["dir_path"] = str(item)
                            data["enabled"] = data.get("enabled", True)
                            data["installed"] = self._check_plugin_installed(item, plugin_id)
                            self.plugins[plugin_id] = data
                    except Exception as exc:
                        logger.error("Error reading manifest for plugin %s: %s", item.name, exc)

        return list(self.plugins.values())

    def init_app(self, app: FastAPI):
        """Mount all discovered & enabled plugin routers and register template paths."""
        self.discover_plugins()
        from templating import templates

        template_dirs = [str(templates.env.loader.searchpath[0])] if templates.env.loader else []

        for plugin_id, plugin in self.plugins.items():

            plugin_dir = Path(plugin["dir_path"])

            # 1. Register plugin template path if exists
            plugin_templates_dir = plugin_dir / "templates"
            if plugin_templates_dir.exists():
                template_dirs.append(str(plugin_templates_dir))

            # 2. Dynamically import and mount router.py if present
            router_file = plugin_dir / "router.py"
            if router_file.exists():
                try:
                    module_name = f"plugins.{plugin_dir.name}.router"
                    mod = importlib.import_module(module_name)
                    if hasattr(mod, "router") and plugin_id not in self.mounted_routers:
                        app.include_router(mod.router)
                        self.mounted_routers.add(plugin_id)
                        logger.info("Successfully mounted router for plugin: %s", plugin_id)
                except Exception as exc:
                    logger.error("Failed to load router for plugin %s: %s", plugin_id, exc)

        # Update Jinja ChoiceLoader to search core templates first, then plugin templates
        if template_dirs:
            loaders = [FileSystemLoader(d) for d in template_dirs]
            templates.env.loader = ChoiceLoader(loaders)

    def get_sidebar_items(self) -> List[Dict[str, Any]]:
        """Return list of enabled & installed plugins configured for sidebar display."""
        items = []
        for plugin_id, plugin in self.plugins.items():
            if plugin.get("enabled", True) and plugin.get("sidebar", False):
                plugin_dir = Path(plugin["dir_path"])
                is_installed = self._check_plugin_installed(plugin_dir, plugin_id)

                if is_installed:
                    items.append({
                        "id": plugin_id,
                        "label": plugin.get("sidebar_label", plugin.get("name")),
                        "route": plugin.get("route_prefix", f"/plugins/{plugin_id}"),
                        "icon": plugin.get("icon", "grid"),
                    })
        return items

    def get_plugin(self, plugin_id: str) -> Optional[Dict[str, Any]]:
        """Get plugin metadata by ID."""
        if not self.plugins:
            self.discover_plugins()
        return self.plugins.get(plugin_id)

    def toggle_plugin(self, plugin_id: str, enabled: bool) -> bool:
        """Enable or disable a plugin in its plugin.json manifest."""
        plugin = self.get_plugin(plugin_id)
        if not plugin:
            return False

        if not plugin.get("installed", False) and enabled:
            logger.warning("Cannot enable plugin %s because it is not installed.", plugin_id)
            return False

        manifest_path = Path(plugin["dir_path"]) / "plugin.json"
        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            data["enabled"] = enabled
            with open(manifest_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)

            plugin["enabled"] = enabled
            return True
        except Exception as exc:
            logger.error("Failed to toggle plugin %s: %s", plugin_id, exc)
            return False

    def run_plugin_script(self, plugin_id: str, action: str) -> bool:
        """Run install or uninstall script for a plugin."""
        plugin = self.get_plugin(plugin_id)
        if not plugin:
            return False

        script_rel = plugin.get(f"{action}_script")
        if not script_rel:
            return True

        script_path = Path(plugin["dir_path"]) / script_rel
        if not script_path.exists():
            logger.error("Script %s does not exist for plugin %s", script_path, plugin_id)
            return False

        if os.name == "nt":
            logger.info("Windows detected — skipping bash script %s for %s", script_path, action)
            return True

        try:
            subprocess.run(["bash", str(script_path)], check=True)
            self.discover_plugins()
            return True
        except Exception as exc:
            logger.error("Error executing %s script for plugin %s: %s", action, plugin_id, exc)
            return False

    def upload_plugin_zip(self, zip_filepath: str) -> Optional[Dict[str, Any]]:
        """Extract uploaded .zip archive into backend/plugins/."""
        try:
            with zipfile.ZipFile(zip_filepath, 'r') as zip_ref:
                manifest_file = [f for f in zip_ref.namelist() if f.endswith("plugin.json")]
                if not manifest_file:
                    logger.error("Uploaded zip has no plugin.json manifest")
                    return None

                zip_ref.extractall(PLUGINS_DIR)

            self.discover_plugins()
            return {"status": "success"}
        except Exception as exc:
            logger.error("Failed to extract plugin zip: %s", exc)
            return None


plugin_manager = PluginManager()
