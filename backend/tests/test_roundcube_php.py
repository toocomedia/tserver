import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from plugins.roundcube_php.service import RoundcubePhpService
from plugins.manager import PluginManager


class RoundcubePhpLaunchTokenTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.patch = patch.dict(
            os.environ, {"ROUNDCUBE_PHP_DATA_DIR": self.temp.name}
        )
        self.patch.start()
        self.service = RoundcubePhpService()
        self.secret_path = Path(self.temp.name) / "launch.secret"
        self.secret_path.parent.mkdir(parents=True, exist_ok=True)
        self.secret_path.write_bytes(b"0123456789abcdef0123456789abcdef")

    def tearDown(self):
        self.patch.stop()
        self.temp.cleanup()

    def test_roundcube_php_launch_token_generation(self):
        token = self.service.create_launch_token("user@example.com", now=1000)
        self.assertIn(".", token)
        encoded, signature = token.split(".", 1)
        self.assertTrue(len(encoded) > 0)
        self.assertTrue(len(signature) > 0)

    def test_roundcube_php_launch_token_rejects_invalid_emails(self):
        with self.assertRaises(ValueError):
            self.service.create_launch_token("invalid-email")

    def test_roundcube_php_launch_token_rejects_missing_secret(self):
        self.secret_path.unlink()
        with self.assertRaises(RuntimeError):
            self.service.create_launch_token("user@example.com")


class RoundcubePhpStateAndSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.patch = patch.dict(
            os.environ, {"ROUNDCUBE_PHP_DATA_DIR": self.temp.name}
        )
        self.patch.start()
        self.service = RoundcubePhpService()

    def tearDown(self):
        self.patch.stop()
        self.temp.cleanup()

    def test_default_settings(self):
        settings = self.service.get_settings()
        self.assertEqual(settings.get("skin"), "elastic")
        self.assertEqual(settings.get("max_message_size"), "32M")
        self.assertEqual(settings.get("session_lifetime"), 30)
        self.assertIn("srvpanel_launch", settings.get("plugins", []))

    def test_update_settings(self):
        updated = self.service.update_settings(
            skin="larry",
            max_message_size="64M",
            session_lifetime=60,
        )
        self.assertEqual(updated["skin"], "larry")
        self.assertEqual(updated["max_message_size"], "64M")
        self.assertEqual(updated["session_lifetime"], 60)
        # Verify persistence in state.json
        reloaded = self.service.get_settings()
        self.assertEqual(reloaded["skin"], "larry")

    def test_sync_config_file_generates_valid_php(self):
        config_dir = self.service.htdocs / "config"
        config_dir.mkdir(parents=True, exist_ok=True)
        (self.service.htdocs / "skins" / "classic").mkdir(parents=True, exist_ok=True)
        self.service.update_settings(
            skin="classic",
            product_name="Custom Webmail",
            max_message_size="128M",
            session_lifetime=120,
            plugins=["archive", "zipdownload"],
        )
        config_file = config_dir / "config.inc.php"
        self.assertTrue(config_file.is_file())
        content = config_file.read_text(encoding="utf-8")
        self.assertIn("$config['skin'] = 'classic';", content)
        self.assertIn("$config['product_name'] = 'Custom Webmail';", content)
        self.assertIn("$config['max_message_size'] = '128M';", content)
        self.assertIn("$config['session_lifetime'] = 120;", content)
        self.assertIn("'srvpanel_launch'", content)

    def test_site_operations(self):
        self.assertEqual(self.service.get_sites(), {})
        self.service.save_site("example.com", {
            "public_host": "webmail.example.com",
            "ssl_status": "ready",
        })
        sites = self.service.get_sites()
        self.assertIn("example.com", sites)
        self.assertEqual(sites["example.com"]["public_host"], "webmail.example.com")
        self.assertEqual(self.service.get_public_url("example.com"), "https://webmail.example.com/")

        # Delete site
        deleted = self.service.delete_site("example.com")
        self.assertIsNotNone(deleted)
        self.assertEqual(self.service.get_sites(), {})


class RoundcubePhpPackagingTests(unittest.TestCase):
    def test_plugin_manifest_validation(self):
        manager = PluginManager()
        plugin_dir = BACKEND / "plugins" / "roundcube_php"
        self.assertTrue((plugin_dir / "plugin.json").exists())
        manifest = json.loads((plugin_dir / "plugin.json").read_text(encoding="utf-8"))
        err = manager._validate_manifest(manifest, plugin_dir)
        self.assertIsNone(err)

    def test_install_script_contains_php_and_sqlite(self):
        plugin_dir = BACKEND / "plugins" / "roundcube_php"
        install_script = (plugin_dir / "scripts" / "install_roundcube.sh").read_text(encoding="utf-8")
        self.assertIn("ROUNDCUBE_VERSION=", install_script)
        self.assertIn("sqlite.initial.sql", install_script)
        self.assertIn("srv-panel-roundcube-php.service", install_script)

    def test_template_exists_and_is_clean(self):
        plugin_dir = BACKEND / "plugins" / "roundcube_php"
        template_file = plugin_dir / "templates" / "roundcube.html"
        self.assertTrue(template_file.is_file())
        content = template_file.read_text(encoding="utf-8")
        self.assertIn("php-split-layout", content)
        self.assertIn("_rc_domains.html", content)
        partials_dir = plugin_dir / "templates" / "partials"
        self.assertTrue((partials_dir / "_rc_domains.html").is_file())


if __name__ == "__main__":
    unittest.main()
