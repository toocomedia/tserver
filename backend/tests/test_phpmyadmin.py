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

from plugins.phpmyadmin.service import PhpMyAdminService, PHP_STATE_PATH


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

    def test_site_state_roundtrip_and_delete(self):
        self.assertIsNone(self.service.get_site())

        self.service.save_site(
            {"public_host": "pma.example.com", "ssl_status": "ready"}
        )
        site = self.service.get_site()
        self.assertEqual(site["public_host"], "pma.example.com")
        self.assertEqual(site["ssl_status"], "ready")

        removed = self.service.delete_site()
        self.assertEqual(removed["public_host"], "pma.example.com")
        self.assertIsNone(self.service.get_site())

    def test_update_state_keeps_schema_and_merges(self):
        self.service.save_site({"public_host": "pma.example.com"})
        self.service.update_state(paused=True)

        state = json.loads(self.service.state_path.read_text(encoding="utf-8"))
        self.assertEqual(state["schema_version"], 1)
        self.assertTrue(state["paused"])
        self.assertEqual(state["site"]["public_host"], "pma.example.com")

    def test_public_url_requires_ready_ssl(self):
        self.service.save_site({"public_host": "pma.example.com"})
        self.assertIsNone(self.service.get_public_url())
        self.assertEqual(
            self.service.get_configured_url(), "http://pma.example.com/"
        )

        self.service.save_site(
            {"public_host": "pma.example.com", "ssl_status": "ready"}
        )
        self.assertEqual(
            self.service.get_public_url(), "https://pma.example.com/"
        )
        self.assertEqual(
            self.service.get_configured_url(), "https://pma.example.com/"
        )

    def test_purge_data_requires_uninstalled_app(self):
        self.service.state_path.write_text("{}", encoding="utf-8")
        self.service.secret_path.write_text("s" * 64, encoding="utf-8")
        self.service.marker_path.write_text("1", encoding="utf-8")
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
            with patch(
                "plugins.phpmyadmin.service.PHP_STATE_PATH", state
            ):
                self.assertEqual(self.service.php_version(), "8.3")

    def test_php_version_falls_back_to_installed_directories(self):
        with tempfile.TemporaryDirectory() as temp:
            php_dir = Path(temp) / "php"
            (php_dir / "8.2").mkdir(parents=True)
            (php_dir / "8.3").mkdir(parents=True)
            with patch(
                "plugins.phpmyadmin.service.PHP_STATE_PATH",
                Path(temp) / "missing.json",
            ), patch("plugins.phpmyadmin.service.Path") as mock_path:
                mock_path.return_value = php_dir
                self.assertEqual(self.service.php_version(), "8.3")


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

    @patch("plugins.phpmyadmin.service.PhpMyAdminService.mariadb_reachable")
    def test_status_tracks_socket_and_mariadb(self, mariadb):
        with tempfile.TemporaryDirectory() as temp, patch.dict(
            os.environ, {"PHPMYADMIN_DATA_DIR": temp}
        ):
            service = PhpMyAdminService()
            service.htdocs.mkdir(parents=True)
            (service.htdocs / "index.php").write_text("<?php", encoding="utf-8")
            service.php_version = Mock(return_value="8.3")

            service.socket_path = Mock(return_value=None)
            mariadb.return_value = True
            status = service.get_status()
            self.assertTrue(status["installed"])
            self.assertFalse(status["running"])
            self.assertFalse(status["healthy"])
            self.assertEqual(status["state"], "stopped")

            fake_socket = Path(temp) / "fpm.sock"
            fake_socket.touch()
            service.socket_path = Mock(return_value=fake_socket)
            mariadb.return_value = False
            status = service.get_status()
            self.assertTrue(status["running"])
            self.assertFalse(status["healthy"])
            self.assertEqual(status["state"], "running")

            mariadb.return_value = True
            status = service.get_status()
            self.assertTrue(status["healthy"])
            self.assertEqual(status["state"], "healthy")
            self.assertIsNone(status["error"])

    def test_usage_counts_pool_workers(self):
        service = PhpMyAdminService()
        service.get_status = Mock(
            return_value={
                "installed": True,
                "running": True,
                "healthy": True,
                "state": "healthy",
            }
        )
        service._run = Mock(
            return_value=subprocess.CompletedProcess(
                [], 0, "123\n124\n125\n", ""
            )
        )

        usage = service.get_usage()

        self.assertEqual(usage["count"], 3)
        self.assertEqual(usage["status"], "running")

    @patch("plugins.phpmyadmin.service.PhpMyAdminService._write_live_site")
    @patch("plugins.phpmyadmin.service.PhpMyAdminService._write_offline_site")
    def test_pause_and_resume_swap_site_and_flag(self, offline, live):
        with tempfile.TemporaryDirectory() as temp, patch.dict(
            os.environ, {"PHPMYADMIN_DATA_DIR": temp}
        ):
            service = PhpMyAdminService()
            service.save_site(
                {"public_host": "pma.example.com", "ssl_status": "ready"}
            )

            service.pause()
            offline.assert_called_once_with(
                "pma.example.com",
                service.get_site(),
            )
            self.assertTrue(service.is_paused())

            service.resume()
            live.assert_called_once_with(
                "pma.example.com",
                service.get_site(),
            )
            self.assertFalse(service.is_paused())

    def test_pause_without_site_only_flags_state(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(
            os.environ, {"PHPMYADMIN_DATA_DIR": temp}
        ):
            service = PhpMyAdminService()
            service.pause()
            self.assertTrue(service.read_state().get("paused"))


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
        self.assertNotIn("docker", manifest["requires"]["dependencies"])

    def test_installer_is_native_php_without_docker(self):
        plugin = BACKEND / "plugins" / "phpmyadmin"
        install = (plugin / "scripts" / "install_phpmyadmin.sh").read_text(
            encoding="utf-8"
        )

        self.assertNotIn("docker", install)
        self.assertIn("PHP-FPM", install)
        self.assertIn('FPM_POOL="srv-panel-phpmyadmin"', install)
        self.assertIn('"php${PHP_VERSION}-fpm"', install)
        self.assertIn("config.inc.php", install)
        self.assertIn("blowfish_secret", install)
        self.assertIn("127.0.0.1", install)
        self.assertIn("sha256sum", install)
        self.assertIn('POOL_PATH="${POOL_DIR}/${FPM_POOL}.conf"', install)

    def test_uninstaller_removes_pool_and_nginx_site(self):
        plugin = BACKEND / "plugins" / "phpmyadmin"
        uninstall = (plugin / "scripts" / "uninstall_phpmyadmin.sh").read_text(
            encoding="utf-8"
        )

        self.assertIn("srv-panel-phpmyadmin", uninstall)
        self.assertIn("sites-enabled", uninstall)
        self.assertIn("sites-available", uninstall)
        self.assertIn("reload-or-restart", uninstall)

    def test_template_and_router_use_phpmyadmin_route(self):
        plugin = BACKEND / "plugins" / "phpmyadmin"
        template = (plugin / "templates" / "phpmyadmin.html").read_text(
            encoding="utf-8"
        )
        router = (plugin / "router.py").read_text(encoding="utf-8")

        self.assertIn('prefix="/phpmyadmin"', router)
        self.assertIn("install", template)
        self.assertIn("open-pma-button", template)
        self.assertIn("manage_dns", template)


if __name__ == "__main__":
    unittest.main()
