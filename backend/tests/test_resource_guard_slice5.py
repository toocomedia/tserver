"""
Tests for Resource Guard Slice 5 — Source Intelligence & Disk Cleanup.

Run on the VPS:
    cd /opt/srv-panel/app
    /opt/srv-panel/venv/bin/python -m pytest tests/test_resource_guard_slice5.py -v
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from services.container_app_inspection_service import (
    CONFIDENCE_HIGH, CONFIDENCE_LOW, CONFIDENCE_MEDIUM,
    _compose_databases, _databases_with_confidence, _find_compose,
    _text_markers,
)
from services.container_app_service import clear_auto_detected_fields
from services import disk_cleanup_service


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _fake_app(**kwargs):
    defaults = {
        "source_type": "git",
        "build_mode": "railpack",
        "repository_url": "https://github.com/org/repo",
        "branch": "main",
        "pending_database_specs": None,
        "image_digest": None,
        "previous_image": None,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


class DetectionPrecedenceTests(unittest.TestCase):
    """Test that detection precedence is respected (Compose > Dockerfile > package > lockfile)."""

    def _run(self, root: Path):
        files = {p.name for p in root.iterdir() if p.is_file()}
        from services.container_app_inspection_service import _read_sources
        text = _read_sources(root)
        confirmed, suggestions = _databases_with_confidence(root, files, text)
        return confirmed, suggestions

    def test_compose_file_overrides_lockfile_detection(self):
        """If docker-compose.yml has postgres image, confidence is HIGH (not LOW from lockfile)."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # Lockfile mentions mariadb (LOW)
            _write(root / "package-lock.json", '{"name":"app","packages":{"mariadb":{}}}')
            # Compose has postgres (HIGH)
            _write(root / "docker-compose.yml",
                   "services:\n  db:\n    image: postgres:15\n")
            confirmed, suggestions = self._run(root)
            kinds = {d["kind"] for d in confirmed}
            self.assertIn("postgresql", kinds)
            pg = next(d for d in confirmed if d["kind"] == "postgresql")
            self.assertEqual(pg["confidence"], CONFIDENCE_HIGH)

    def test_lockfile_inference_is_low_confidence(self):
        """Detection from lockfile only → returned as suggestion with confidence=LOW."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root / "package-lock.json",
                   '{"name":"app","packages":{"mariadb":{"version":"3.0.0"}}}')
            confirmed, suggestions = self._run(root)
            # Should NOT be in confirmed (no Compose/Dockerfile evidence)
            mariadb_confirmed = [d for d in confirmed if d["kind"] == "mariadb/mysql"]
            self.assertEqual(len(mariadb_confirmed), 0,
                "mariadb from lockfile should not be auto-confirmed")
            # Should be in suggestions with LOW confidence
            mariadb_sugg = [d for d in suggestions if d["kind"] == "mariadb/mysql"]
            self.assertTrue(len(mariadb_sugg) > 0, "mariadb should appear as suggestion")
            self.assertEqual(mariadb_sugg[0]["confidence"], CONFIDENCE_LOW)

    def test_compose_postgres_service_maps_to_database_postgresql(self):
        """A 'postgres:' image in Compose auto-maps to kind=postgresql with HIGH confidence."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            compose = root / "docker-compose.yml"
            _write(compose, "services:\n  db:\n    image: postgres:15-alpine\n")
            kinds = _compose_databases(compose)
            self.assertIn("postgresql", kinds)

    def test_dockerfile_expose_is_medium_confidence(self):
        """FROM postgres in Dockerfile gives confidence=MEDIUM."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root / "Dockerfile",
                   "FROM node:20-alpine\nRUN apt-get install -y postgresql\nEXPOSE 3000\n")
            confirmed, _ = self._run(root)
            pg = next((d for d in confirmed if d["kind"] == "postgresql"), None)
            self.assertIsNotNone(pg, "postgresql should be detected from Dockerfile RUN")
            self.assertEqual(pg["confidence"], CONFIDENCE_MEDIUM)

    def test_false_positive_mariadb_from_lockfile_is_not_auto_applied(self):
        """MariaDB mentioned only in lockfile text is suggestion only, not in confirmed."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # lockfile that mentions mariadb as a package name
            _write(root / "yarn.lock",
                   "mariadb@3.0.0:\n  version \"3.0.0\"\n  resolved \"...\"\n")
            confirmed, suggestions = self._run(root)
            auto_applied = [d for d in confirmed if d["kind"] == "mariadb/mysql"]
            self.assertEqual(auto_applied, [],
                "mariadb from yarn.lock only should NOT be auto-applied")


