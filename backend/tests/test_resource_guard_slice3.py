"""
Tests for Resource Guard Slice 3 — Safe Install Mode.

Run on the VPS:
    cd /opt/srv-panel/app
    /opt/srv-panel/venv/bin/python -m pytest tests/test_resource_guard_slice3.py -v

Status: SKELETON — fill in as Slice 3 is implemented.
"""
import sys
import unittest
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


class RelationshipResolverTests(unittest.TestCase):
    """Test classify_services() correctly labels protected / required / optional."""

    def test_active_app_is_protected(self):
        """A running Apps Engine app must never appear as optional candidate."""
        self.skipTest("Implement after classify_services() is added")

    def test_plugin_without_lifecycle_adapter_not_offered(self):
        """Plugin with safe_temporary_stop but no adapter must not be a candidate."""
        self.skipTest("Implement after lifecycle adapter validation is added")

    def test_dependency_of_install_target_is_required(self):
        """If new app needs PostgreSQL, PostgreSQL must be classified as required."""
        self.skipTest("Implement after dependency resolution is added")

    def test_optional_plugin_with_adapter_is_candidate(self):
        """Plugin with safe_temporary_stop=True + valid adapter appears as optional."""
        self.skipTest("Implement after full classification is added")


class HostCapabilitiesTests(unittest.TestCase):
    """Test host_capabilities() report."""

    def test_report_includes_all_required_keys(self):
        """host_capabilities() must return level, missing, and all capability flags."""
        self.skipTest("Implement after host_capabilities() is added")

    def test_level_is_reduced_when_buildx_missing(self):
        """If srv-panel-builder not found, level must be 'reduced', not 'full'."""
        self.skipTest("Implement after capability detection is added")

    def test_level_is_unsupported_when_cgroup_missing(self):
        """If cgroup memory not available, level must be 'unsupported'."""
        self.skipTest("Implement after capability detection is added")


class SafeInstallLifecycleTests(unittest.IsolatedAsyncioTestCase):
    """Test the Safe Install request → approve → complete flow."""

    async def test_request_returns_candidate_list(self):
        """request_safe_install() returns list with RAM, reason, dependencies per candidate."""
        self.skipTest("Implement after request_safe_install() is added")

    async def test_approve_stops_candidates_one_by_one(self):
        """approve_safe_install() stops each candidate and rechecks capacity after each."""
        self.skipTest("Implement after approve_safe_install() is added")

    async def test_stop_fails_aborts_whole_run(self):
        """If stopping a candidate fails, entire Safe Install run is aborted."""
        self.skipTest("Implement after error handling is added")

    async def test_complete_restores_when_capacity_allows(self):
        """complete_safe_install() restores stopped services when new app + originals fit."""
        self.skipTest("Implement after complete_safe_install() is added")

    async def test_complete_pauses_new_app_when_cannot_coexist(self):
        """complete_safe_install() pauses new app if originals can't coexist with it."""
        self.skipTest("Implement after post-install coexistence check is added")


class SafeInstallRunModelTests(unittest.IsolatedAsyncioTestCase):
    """Test SafeInstallRun DB model."""

    async def test_run_record_created_on_request(self):
        """request_safe_install() creates a SafeInstallRun DB record."""
        self.skipTest("Implement after SafeInstallRun model is created")

    async def test_restore_state_updated_on_complete(self):
        """complete_safe_install() updates restore_state field on the run record."""
        self.skipTest("Implement after model is created")


class SafeInstallApiTests(unittest.IsolatedAsyncioTestCase):
    """Test new Safe Install API endpoints."""

    async def test_request_endpoint_returns_candidates(self):
        """POST /api/resource-guard/safe-install/request returns candidate list."""
        self.skipTest("Implement after endpoint is added")

    async def test_approve_endpoint_starts_stop_sequence(self):
        """POST /api/resource-guard/safe-install/{run_id}/approve triggers stop."""
        self.skipTest("Implement after endpoint is added")

    async def test_restore_endpoint_rechecks_capacity_first(self):
        """POST /api/resource-guard/safe-install/{run_id}/restore runs preflight."""
        self.skipTest("Implement after endpoint is added")

    async def test_host_capabilities_endpoint(self):
        """GET /api/resource-guard/host-capabilities returns valid report."""
        self.skipTest("Implement after endpoint is added")


if __name__ == "__main__":
    unittest.main(verbosity=2)
