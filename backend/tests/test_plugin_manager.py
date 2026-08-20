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

    def test_platform_selector_marks_plugin_unsupported_and_blocks_actions(self):
        manager = PluginManager()
        manager.plugins = {
            "ubuntu_only": {
                "id": "ubuntu_only",
                "name": "Ubuntu Only",
                "manifest_enabled": True,
                "manifest_error": None,
                "installed": True,
                "usage": {},
                "requires": {
                    "dependencies": [],
                    "platforms": ["ubuntu:24.04"],
                },
            }
        }
        reason = "Ubuntu Only is not verified on Debian 13 (amd64)."

        with patch(
            "plugins.manager.platform_support_service.plugin_support",
            return_value=(False, reason),
        ), patch(
            "plugins.manager.platform_support_service.get",
            return_value={
                "supported": False,
                "selector": "debian:11",
                "pretty_name": "Debian 11",
                "arch": "amd64",
            },
        ):
            plugin = manager.get_plugin("ubuntu_only")
            self.assertFalse(plugin["platform_supported"])
            self.assertEqual(plugin["platform_error"], reason)
            self.assertEqual(plugin["effective_status"], "unsupported")

            with self.assertRaises(PluginUnavailableError) as error:
                manager.availability_dependency("ubuntu_only")()
            self.assertEqual(error.exception.status_code, 409)
            self.assertEqual(error.exception.code, "platform_unsupported")

            enabled, message = asyncio.run(
                manager.toggle_plugin("ubuntu_only", True)
            )
            self.assertFalse(enabled)
            self.assertEqual(message, reason)

            installed, message = asyncio.run(
                manager.run_plugin_script("ubuntu_only", "install")
            )
            self.assertFalse(installed)
            self.assertEqual(message, reason)

    def test_supported_os_can_approve_plugin_as_unverified(self):
        manager = PluginManager()
        manager.plugins = {
            "maddy": {
                "id": "maddy",
                "name": "Maddy",
                "manifest_enabled": True,
                "manifest_error": None,
                "installed": True,
                "usage": {},
                "requires": {"platforms": ["ubuntu:24.04"]},
            }
        }
        platform = {
            "supported": True,
            "selector": "ubuntu:26.04",
            "pretty_name": "Ubuntu 26.04 LTS",
            "arch": "amd64",
        }
        with patch(
            "plugins.manager.platform_support_service.plugin_support",
            return_value=(False, "Plugin is not verified on Ubuntu 26.04 LTS (amd64)."),
        ), patch(
            "plugins.manager.platform_support_service.get", return_value=platform
        ), patch(
            "plugins.manager.plugin_platform_approval_service.is_approved",
            return_value=False,
        ), patch(
            "plugins.manager.plugin_platform_approval_service.approve"
        ) as approve:
            plugin = manager.get_plugin("maddy")
            self.assertTrue(plugin["platform_unverified"])
            self.assertFalse(plugin["platform_allowed"])
            self.assertEqual(plugin["effective_status"], "unverified")

            accepted, message = manager.approve_unverified_platform(plugin, "wrong")
            self.assertFalse(accepted)
            self.assertIn("Confirm the unverified installation", message)

            accepted, _ = manager.approve_unverified_platform(
                plugin, "INSTALL maddy UNVERIFIED"
            )
            self.assertTrue(accepted)
            approve.assert_called_once_with("maddy", "ubuntu:26.04")

    def test_approved_unverified_plugin_remains_labeled_but_is_available(self):
        manager = PluginManager()
        manager.plugins = {
            "maddy": {
                "id": "maddy",
                "name": "Maddy",
                "manifest_enabled": True,
                "manifest_error": None,
                "installed": True,
                "sidebar": True,
                "usage": {},
                "requires": {"platforms": ["ubuntu:24.04"]},
            }
        }
        with patch(
            "plugins.manager.platform_support_service.plugin_support",
            return_value=(False, "Plugin is not verified."),
        ), patch(
            "plugins.manager.platform_support_service.get",
            return_value={
                "supported": True,
                "selector": "ubuntu:26.04",
                "pretty_name": "Ubuntu 26.04 LTS",
                "arch": "amd64",
            },
        ), patch(
            "plugins.manager.plugin_platform_approval_service.is_approved",
            return_value=True,
        ):
            plugin = manager.get_plugin("maddy")
            self.assertTrue(plugin["platform_unverified"])
            self.assertTrue(plugin["platform_approved"])
            self.assertTrue(plugin["platform_allowed"])
            self.assertEqual(plugin["effective_status"], "active")
            manager.availability_dependency("maddy")()

if __name__ == "__main__":
    unittest.main()
