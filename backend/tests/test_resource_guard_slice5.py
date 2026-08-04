"""
Tests for Resource Guard Slice 5 — Source Intelligence & Disk Cleanup.

Run on the VPS:
    cd /opt/srv-panel/app
    /opt/srv-panel/venv/bin/python -m pytest tests/test_resource_guard_slice5.py -v

Status: SKELETON — fill in as Slice 5 is implemented.
"""
import sys
import unittest
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


class DetectionPrecedenceTests(unittest.TestCase):
    """Test that detection precedence is respected (Compose > Dockerfile > package > lockfile)."""

    def test_compose_file_overrides_dockerfile_detection(self):
        """If docker-compose.yml exists, its services take precedence over Dockerfile."""
        self.skipTest("Implement after detection precedence is added")

    def test_lockfile_inference_is_low_confidence(self):
        """Detection from lockfile returns confidence=LOW, not applied automatically."""
        self.skipTest("Implement after confidence levels are added")

    def test_compose_postgres_service_maps_to_database_postgresql(self):
        """A 'postgres:' image in Compose auto-maps to kind=postgresql."""
        self.skipTest("Implement after Compose service mapping is added")

    def test_dockerfile_expose_is_medium_confidence(self):
        """EXPOSE in Dockerfile gives confidence=MEDIUM."""
        self.skipTest("Implement after Dockerfile parsing is upgraded")

    def test_false_positive_mariadb_from_lockfile_is_not_auto_applied(self):
        """MariaDB mentioned in lockfile text is suggestion only, not auto-applied."""
        self.skipTest("Implement after lockfile precedence rules are added")


class StaleClearingTests(unittest.TestCase):
    """Test that auto-detected values are cleared when source URL or branch changes."""

    def test_changing_repository_url_clears_auto_detected_fields(self):
        """Updating repository_url clears build_mode, build_command, start_command."""
        self.skipTest("Implement after stale clearing is added")

    def test_user_set_values_survive_source_change(self):
        """Fields manually set by user (user_set=True) are NOT cleared on source change."""
        self.skipTest("Implement after user_set tracking is added")

    def test_low_confidence_db_attachments_cleared_on_source_change(self):
        """DB attachment specs with confidence < HIGH are cleared on source change."""
        self.skipTest("Implement after confidence-based clearing is added")


class ImageInspectionTests(unittest.TestCase):
    """Test registry image inspection endpoint logic."""

    def test_inspect_result_includes_required_fields(self):
        """Image inspection result must include digest, size_mb, exposed_ports, entrypoint."""
        self.skipTest("Implement after inspect-image endpoint is added")

    def test_invalid_image_reference_returns_400(self):
        """Non-existent or malformed image reference returns 400."""
        self.skipTest("Implement after validation is added")

    def test_inspect_uses_image_pull_profile(self):
        """Image inspection registers an image_pull profile Guard token."""
        self.skipTest("Implement after preflight guard is added to inspect endpoint")


class CleanupInventoryTests(unittest.TestCase):
    """Test dry-run cleanup inventory logic."""

    def test_inventory_lists_stale_build_dirs(self):
        """Old build dirs appear in the inventory with path, size_mb, age_days."""
        self.skipTest("Implement after inventory service is added")

    def test_inventory_excludes_active_image(self):
        """Current image_digest of a running app is in protected list, not deletable."""
        self.skipTest("Implement after protection list is added")

    def test_inventory_excludes_rollback_image(self):
        """Previous deployment image with rollback_enabled=True is protected."""
        self.skipTest("Implement after rollback protection is added")

    def test_inventory_never_includes_volumes(self):
        """Docker volumes are never included in the cleanup inventory."""
        self.skipTest("Implement after volume exclusion rule is added")

    def test_inventory_never_includes_backups(self):
        """Backup archives are never included in the cleanup inventory."""
        self.skipTest("Implement after backup exclusion rule is added")


class CleanupRunTests(unittest.TestCase):
    """Test confirmed cleanup execution."""

    def test_cleanup_only_deletes_selected_categories(self):
        """Cleanup run only deletes items in the include list."""
        self.skipTest("Implement after cleanup run endpoint is added")

    def test_cleanup_uses_native_light_profile(self):
        """Cleanup registers a native_light Guard token during execution."""
        self.skipTest("Implement after Guard call is added to cleanup service")

    def test_cleanup_never_deletes_protected_items(self):
        """Even if a protected item somehow ends up in include list, it is skipped."""
        self.skipTest("Implement after server-side protection enforcement is added")


if __name__ == "__main__":
    unittest.main(verbosity=2)