class StaleClearingTests(unittest.TestCase):
    """Test that auto-detected values are cleared when source URL changes."""

    def test_changing_repository_url_clears_build_mode(self):
        """Updating repository_url resets build_mode to railpack."""
        app = _fake_app(build_mode="dockerfile")
        clear_auto_detected_fields(app, "https://github.com/org/other", "main")
        self.assertEqual(app.build_mode, "railpack")

    def test_no_change_does_not_clear_fields(self):
        """If URL and branch are the same, nothing is cleared."""
        app = _fake_app(build_mode="dockerfile")
        clear_auto_detected_fields(app, app.repository_url, app.branch)
        self.assertEqual(app.build_mode, "dockerfile")

    def test_low_confidence_db_specs_cleared_on_source_change(self):
        """DB specs with confidence=LOW are removed when source URL changes."""
        specs = [
            {"kind": "mariadb/mysql", "confidence": "LOW"},
            {"kind": "postgresql", "confidence": "HIGH"},
        ]
        app = _fake_app(pending_database_specs=json.dumps(specs))
        clear_auto_detected_fields(app, "https://github.com/org/new-repo", "main")
        remaining = json.loads(app.pending_database_specs or "[]")
        kinds = [s["kind"] for s in remaining]
        self.assertNotIn("mariadb/mysql", kinds)
        self.assertIn("postgresql", kinds)

    def test_high_confidence_db_specs_survive_source_change(self):
        """HIGH and MEDIUM confidence specs survive source URL change."""
        specs = [
            {"kind": "redis", "confidence": "MEDIUM"},
            {"kind": "postgresql", "confidence": "HIGH"},
        ]
        app = _fake_app(pending_database_specs=json.dumps(specs))
        clear_auto_detected_fields(app, "https://github.com/org/new-repo", "main")
        remaining = json.loads(app.pending_database_specs or "[]")
        self.assertEqual(len(remaining), 2)

    def test_branch_change_clears_low_confidence_specs(self):
        """Changing branch also clears LOW confidence specs."""
        specs = [{"kind": "mongodb", "confidence": "LOW"}]
        app = _fake_app(pending_database_specs=json.dumps(specs))
        clear_auto_detected_fields(app, app.repository_url, "develop")
        remaining = json.loads(app.pending_database_specs or "[]")
        self.assertEqual(remaining, [])


class ImageInspectionTests(unittest.TestCase):
    """Test registry image inspection service logic."""

    def test_inspect_result_includes_required_fields(self):
        """Image inspection result must include digest, size_mb, exposed_ports, entrypoint."""
        from services.container_app_image_inspect_service import _pull_and_inspect

        fake_inspect_output = json.dumps([{
            "RepoDigests": ["nginx@sha256:abc123"],
            "Size": 50 * 1024 * 1024,
            "VirtualSize": 50 * 1024 * 1024,
            "Config": {
                "ExposedPorts": {"80/tcp": {}},
                "Entrypoint": ["/docker-entrypoint.sh"],
                "Cmd": ["nginx", "-g", "daemon off;"],
                "Healthcheck": {"Test": ["CMD-SHELL", "curl -f http://localhost/ || exit 1"]},
                "Labels": {},
            },
        }])

        def fake_run(cmd, *, timeout):
            if "pull" in cmd:
                return SimpleNamespace(returncode=0, stdout="", stderr="")
            if "inspect" in cmd:
                return SimpleNamespace(returncode=0, stdout=fake_inspect_output, stderr="")
            # rmi
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        with patch("services.container_app_image_inspect_service._run", side_effect=fake_run):
            result = _pull_and_inspect("nginx:latest")

        self.assertIn("digest", result)
        self.assertIn("size_mb", result)
        self.assertIn("exposed_ports", result)
        self.assertIn("entrypoint", result)
        self.assertEqual(result["exposed_ports"], ["80"])
        self.assertAlmostEqual(result["size_mb"], 50.0, places=0)

    def test_umami_image_recommends_postgresql_and_port(self):
        from services.container_app_image_inspect_service import _recommendations
        result = _recommendations("docker.umami.is/umami-software/umami:latest", ["3000"], [], None)
        self.assertEqual(result["internal_port"], 3000)
        self.assertEqual(result["database_types"], ["postgresql"])
        self.assertEqual(result["required_environment_names"], ["DATABASE_URL"])

    def test_database_environment_names_are_generic_recommendations(self):
        from services.container_app_image_inspect_service import _recommendations
        result = _recommendations("example.org/team/app:1", ["8080"], ["DATABASE_URL", "REDIS_URL"], None)
        self.assertEqual(result["database_types"], ["postgresql", "redis"])

    def test_invalid_image_reference_raises_value_error(self):
        """Non-valid image reference raises ValueError."""
        from services.container_app_image_inspect_service import validate_image_reference
        with self.assertRaises(ValueError):
            validate_image_reference("../../../etc/passwd")
        with self.assertRaises(ValueError):
            validate_image_reference("")

    def test_inspect_registers_guard_token(self):
        """inspect_image() registers and unregisters a Guard image_pull token."""
        import asyncio
        from services.container_app_image_inspect_service import inspect_image

        tokens_registered = []
        tokens_unregistered = []

        original_register = __import__("services.resource_guard_service", fromlist=["resource_guard_service"]).resource_guard_service.register
        original_unregister = __import__("services.resource_guard_service", fromlist=["resource_guard_service"]).resource_guard_service.unregister

        def fake_register(*args, **kwargs):
            tok = original_register(*args, **kwargs)
            tokens_registered.append(tok)
            return tok

        def fake_unregister(token):
            tokens_unregistered.append(token)
            original_unregister(token)

        fake_meta = {
            "digest": "sha256:abc", "size_mb": 10.0,
            "exposed_ports": [], "entrypoint": [], "cmd": [],
            "healthcheck": None, "labels": {}, "reference": "alpine:latest",
        }

        with patch("services.resource_guard_service.resource_guard_service.register", side_effect=fake_register), \
             patch("services.resource_guard_service.resource_guard_service.unregister", side_effect=fake_unregister), \
             patch("services.container_app_image_inspect_service.asyncio.to_thread", return_value=fake_meta):
            asyncio.run(inspect_image("alpine:latest"))

        self.assertTrue(len(tokens_registered) > 0, "Guard token should be registered")
        self.assertEqual(tokens_registered, tokens_unregistered, "Guard token should be unregistered")


