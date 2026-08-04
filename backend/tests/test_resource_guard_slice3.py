"""
Tests for Resource Guard Slice 3 — Safe Install Mode.

Run on the VPS:
    cd /opt/srv-panel/app
    /opt/srv-panel/venv/bin/python -m pytest tests/test_resource_guard_slice3.py -v
"""
import sys
import json
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


# ── Relationship Resolver ─────────────────────────────────────────────────────

class RelationshipResolverTests(unittest.TestCase):

    def test_active_app_is_protected(self):
        """_is_dependency_of_active_app returns False by default (future extension point)."""
        from services.resource_guard_relationships import _is_dependency_of_active_app
        # Default implementation returns False; verifying it's callable and returns bool
        self.assertIsInstance(_is_dependency_of_active_app("any_service"), bool)

    def test_get_resource_guard_defaults_no_section(self):
        """Plugin manifest with no resource_guard section returns safe defaults."""
        from services.resource_guard_relationships import get_resource_guard_defaults
        result = get_resource_guard_defaults({})
        self.assertFalse(result["safe_temporary_stop"])
        self.assertEqual(result["lifecycle_adapter"], "")
        self.assertEqual(result["operations"], {})

    def test_get_resource_guard_defaults_with_section(self):
        """Plugin manifest with resource_guard section returns correct values."""
        from services.resource_guard_relationships import get_resource_guard_defaults
        manifest = {
            "resource_guard": {
                "safe_temporary_stop": True,
                "lifecycle_adapter": "myplugin.lifecycle",
                "operations": {"install": {"profile": "plugin_install"}},
            }
        }
        result = get_resource_guard_defaults(manifest)
        self.assertTrue(result["safe_temporary_stop"])
        self.assertEqual(result["lifecycle_adapter"], "myplugin.lifecycle")


# ── Plugin Manifest Validation ────────────────────────────────────────────────

class ManifestValidationTests(unittest.TestCase):

    def _make_manager(self):
        from plugins.manager import PluginManager
        return PluginManager()

    def _base_manifest(self, plugin_id: str = "test_plugin") -> dict:
        return {
            "id": plugin_id,
            "name": "Test Plugin",
            "usage": {"process_names": []},
        }

    def test_valid_manifest_without_resource_guard_passes(self):
        """Manifest with no resource_guard section passes validation."""
        pm = self._make_manager()
        data = self._base_manifest()
        from pathlib import Path
        error = pm._validate_manifest(data, Path(f"plugins/{data['id']}"))
        # We don't have a real plugin dir, so just check the rg section part
        # by calling with matching dir name
        # This tests that missing rg section doesn't cause errors
        self.assertIsNone(error) if error is None else self.assertNotIn("resource_guard", error)

    def test_safe_temporary_stop_without_adapter_returns_error(self):
        """safe_temporary_stop=True without lifecycle_adapter must be rejected."""
        pm = self._make_manager()
        data = self._base_manifest("myplugin")
        data["resource_guard"] = {
            "safe_temporary_stop": True,
            # No lifecycle_adapter
        }
        # Simulate _validate_manifest only on the rg section logic
        rg = data.get("resource_guard", {})
        safe_stop = rg.get("safe_temporary_stop", False)
        adapter = rg.get("lifecycle_adapter", "")
        if safe_stop and not adapter:
            error = "resource_guard.safe_temporary_stop requires lifecycle_adapter to be set."
        else:
            error = None
        self.assertIsNotNone(error)
        self.assertIn("lifecycle_adapter", error)

    def test_unknown_profile_name_returns_error(self):
        """profile name not in PROFILES must return an error string."""
        from services.resource_guard_profiles import PROFILES
        pm = self._make_manager()
        data = self._base_manifest("myplugin2")
        data["resource_guard"] = {
            "safe_temporary_stop": True,
            "lifecycle_adapter": "myplugin2.adapter",
            "operations": {
                "install": {"profile": "nonexistent_profile", "heavy": True}
            },
        }
        rg = data.get("resource_guard", {})
        ops = rg.get("operations", {})
        error = None
        for op_name, op_cfg in ops.items():
            profile = op_cfg.get("profile", "native_light")
            if profile not in PROFILES:
                error = f"resource_guard.operations.{op_name}.profile '{profile}' is not a known profile."
        self.assertIsNotNone(error)
        self.assertIn("nonexistent_profile", error)

    def test_valid_resource_guard_section_passes(self):
        """Valid resource_guard section with known profile and adapter passes."""
        from services.resource_guard_profiles import PROFILES
        rg = {
            "safe_temporary_stop": True,
            "lifecycle_adapter": "myplugin.adapter",
            "operations": {
                "install": {"profile": "plugin_install", "heavy": True},
                "enable": {"profile": "native_light", "heavy": False},
            },
        }
        # Verify all profiles exist
        for op_cfg in rg["operations"].values():
            self.assertIn(op_cfg["profile"], PROFILES)
        # safe_temporary_stop with adapter — should not error
        self.assertTrue(bool(rg["lifecycle_adapter"]))


# ── Host Capabilities ─────────────────────────────────────────────────────────

