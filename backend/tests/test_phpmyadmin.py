import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from plugins.phpmyadmin.service import PhpMyAdminService


class PhpMyAdminStateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(
            os.environ, {"PHPMYADMIN_DATA_DIR": self.temp.name}
        )
        self.env.start()
        self.service = PhpMyAdminService()
        self.service.data_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def test_update_state_keeps_schema_and_merges(self):
        self.service.update_state(installed_at=123)

        state = json.loads(self.service.state_path.read_text(encoding="utf-8"))
        self.assertEqual(state["schema_version"], 2)
        self.assertEqual(state["installed_at"], 123)

    def test_port_reads_state_then_env_override(self):
        self.assertEqual(self.service.port, 8090)

        self.service.update_state(port=8093)
        self.assertEqual(self.service.port, 8093)

        with patch.dict(os.environ, {"PHPMYADMIN_PORT": "9000"}):
            self.assertEqual(self.service.port, 9000)

    def test_purge_data_requires_uninstalled_app(self):
        self.service.state_path.write_text("{}", encoding="utf-8")
        self.service.secret_path.write_text("s" * 64, encoding="utf-8")
        self.service.marker_path.write_text("2", encoding="utf-8")
        self.service.is_installed = Mock(return_value=False)

        self.service.purge_data()

        self.assertFalse(self.service.state_path.exists())
        self.assertFalse(self.service.secret_path.exists())
        self.assertFalse(self.service.marker_path.exists())

    def test_purge_data_blocks_while_installed(self):
        self.service.state_path.write_text("{}", encoding="utf-8")
        self.service.is_installed = Mock(return_value=True)

        with self.assertRaises(RuntimeError):
            self.service.purge_data()

    def test_php_version_reads_managed_versions_highest_first(self):
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp) / "managed-versions.json"
            state.write_text(
                json.dumps({"8.1": ["php8.1-fpm"], "8.3": ["php8.3-fpm"]}),
                encoding="utf-8",
            )
            with patch("plugins.phpmyadmin.service.PHP_STATE_PATH", state):
                self.assertEqual(self.service.php_version(), "8.3")

    def test_php_binary_falls_back_to_plain_php(self):
        service = PhpMyAdminService()
        service.php_version = Mock(return_value="8.3")
        with patch(
            "plugins.phpmyadmin.service.os.path.isfile",
            side_effect=lambda path: path == "/usr/bin/php",
        ):
            self.assertEqual(service.php_binary(), "/usr/bin/php")