class CleanupInventoryTests(unittest.TestCase):
    """Test dry-run cleanup inventory logic."""

    def _inventory_sync(self, active=None, rollback=None):
        import asyncio
        return asyncio.run(
            disk_cleanup_service.inventory(active or set(), rollback or set())
        )

    def test_inventory_lists_stale_build_dirs(self):
        """Old build dirs appear in the inventory with path, size_mb, age_days."""
        with tempfile.TemporaryDirectory() as fake_root:
            # Create a fake old build dir
            old_dir = Path(fake_root) / "99"
            old_dir.mkdir()
            (old_dir / "source.txt").write_text("hello")

            with patch("services.disk_cleanup_service.config") as mock_cfg, \
                 patch("services.disk_cleanup_service._run") as mock_run:
                mock_cfg.CONTAINER_APP_ROOT = fake_root
                mock_cfg.CONTAINER_APP_BACKUP_ROOT = "/nowhere/backups"
                mock_run.return_value = SimpleNamespace(returncode=1, stdout="", stderr="")

                items = self._inventory_sync()

        build_items = [i for i in items if i["type"] == "build_dir"]
        self.assertTrue(len(build_items) > 0, "Should find the build dir")
        item = build_items[0]
        self.assertIn("item_id", item)
        self.assertIn("size_mb", item)
        self.assertIn("age_days", item)

    def test_inventory_excludes_active_image_from_deletable(self):
        """Current image_digest of a running app is protected, not deletable."""
        fake_dangling = "sha256deadbeef\t10MB\t2024-01-01"
        active_digest = "sha256deadbeef"

        with patch("services.disk_cleanup_service.config") as mock_cfg, \
             patch("services.disk_cleanup_service._run") as mock_run:
            mock_cfg.CONTAINER_APP_ROOT = "/tmp/nonexistent-abc123"
            mock_cfg.CONTAINER_APP_BACKUP_ROOT = "/nowhere/backups"
            mock_run.return_value = SimpleNamespace(
                returncode=0, stdout=fake_dangling, stderr=""
            )
            items = self._inventory_sync(active={active_digest})

        protected = [i for i in items if i.get("protected")]
        self.assertTrue(any(active_digest in i["path"] for i in protected),
                        "Active image should be in protected list")

    def test_inventory_excludes_rollback_image(self):
        """Previous deployment image is protected."""
        rollback_id = "sha256rollback99"
        fake_dangling = f"{rollback_id}\t5MB\t2024-01-01"

        with patch("services.disk_cleanup_service.config") as mock_cfg, \
             patch("services.disk_cleanup_service._run") as mock_run:
            mock_cfg.CONTAINER_APP_ROOT = "/tmp/nonexistent-abc123"
            mock_cfg.CONTAINER_APP_BACKUP_ROOT = "/nowhere/backups"
            mock_run.return_value = SimpleNamespace(
                returncode=0, stdout=fake_dangling, stderr=""
            )
            items = self._inventory_sync(rollback={rollback_id})

        protected = [i for i in items if i.get("protected")]
        self.assertTrue(any(rollback_id in i["path"] for i in protected),
                        "Rollback image should be protected")

    def test_inventory_never_includes_volumes(self):
        """Volumes are not collected at all (no volume type in inventory)."""
        with patch("services.disk_cleanup_service.config") as mock_cfg, \
             patch("services.disk_cleanup_service._run") as mock_run:
            mock_cfg.CONTAINER_APP_ROOT = "/tmp/nonexistent-abc123"
            mock_cfg.CONTAINER_APP_BACKUP_ROOT = "/nowhere/backups"
            mock_run.return_value = SimpleNamespace(returncode=1, stdout="", stderr="")
            items = self._inventory_sync()

        volume_items = [i for i in items if "volume" in i.get("type", "")]
        self.assertEqual(volume_items, [], "No volumes should appear in inventory")

    def test_inventory_never_includes_backups(self):
        """Backup archives are never included (they live in backup root, which is excluded)."""
        with tempfile.TemporaryDirectory() as backup_root:
            # Create a "backup" file
            backup_file = Path(backup_root) / "app-1-20240101.tar.gz"
            backup_file.write_bytes(b"fake backup")

            with patch("services.disk_cleanup_service.config") as mock_cfg, \
                 patch("services.disk_cleanup_service._run") as mock_run:
                mock_cfg.CONTAINER_APP_ROOT = "/tmp/nonexistent-abc123"
                mock_cfg.CONTAINER_APP_BACKUP_ROOT = backup_root
                mock_run.return_value = SimpleNamespace(returncode=1, stdout="", stderr="")
                items = self._inventory_sync()

        backup_items = [i for i in items if backup_root in i.get("path", "")]
        self.assertEqual(backup_items, [], "Backup files should not appear in inventory")


