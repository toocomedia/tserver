import json
import sys
import unittest
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from plugins.rspamd.service import RspamdService


class RspamdPluginTests(unittest.TestCase):
    def setUp(self):
        self.plugin_dir = BACKEND / "plugins" / "rspamd"

    def test_manifest_structure(self):
        manifest_path = self.plugin_dir / "plugin.json"
        self.assertTrue(manifest_path.exists(), "plugin.json must exist")
        
        with open(manifest_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        self.assertEqual(data.get("id"), "rspamd")
        self.assertEqual(data.get("route_prefix"), "/plugins/rspamd")
        self.assertTrue(data.get("sidebar"))
        self.assertIn("process_names", data.get("usage", {}))
        self.assertIn("rspamd", data["usage"]["process_names"])

    def test_service_initialization_and_usage(self):
        service = RspamdService()
        status = service.get_status()
        self.assertIn("installed", status)
        self.assertIn("running", status)
        self.assertIn("maddy_integrated", status)

        usage = service.get_usage_details()
        self.assertIn("details", usage)

    def test_installer_and_uninstaller_scripts_exist(self):
        installer = self.plugin_dir / "scripts" / "install_rspamd.sh"
        uninstaller = self.plugin_dir / "scripts" / "uninstall_rspamd.sh"
        manage = self.plugin_dir / "scripts" / "manage_rspamd.py"

        self.assertTrue(installer.exists())
        self.assertTrue(uninstaller.exists())
        self.assertTrue(manage.exists())

        install_text = installer.read_text(encoding="utf-8")
        self.assertIn("apt-get install -y -qq rspamd redis-server", install_text)
        self.assertIn("maxmemory 32mb", install_text)
        self.assertIn("rspamd http://127.0.0.1:11333", install_text)

        uninstall_text = uninstaller.read_text(encoding="utf-8")
        self.assertIn("apt-get purge -y -qq rspamd", uninstall_text)

    def test_manage_script_commands(self):
        manage = (self.plugin_dir / "scripts" / "manage_rspamd.py").read_text(encoding="utf-8")
        self.assertIn("service-control", manage)
        self.assertIn("update-thresholds", manage)
        self.assertIn("sync-maddy", manage)


if __name__ == "__main__":
    unittest.main()
