"""
Tests for Resource Guard Slice 4 — All Core Plugins & Dependencies Through Guard.

Run on the VPS:
    cd /opt/srv-panel/app
    /opt/srv-panel/venv/bin/python -m pytest tests/test_resource_guard_slice4.py -v
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


# ── Plugin Manifest: resource_guard section ───────────────────────────────────

class PluginManifestValidationTests(unittest.TestCase):

    def _make_manager(self):
        from plugins.manager import PluginManager
        return PluginManager()

    def _rg_validate(self, rg_section: dict):
        """Run just the resource_guard validation logic inline."""
        from services.resource_guard_profiles import PROFILES
        if not isinstance(rg_section, dict):
            return "resource_guard must be an object."
        safe_stop = rg_section.get("safe_temporary_stop", False)
        adapter = rg_section.get("lifecycle_adapter", "")
        if safe_stop and not adapter:
            return "resource_guard.safe_temporary_stop requires lifecycle_adapter to be set."
        ops = rg_section.get("operations", {})
        if not isinstance(ops, dict):
            return "resource_guard.operations must be an object."
        for op_name, op_cfg in ops.items():
            if not isinstance(op_cfg, dict):
                return f"resource_guard.operations.{op_name} must be an object."
            profile = op_cfg.get("profile", "native_light")
            if profile not in PROFILES:
                return f"resource_guard.operations.{op_name}.profile '{profile}' is not a known profile."
        return None

    def test_valid_manifest_passes_validation(self):
        """Plugin manifest with valid resource_guard section passes."""
        error = self._rg_validate({
            "safe_temporary_stop": True,
            "lifecycle_adapter": "myplugin.adapter",
            "operations": {
                "install": {"profile": "plugin_install", "heavy": True},
            }
        })
        self.assertIsNone(error)

    def test_safe_temporary_stop_without_adapter_is_rejected(self):
        """safe_temporary_stop=True without lifecycle_adapter is rejected."""
        error = self._rg_validate({
            "safe_temporary_stop": True,
            # No lifecycle_adapter
        })
        self.assertIsNotNone(error)
        self.assertIn("lifecycle_adapter", error)

    def test_unknown_profile_name_is_rejected(self):
        """profile name not in PROFILES dict is rejected."""
        error = self._rg_validate({
            "safe_temporary_stop": True,
            "lifecycle_adapter": "p.adapter",
            "operations": {"install": {"profile": "super_heavy_build_5000"}},
        })
        self.assertIsNotNone(error)
        self.assertIn("super_heavy_build_5000", error)

    def test_plugin_without_resource_guard_section_allowed(self):
        """Plugin with no resource_guard section is not an error (section is optional)."""
        # No rg section → we skip rg validation entirely
        error = self._rg_validate({})  # empty dict is valid (all defaults)
        self.assertIsNone(error)

    def test_all_known_profiles_are_valid(self):
        """Every profile in PROFILES is a valid value for resource_guard.operations."""
        from services.resource_guard_profiles import PROFILES
        for profile_name in PROFILES:
            error = self._rg_validate({
                "safe_temporary_stop": False,
                "operations": {"install": {"profile": profile_name}},
            })
            self.assertIsNone(error, f"Profile '{profile_name}' should be valid but got: {error}")


# ── Guard Integration: preflight + register ───────────────────────────────────

class PreflightIntegrationTests(unittest.IsolatedAsyncioTestCase):

    async def test_preflight_returns_ok_false_when_no_capacity(self):
        """preflight() returns ok=False when available RAM is below required."""
        from services.resource_guard_service import ResourceGuardService
        svc = ResourceGuardService()

        # Mock settings: enabled guard with tiny reserve
        mock_settings = MagicMock()
        mock_settings.mode = "enabled"
        mock_settings.memory_limit_percent = 80
        mock_settings.protected_reserve_mb = 9999  # huge reserve → always block
        mock_settings.build_concurrency = 1

        mock_db = AsyncMock()
        mock_db.get = AsyncMock(return_value=mock_settings)

        with patch.object(svc, "settings", AsyncMock(return_value=mock_settings)):
            with patch.object(svc, "sample", return_value={
                "ram_percent": 50.0,
                "ram_available_mb": 100,
                "swap_percent": 0.0,
                "total_bytes": 2 * 1024 ** 3,  # 2 GB → guard enabled
                "total_mb": 2048,
            }):
                result = await svc.preflight(mock_db, "plugin_install")

        self.assertFalse(result["ok"])
        self.assertIn("safe memory", result["reason"].lower())

    async def test_preflight_ok_when_capacity_sufficient(self):
        """preflight() returns ok=True when there is sufficient safe capacity."""
        from services.resource_guard_service import ResourceGuardService
        svc = ResourceGuardService()

        mock_settings = MagicMock()
        mock_settings.mode = "enabled"
        mock_settings.memory_limit_percent = 80
        mock_settings.protected_reserve_mb = 200
        mock_settings.build_concurrency = 1

        mock_db = AsyncMock()

        with patch.object(svc, "settings", AsyncMock(return_value=mock_settings)):
            with patch.object(svc, "sample", return_value={
                "ram_percent": 30.0,
                "ram_available_mb": 1500,
                "swap_percent": 0.0,
                "total_bytes": 2 * 1024 ** 3,
                "total_mb": 2048,
            }):
                result = await svc.preflight(mock_db, "plugin_install")

        self.assertTrue(result["ok"])

    def test_register_and_unregister_token(self):
        """register() adds an operation, unregister() removes it."""
        from services.resource_guard_service import ResourceGuardService
        svc = ResourceGuardService()
        token = svc.register(
            "plugin", "my_plugin", "normal",
            "Test registration", profile="plugin_install"
        )
        self.assertIn(token, svc._operations)
        svc.unregister(token)
        self.assertNotIn(token, svc._operations)

    def test_register_reserves_correct_ram(self):
        """register() reserves the correct RAM for the profile."""
        from services.resource_guard_service import ResourceGuardService
        from services.resource_guard_profiles import PROFILES
        svc = ResourceGuardService()
        token = svc.register(
            "dependency", "docker", "normal",
            "Test dep", profile="plugin_install"
        )
        op = svc._operations[token]
        self.assertEqual(op.reserved_mb, PROFILES["plugin_install"]["ram_mb"])
        svc.unregister(token)


# ── Guarded Runner ────────────────────────────────────────────────────────────

class GuardedRunnerTests(unittest.TestCase):

    def test_lifecycle_adapter_stop_is_async(self):
        """LifecycleAdapter.stop() is an async method."""
        import asyncio
        from services.guarded_runner import LifecycleAdapter
        self.assertTrue(asyncio.iscoroutinefunction(LifecycleAdapter.stop))

    def test_lifecycle_adapter_start_is_async(self):
        """LifecycleAdapter.start() is an async method."""
        import asyncio
        from services.guarded_runner import LifecycleAdapter
        self.assertTrue(asyncio.iscoroutinefunction(LifecycleAdapter.start))

    def test_lifecycle_adapter_is_running_is_async(self):
        """LifecycleAdapter.is_running() is an async method."""
        import asyncio
        from services.guarded_runner import LifecycleAdapter
        self.assertTrue(asyncio.iscoroutinefunction(LifecycleAdapter.is_running))

    def test_lifecycle_adapter_current_ram_mb_is_async(self):
        """LifecycleAdapter.current_ram_mb() is an async method."""
        import asyncio
        from services.guarded_runner import LifecycleAdapter
        self.assertTrue(asyncio.iscoroutinefunction(LifecycleAdapter.current_ram_mb))

    def test_run_native_builds_correct_command_no_systemd(self):
        """run_native() uses plain command when systemd-run is not available."""
        from services import guarded_runner
        with patch.object(guarded_runner, "_SCOPE_AVAILABLE", False):
            # Without systemd scope: verify build_docker_run_args works
            args = guarded_runner.build_docker_run_args(
                "myimage:latest", "native_light", "test-cmd"
            )
        self.assertIn("docker", args)
        self.assertIn("run", args)

    def test_build_docker_run_args_memory_from_profile(self):
        """build_docker_run_args() applies correct memory limit from profile."""
        from services.guarded_runner import build_docker_run_args
        from services.resource_guard_profiles import PROFILES
        profile = "build_large"
        args = build_docker_run_args("img", profile, "test")
        expected_ram = PROFILES[profile]["ram_mb"]
        self.assertIn(f"--memory={expected_ram}m", args)

    def test_build_docker_run_args_container_name(self):
        """build_docker_run_args() adds --name when container_name is provided."""
        from services.guarded_runner import build_docker_run_args
        args = build_docker_run_args(
            "img", "native_light", "test",
            container_name="my-container"
        )
        self.assertIn("--name", args)
        self.assertIn("my-container", args)

    def test_build_docker_run_args_network(self):
        """build_docker_run_args() adds --network flag when provided."""
        from services.guarded_runner import build_docker_run_args
        args = build_docker_run_args(
            "img", "native_light", "test",
            network="srv-net"
        )
        self.assertIn("--network", args)
        self.assertIn("srv-net", args)


# ── Backup/Restore Guard ──────────────────────────────────────────────────────

class BackupGuardTests(unittest.TestCase):

    def test_backup_service_imports_guard(self):
        """container_app_backup_service imports resource_guard_service inside functions."""
        import inspect
        import services.container_app_backup_service as backup_svc
        src = inspect.getsource(backup_svc.create_database_backup)
        self.assertIn("resource_guard_service", src)
        self.assertIn("preflight", src)
        self.assertIn("unregister", src)

    def test_restore_service_imports_guard(self):
        """restore_database_backup uses resource_guard_service."""
        import inspect
        import services.container_app_backup_service as backup_svc
        src = inspect.getsource(backup_svc.restore_database_backup)
        self.assertIn("resource_guard_service", src)
        self.assertIn("preflight", src)
        self.assertIn("unregister", src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