class PhpMyAdminLifecycleTests(unittest.TestCase):
    def test_needs_reconcile_detects_old_marker(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(
            os.environ, {"PHPMYADMIN_DATA_DIR": temp}
        ):
            service = PhpMyAdminService()
            service.htdocs.mkdir(parents=True)
            (service.htdocs / "index.php").write_text("<?php", encoding="utf-8")

            self.assertTrue(service.needs_reconcile())

            service.marker_path.write_text(
                service.config_version, encoding="utf-8"
            )
            self.assertFalse(service.needs_reconcile())

    def test_status_tracks_port_and_mariadb(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(
            os.environ, {"PHPMYADMIN_DATA_DIR": temp}
        ):
            service = PhpMyAdminService()
            service.htdocs.mkdir(parents=True)
            (service.htdocs / "index.php").write_text("<?php", encoding="utf-8")
            service.php_binary = Mock(return_value="/usr/bin/php8.3")
            service._unit_state = Mock(return_value=(True, "active"))
            service.unit_logs = Mock(return_value="")
            service._port_open = Mock(return_value=False)
            service.mariadb_reachable = Mock(return_value=True)

            status = service.get_status()
            self.assertTrue(status["installed"])
            self.assertFalse(status["running"])
            self.assertEqual(status["state"], "stopped")
            self.assertTrue(status["unit_exists"])

            service._port_open.return_value = True
            service.mariadb_reachable.return_value = False
            status = service.get_status()
            self.assertTrue(status["running"])
            self.assertFalse(status["healthy"])
            self.assertEqual(status["state"], "running")

            service.mariadb_reachable.return_value = True
            status = service.get_status()
            self.assertTrue(status["healthy"])
            self.assertEqual(status["state"], "healthy")
            self.assertIsNone(status["error"])

    def test_failed_unit_surfaces_logs(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(
            os.environ, {"PHPMYADMIN_DATA_DIR": temp}
        ):
            service = PhpMyAdminService()
            service.htdocs.mkdir(parents=True)
            (service.htdocs / "index.php").write_text("<?php", encoding="utf-8")
            service.php_binary = Mock(return_value="/usr/bin/php8.3")
            service._unit_state = Mock(return_value=(True, "failed"))
            service.unit_logs = Mock(return_value="Address already in use")
            service._port_open = Mock(return_value=False)
            service.mariadb_reachable = Mock(return_value=True)

            status = service.get_status()

            self.assertEqual(status["unit_state"], "failed")
            self.assertEqual(status["unit_logs"], "Address already in use")
            self.assertIn("systemd failed", status["error"])

    def test_status_reports_missing_php_binary(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(
            os.environ, {"PHPMYADMIN_DATA_DIR": temp}
        ):
            service = PhpMyAdminService()
            service.htdocs.mkdir(parents=True)
            (service.htdocs / "index.php").write_text("<?php", encoding="utf-8")
            service.php_binary = Mock(return_value=None)

            status = service.get_status()

            self.assertTrue(status["installed"])
            self.assertEqual(status["state"], "error")

    def test_unit_state_parses_systemctl_output(self):
        service = PhpMyAdminService()
        service._run = Mock(
            return_value=subprocess.CompletedProcess([], 0, "active\n", "")
        )
        self.assertEqual(service._unit_state(), (True, "active"))

        service._run.return_value = subprocess.CompletedProcess([], 4, "", "")
        self.assertEqual(service._unit_state(), (False, "unknown"))

    def test_usage_counts_server_process(self):
        service = PhpMyAdminService()
        service.get_status = Mock(
            return_value={
                "installed": True,
                "healthy": True,
                "state": "healthy",
            }
        )
        service._run = Mock(
            return_value=subprocess.CompletedProcess(
                [], 0, "123\n124\n", ""
            )
        )

        usage = service.get_usage()

        self.assertEqual(usage["count"], 2)
        self.assertEqual(usage["status"], "running")

    def test_pause_stops_and_resume_starts_unit(self):
        service = PhpMyAdminService()
        service.is_installed = Mock(return_value=True)
        service._run = Mock(
            return_value=subprocess.CompletedProcess([], 0, "", "")
        )

        service.pause()
        self.assertEqual(
            service._run.call_args.args[0],
            ["systemctl", "stop", service.unit_name],
        )

        service.resume()
        self.assertEqual(
            service._run.call_args.args[0],
            ["systemctl", "start", service.unit_name],
        )

    def test_pause_skips_when_not_installed(self):
        service = PhpMyAdminService()
        service.is_installed = Mock(return_value=False)
        service._run = Mock()

        service.pause()

        service._run.assert_not_called()


class PhpMyAdminPackagingTests(unittest.TestCase):
    def test_manifest_declares_native_php_dependencies_and_route(self):
        plugin = BACKEND / "plugins" / "phpmyadmin"
        manifest = json.loads((plugin / "plugin.json").read_text(encoding="utf-8"))

        self.assertEqual(manifest["id"], "phpmyadmin")
        self.assertEqual(manifest["route_prefix"], "/phpmyadmin")
        self.assertTrue(manifest["sidebar"])
        self.assertEqual(
            sorted(manifest["requires"]["dependencies"]), ["mariadb", "php"]
        )
        self.assertEqual(manifest["usage"], {})

    def test_installer_runs_local_php_server_without_nginx_or_docker(self):
        plugin = BACKEND / "plugins" / "phpmyadmin"
        install = (plugin / "scripts" / "install_phpmyadmin.sh").read_text(
            encoding="utf-8"
        )

        self.assertNotIn("docker", install)
        self.assertIn('-S 127.0.0.1:${PORT}', install)
        self.assertIn("srv-panel-phpmyadmin.service", install)
        self.assertIn("PmaAbsoluteUri", install)
        self.assertIn("CookiePath", install)
        self.assertIn("blowfish_secret", install)
        self.assertIn("sha256sum", install)
        self.assertIn("srv-panel-phpmyadmin.conf", install)  # v1 migration
        self.assertIn("systemctl reload-or-restart", install)
        self.assertIn("8090 8091 8092", install)  # free-port selection
        self.assertIn('chmod 0755 "$DATA_DIR"', install)  # www-data traversal
        self.assertIn("chmod o+x /opt/srv-panel", install)

    def test_uninstaller_removes_unit_and_files(self):
        plugin = BACKEND / "plugins" / "phpmyadmin"
        uninstall = (plugin / "scripts" / "uninstall_phpmyadmin.sh").read_text(
            encoding="utf-8"
        )

        self.assertIn("srv-panel-phpmyadmin.service", uninstall)
        self.assertIn("systemctl disable --now", uninstall)
        self.assertIn("daemon-reload", uninstall)

    def test_template_is_minimal_launcher(self):
        plugin = BACKEND / "plugins" / "phpmyadmin"
        template = (plugin / "templates" / "phpmyadmin.html").read_text(
            encoding="utf-8"
        )
        router = (plugin / "router.py").read_text(encoding="utf-8")

        self.assertIn('prefix="/phpmyadmin"', router)
        self.assertIn("pma-open-btn", template)
        self.assertIn("window.open", template)
        self.assertIn("/phpmyadmin/api/uninstall", template)
        self.assertIn("pma-start-btn", template)
        self.assertIn('@router.post("/api/install")', router)
        self.assertIn('@router.post("/api/start")', router)
        self.assertNotIn("manage_dns", template)


if __name__ == "__main__":
    unittest.main()
