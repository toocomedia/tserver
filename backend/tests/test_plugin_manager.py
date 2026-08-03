import asyncio
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from plugins.manager import PluginManager, PluginUnavailableError
from services.component_state import ComponentStateValue, component_state_store


class PluginManagerTests(unittest.TestCase):
    def setUp(self):
        self._state_cache = dict(component_state_store._cache)

    def tearDown(self):
        component_state_store._cache = self._state_cache

    @staticmethod
    def _write_manifest(root: Path, plugin_id: str, **extra):
        plugin_dir = root / plugin_id
        plugin_dir.mkdir(parents=True)
        data = {
            "id": plugin_id,
            "name": plugin_id.title(),
            "version": "1.0.0",
            "enabled": True,
            "usage": {},
            **extra,
        }
        (plugin_dir / "plugin.json").write_text(json.dumps(data), encoding="utf-8")

    def test_dependency_outage_pauses_plugin_and_blocks_direct_route(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._write_manifest(
                root,
                "container_mail",
                requires={"dependencies": ["docker"]},
            )
            manager = PluginManager()
            with patch("plugins.manager.PLUGINS_DIR", root), patch(
                "dependencies.dependency_manager.is_healthy", return_value=False
            ):
                plugins = manager.discover_plugins()
                self.assertEqual(plugins[0]["effective_status"], "paused")
                with self.assertRaises(PluginUnavailableError) as error:
                    manager.availability_dependency("container_mail")()
                self.assertEqual(error.exception.status_code, 503)

    def test_manual_disable_is_not_overridden_by_healthy_dependency(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._write_manifest(
                root,
                "container_app",
                requires={"dependencies": ["docker"]},
            )
            component_state_store._cache[("plugin", "container_app")] = (
                ComponentStateValue(desired_enabled=False)
            )
            manager = PluginManager()
            with patch("plugins.manager.PLUGINS_DIR", root), patch(
                "dependencies.dependency_manager.is_healthy", return_value=True
            ):
                plugin = manager.discover_plugins()[0]
                self.assertEqual(plugin["effective_status"], "disabled")

    def test_sidebar_uses_stored_enabled_state_without_dependency_probe(self):
        manager = PluginManager()
        manager.plugins = {
            "container_mail": {
                "id": "container_mail",
                "name": "Container Mail",
                "manifest_enabled": True,
                "manifest_error": None,
                "installed": True,
                "sidebar": True,
                "sidebar_label": "Mail",
                "route_prefix": "/plugins/container_mail",
                "icon": "mail",
                "requires": {"dependencies": ["docker"]},
            }
        }

        with patch("dependencies.dependency_manager.is_healthy") as is_healthy:
            items = manager.get_sidebar_items()

        self.assertEqual(items[0]["id"], "container_mail")
        self.assertNotIn("paused", items[0])
        is_healthy.assert_not_called()

        component_state_store._cache[("plugin", "container_mail")] = (
            ComponentStateValue(desired_enabled=False)
        )
        self.assertEqual(manager.get_sidebar_items(), [])

    def test_plugin_list_does_not_probe_dependency_health(self):
        manager = PluginManager()
        manager.plugins = {
            "container_mail": {
                "id": "container_mail",
                "name": "Container Mail",
                "manifest_enabled": True,
                "manifest_error": None,
                "installed": True,
                "usage": {},
                "requires": {"dependencies": ["docker"]},
            }
        }

        with patch("dependencies.dependency_manager.is_healthy") as is_healthy:
            plugins = manager.list_plugins()

        self.assertEqual(plugins[0]["effective_status"], "active")
        self.assertIsNone(plugins[0]["dependency_status"][0]["healthy"])
        is_healthy.assert_not_called()

    def test_enable_checks_and_blocks_unhealthy_dependency(self):
        manager = PluginManager()
        plugin = {"installed": True, "paused_by": ["postgresql"]}

        with patch.object(manager, "get_plugin", return_value=plugin) as get_plugin:
            success, message = asyncio.run(
                manager.toggle_plugin("postgres_manager", True)
            )

        self.assertFalse(success)
        self.assertEqual(message, "Required dependency is unavailable: postgresql.")
        get_plugin.assert_called_once_with(
            "postgres_manager", check_dependencies=True
        )

    def test_unknown_dependency_is_visible_but_blocked(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._write_manifest(
                root,
                "future_app",
                requires={"dependencies": ["not_registered"]},
            )
            manager = PluginManager()
            with patch("plugins.manager.PLUGINS_DIR", root):
                plugin = manager.discover_plugins()[0]
            self.assertEqual(plugin["effective_status"], "invalid")
            self.assertIn("Unknown dependencies", plugin["manifest_error"])

    def test_usage_contract_is_required_and_validated(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._write_manifest(root, "missing_usage", usage=None)
            manager = PluginManager()
            with patch("plugins.manager.PLUGINS_DIR", root):
                plugin = manager.discover_plugins()[0]
            self.assertEqual(plugin["effective_status"], "invalid")
            self.assertIn("usage is required", plugin["manifest_error"])

    def test_upload_rejects_traversal_and_reserved_dependency_id(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "plugins"
            root.mkdir()
            traversal = Path(temp) / "traversal.zip"
            with zipfile.ZipFile(traversal, "w") as archive:
                archive.writestr("../escape.txt", "bad")
            manager = PluginManager()
            with patch("plugins.manager.PLUGINS_DIR", root):
                success, _ = manager.upload_plugin_zip(str(traversal))
                self.assertFalse(success)

            reserved = Path(temp) / "reserved.zip"
            with zipfile.ZipFile(reserved, "w") as archive:
                archive.writestr(
                    "docker/plugin.json",
                    json.dumps({"id": "docker", "name": "Fake Docker"}),
                )
            with patch("plugins.manager.PLUGINS_DIR", root):
                success, message = manager.upload_plugin_zip(str(reserved))
                self.assertFalse(success)
                self.assertIn("reserved", message)

    def test_valid_archive_installs_atomically(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "plugins"
            root.mkdir()
            archive_path = Path(temp) / "sample.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr(
                    "sample/plugin.json",
                    json.dumps(
                        {
                            "id": "sample",
                            "name": "Sample",
                            "usage": {},
                        }
                    ),
                )
            manager = PluginManager()
            with patch("plugins.manager.PLUGINS_DIR", root):
                success, _ = manager.upload_plugin_zip(str(archive_path))
                self.assertTrue(success)
                self.assertTrue((root / "sample" / "plugin.json").is_file())

    def test_route_can_skip_dependency_probe_for_an_instant_page_shell(self):
        manager = PluginManager()
        manager.plugins = {
            "container_apps": {
                "id": "container_apps",
                "name": "Container Apps",
                "manifest_enabled": True,
                "manifest_error": None,
                "installed": True,
                "usage": {},
                "requires": {"dependencies": ["docker"]},
            }
        }

        with patch("dependencies.dependency_manager.is_healthy") as is_healthy:
            manager.availability_dependency(
                "container_apps",
                check_dependencies=False,
            )()

        is_healthy.assert_not_called()

if __name__ == "__main__":
    unittest.main()