class CleanupRunTests(unittest.TestCase):
    """Test confirmed cleanup execution."""

    def _run_cleanup(self, include_ids, active=None, rollback=None):
        import asyncio
        return asyncio.run(
            disk_cleanup_service.run_cleanup(include_ids, active or set(), rollback or set())
        )

    def test_cleanup_only_deletes_selected_ids(self):
        """Cleanup run only deletes items in the include list."""
        with tempfile.TemporaryDirectory() as fake_root:
            dir_a = Path(fake_root) / "1"
            dir_a.mkdir()
            (dir_a / "file.txt").write_text("a")
            dir_b = Path(fake_root) / "2"
            dir_b.mkdir()
            (dir_b / "file.txt").write_text("b")

            with patch("services.disk_cleanup_service.config") as mock_cfg, \
                 patch("services.disk_cleanup_service._run") as mock_run:
                mock_cfg.CONTAINER_APP_ROOT = fake_root
                mock_cfg.CONTAINER_APP_BACKUP_ROOT = "/nowhere/backups"
                mock_run.return_value = SimpleNamespace(returncode=1, stdout="", stderr="")

                import asyncio
                all_items = asyncio.run(
                    disk_cleanup_service.inventory(set(), set())
                )

            build_items = [i for i in all_items if i["type"] == "build_dir"]
            self.assertEqual(len(build_items), 2)

            # Only delete one
            with patch("services.disk_cleanup_service.config") as mock_cfg, \
                 patch("services.disk_cleanup_service._run") as mock_run:
                mock_cfg.CONTAINER_APP_ROOT = fake_root
                mock_cfg.CONTAINER_APP_BACKUP_ROOT = "/nowhere/backups"
                mock_run.return_value = SimpleNamespace(returncode=1, stdout="", stderr="")
                result = self._run_cleanup([build_items[0]["item_id"]])

            self.assertEqual(len(result["deleted"]), 1)
            # Only one dir should be gone
            remaining = [d for d in Path(fake_root).iterdir() if d.is_dir()]
            self.assertEqual(len(remaining), 1)

    def test_cleanup_registers_guard_token(self):
        """Cleanup run registers a native_light Guard token."""
        tokens = []
        original_register = disk_cleanup_service.resource_guard_service.register

        def fake_register(*args, **kwargs):
            tok = original_register(*args, **kwargs)
            tokens.append(kwargs.get("profile", ""))
            return tok

        with patch("services.disk_cleanup_service.resource_guard_service.register",
                   side_effect=fake_register), \
             patch("services.disk_cleanup_service._build_inventory", return_value=[]):
            self._run_cleanup([])

        self.assertIn("native_light", tokens)

    def test_cleanup_never_deletes_protected_items(self):
        """Even if a protected item's ID is in include list, it is skipped."""
        from services.disk_cleanup_service import InventoryItem, _make_id

        active_id = "sha256protectedimage"
        item_id = _make_id("dangling_image", active_id)

        protected_item = InventoryItem(
            item_id=item_id,
            type="dangling_image",
            path=active_id,
            size_mb=100.0,
            age_days=0,
            protected=True,
            protect_reason="Active image",
        )

        with patch("services.disk_cleanup_service._build_inventory",
                   return_value=[protected_item]):
            result = self._run_cleanup([item_id], active={active_id})

        self.assertEqual(result["deleted"], [])
        self.assertEqual(len(result["skipped"]), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
