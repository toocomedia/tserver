import importlib.util
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
HELPER_PATH = ROOT / "scripts" / "php_runtime_helper.py"

if sys.platform == "win32" and "fcntl" not in sys.modules:
    sys.modules["fcntl"] = types.SimpleNamespace()

SPEC = importlib.util.spec_from_file_location("php_runtime_helper_source_test", HELPER_PATH)
assert SPEC and SPEC.loader
helper = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(helper)


class PHPRuntimeHelperSourceTests(unittest.TestCase):
    def test_list_source_disables_only_resolute_ondrej_entry(self):
        source = (
            "deb https://ppa.launchpadcontent.net/ondrej/php/ubuntu resolute main\n"
            "deb https://ppa.launchpadcontent.net/ondrej/php/ubuntu noble main\n"
            "deb http://archive.ubuntu.com/ubuntu resolute main\n"
        )

        updated, changed = helper._disable_list_suite(source, "resolute")

        self.assertTrue(changed)
        self.assertIn("# deb https://ppa.launchpadcontent.net/ondrej/php/ubuntu resolute main", updated)
        self.assertIn("deb https://ppa.launchpadcontent.net/ondrej/php/ubuntu noble main", updated)
        self.assertIn("deb http://archive.ubuntu.com/ubuntu resolute main", updated)
        self.assertFalse(helper._disable_list_suite(updated, "resolute")[1])

    def test_deb822_source_disables_only_resolute_stanza(self):
        source = (
            "Types: deb\nURIs: https://ppa.launchpadcontent.net/ondrej/php/ubuntu\n"
            "Suites: resolute\nComponents: main\n\n"
            "Types: deb\nURIs: https://ppa.launchpadcontent.net/ondrej/php/ubuntu\n"
            "Suites: noble\nComponents: main\n"
        )

        updated, changed = helper._disable_deb822_suite(source, "resolute")

        self.assertTrue(changed)
        self.assertEqual(updated.count("Enabled: no"), 1)
        self.assertIn("Suites: noble", updated)
        self.assertFalse(helper._disable_deb822_suite(updated, "resolute")[1])

    def test_resolute_cleanup_preserves_source_as_commented_text(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "ondrej.list"
            source.write_text(
                "deb https://ppa.launchpadcontent.net/ondrej/php/ubuntu resolute main\n",
                encoding="utf-8",
            )
            release = {
                "ID": "ubuntu",
                "VERSION_ID": "26.04",
                "VERSION_CODENAME": "resolute",
            }
            with patch.object(helper, "os_release", return_value=release), patch.object(
                helper, "apt_source_files", return_value=[source]
            ):
                changed = helper.disable_unpublished_ppa_suite()

            self.assertEqual(changed, [str(source)])
            self.assertTrue(source.read_text(encoding="utf-8").startswith("# deb "))


if __name__ == "__main__":
    unittest.main()