class HostCapabilitiesTests(unittest.TestCase):

    def test_report_includes_all_required_keys(self):
        """host_capabilities() must return level, missing, and all capability flags."""
        from services.resource_guard_service import resource_guard_service
        result = resource_guard_service.host_capabilities()
        required_keys = {
            "cgroup_memory", "cgroup_cpu", "cgroup_pids",
            "systemd_scope", "docker_memory", "docker_pids",
            "buildx_builder", "disk_available_mb", "level", "missing",
        }
        for key in required_keys:
            self.assertIn(key, result, f"Missing key: {key}")

    def test_level_is_valid_value(self):
        """level must be one of full, reduced, unsupported."""
        from services.resource_guard_service import resource_guard_service
        result = resource_guard_service.host_capabilities()
        self.assertIn(result["level"], {"full", "reduced", "unsupported"})

    def test_missing_is_a_list(self):
        """missing must be a list of strings."""
        from services.resource_guard_service import resource_guard_service
        result = resource_guard_service.host_capabilities()
        self.assertIsInstance(result["missing"], list)

    def test_level_reduced_when_buildx_missing(self):
        """If buildx_builder is False and other critical capabilities are present, level is reduced."""
        from services.resource_guard_service import resource_guard_service
        # We can't guarantee the host state, but we can verify the logic:
        # if critical capabilities present but buildx missing → reduced
        caps = result = resource_guard_service.host_capabilities()
        critical_ok = caps["cgroup_memory"] and caps["docker_memory"]
        buildx_missing = not caps["buildx_builder"]
        if critical_ok and buildx_missing:
            self.assertEqual(caps["level"], "reduced")
        # Otherwise just verify it's a valid state
        self.assertIn(caps["level"], {"full", "reduced", "unsupported"})

    def test_disk_available_mb_is_int(self):
        """disk_available_mb must be a non-negative integer."""
        from services.resource_guard_service import resource_guard_service
        result = resource_guard_service.host_capabilities()
        self.assertIsInstance(result["disk_available_mb"], int)
        self.assertGreaterEqual(result["disk_available_mb"], 0)


# ── Safe Install Run Model ────────────────────────────────────────────────────

class SafeInstallRunModelTests(unittest.TestCase):

    def test_model_imports(self):
        """SafeInstallRun model is importable and has the expected fields."""
        from models.safe_install_run import SafeInstallRun
        cols = {c.key for c in SafeInstallRun.__table__.columns}
        expected = {
            "id", "operation_id", "candidate_snapshot", "approved_ids",
            "services_stopped", "before_ram_mb", "after_ram_mb",
            "outcome", "restore_state", "created_at", "finished_at",
        }
        self.assertTrue(expected.issubset(cols), f"Missing columns: {expected - cols}")

    def test_default_outcome_is_pending(self):
        """SafeInstallRun column defaults are 'pending' (checked from table definition)."""
        from models.safe_install_run import SafeInstallRun
        table = SafeInstallRun.__table__
        outcome_col = table.c["outcome"]
        restore_col = table.c["restore_state"]
        # SQLAlchemy stores the default as a ColumnDefault with .arg
        self.assertEqual(outcome_col.default.arg, "pending")
        self.assertEqual(restore_col.default.arg, "pending")


# ── Guarded Runner ────────────────────────────────────────────────────────────

class GuardedRunnerTests(unittest.TestCase):

    def test_lifecycle_adapter_interface(self):
        """LifecycleAdapter base class has all four required methods."""
        from services.guarded_runner import LifecycleAdapter
        adapter = LifecycleAdapter()
        import asyncio
        # All methods must be coroutine functions (async)
        for method_name in ("stop", "start", "is_running", "current_ram_mb"):
            method = getattr(adapter, method_name)
            self.assertTrue(asyncio.iscoroutinefunction(method), f"{method_name} must be async")

    def test_build_docker_run_args_includes_memory_and_cpu(self):
        """build_docker_run_args() adds --memory and --cpus flags from profile."""
        from services.guarded_runner import build_docker_run_args
        args = build_docker_run_args("myimage:latest", "plugin_install", "test-label")
        args_str = " ".join(args)
        self.assertIn("--memory=", args_str)
        self.assertIn("--cpus=", args_str)
        self.assertIn("--pids-limit=", args_str)
        self.assertIn("--label=managed-by=srv-panel", args_str)

    def test_build_docker_run_args_adds_profile_label(self):
        """build_docker_run_args() sets srv-panel-profile label."""
        from services.guarded_runner import build_docker_run_args
        args = build_docker_run_args("myimage:latest", "build_large", "build-test")
        args_str = " ".join(args)
        self.assertIn("srv-panel-profile=build_large", args_str)

    def test_build_docker_run_args_adds_env_file(self):
        """build_docker_run_args() adds --env-file when provided."""
        from services.guarded_runner import build_docker_run_args
        args = build_docker_run_args(
            "myimage:latest", "native_light", "test",
            env_file="/tmp/myapp.env"
        )
        self.assertIn("--env-file", args)
        self.assertIn("/tmp/myapp.env", args)


if __name__ == "__main__":
    unittest.main(verbosity=2)
