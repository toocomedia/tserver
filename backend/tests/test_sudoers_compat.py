import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class SudoersCompatibilityTests(unittest.TestCase):
    def test_bundled_plugin_lifecycle_scripts_are_exactly_allowlisted(self):
        helper = (ROOT / "scripts" / "sudoers_compat.sh").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("plugins/*", helper)

        for manifest_path in (ROOT / "backend" / "plugins").glob("*/plugin.json"):
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            for field in ("install_script", "uninstall_script"):
                script = manifest.get(field)
                if script:
                    expected = f'{manifest_path.parent.name}/{script}'
                    self.assertIn(expected, helper)

    def test_install_and_update_write_compatible_sudoers_atomically(self):
        for name in ("install.sh", "update.sh"):
            with self.subTest(script=name):
                content = (ROOT / "scripts" / name).read_text(encoding="utf-8")
                self.assertNotIn("!requiretty", content)
                self.assertNotIn("app/plugins/*", content)
                self.assertIn("srv_sudoers_plugin_commands", content)
                self.assertIn("mktemp /etc/sudoers.d/.srv-panel.", content)
                self.assertIn('mv -f "$SUDOERS_TEMP" "$SUDOERS_FILE"', content)


if __name__ == "__main__":
    unittest.main()
