"""
Tests for Resource Guard Slice 4 — All Core Plugins & Dependencies.

Run on the VPS:
    cd /opt/srv-panel/app
    /opt/srv-panel/venv/bin/python -m pytest tests/test_resource_guard_slice4.py -v

Status: SKELETON — fill in as Slice 4 is implemented.
"""
import sys
import unittest
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


class PluginManifestValidationTests(unittest.TestCase):
    """Test validation of resource_guard section in plugin manifests."""

    def test_valid_manifest_passes_validation(self):
        """Plugin manifest with valid resource_guard section loads without error."""
        self.skipTest("Implement after manifest validation is added")

    def test_safe_temporary_stop_without_adapter_is_rejected(self):
        """safe_temporary_stop=True without lifecycle_adapter raises on plugin load."""
        self.skipTest("Implement after manifest validation is added")

    def test_unknown_profile_name_is_rejected(self):
        """profile name not in PROFILES dict raises on plugin load."""
        self.skipTest("Implement after manifest validation is added")

    def test_plugin_without_resource_guard_section_defaults_to_native_light(self):
        """Plugin with no resource_guard section defaults to native_light + no stop."""
        self.skipTest("Implement after default fallback is added")


class PluginInstallGuardTests(unittest.IsolatedAsyncioTestCase):
    """Test that plugin install/update goes through preflight."""

    async def test_plugin_install_blocked_when_at_capacity(self):
        """Plugin install returns 409 when preflight returns ok=False."""
        self.skipTest("Implement after Guard calls are added to plugin install")

    async def test_plugin_install_registers_token(self):
        """A Guard token is registered while plugin install is running."""
        self.skipTest("Implement after register() call is added")

    async def test_plugin_install_unregisters_token_on_completion(self):
        """Guard token is unregistered after plugin install succeeds or fails."""
        self.skipTest("Implement after token lifecycle is added")

    async def test_plugin_update_uses_plugin_install_profile(self):
        """Plugin update uses 'plugin_install' profile for preflight."""
        self.skipTest("Implement after update guard call is added")


class DependencyGuardTests(unittest.IsolatedAsyncioTestCase):
    """Test that dependency enable/disable goes through preflight."""

    async def test_dependency_enable_blocked_when_at_capacity(self):
        """Dependency enable returns 409 when preflight returns ok=False."""
        self.skipTest("Implement after Guard calls are added to dependency service")

    async def test_light_dependency_toggle_uses_native_light_profile(self):
        """Simple enable/disable uses native_light, not plugin_install."""
        self.skipTest("Implement after profile routing is added")


class BackupRestoreGuardTests(unittest.IsolatedAsyncioTestCase):
    """Test that backup and restore go through preflight."""

    async def test_backup_registers_native_light_token(self):
        """Backup registers a native_light Guard token during execution."""
        self.skipTest("Implement after Guard calls are added to backup service")

    async def test_restore_registers_token(self):
        """Restore registers a Guard token and unregisters on completion."""
        self.skipTest("Implement after Guard calls are added to restore service")


class LifecycleAdapterTests(unittest.TestCase):
    """Test the LifecycleAdapter interface."""

    def test_adapter_stop_is_callable(self):
        """LifecycleAdapter.stop() must be an async method."""
        self.skipTest("Implement after LifecycleAdapter base class is added")

    def test_adapter_is_running_returns_bool(self):
        """LifecycleAdapter.is_running() must return a bool."""
        self.skipTest("Implement after LifecycleAdapter base class is added")

    def test_adapter_current_ram_mb_returns_int(self):
        """LifecycleAdapter.current_ram_mb() must return an int."""
        self.skipTest("Implement after LifecycleAdapter base class is added")


class GuardedRunnerTests(unittest.TestCase):
    """Test the guarded_runner helpers."""

    def test_run_native_builds_correct_command(self):
        """run_native() wraps command in systemd-run --scope if available."""
        self.skipTest("Implement after guarded_runner.py is created")

    def test_run_docker_adds_profile_limits(self):
        """run_docker() adds --memory and --cpus from the profile."""
        self.skipTest("Implement after guarded_runner.py is created")


if __name__ == "__main__":
    unittest.main(verbosity=2)
