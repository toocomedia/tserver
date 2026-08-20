import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class PluginUnverifiedInstallUITests(unittest.TestCase):
    def test_confirmation_bypasses_global_double_submit_guard(self):
        source = (ROOT / "backend" / "static" / "js" / "modules" / "plugin-install.js").read_text(
            encoding="utf-8"
        )

        self.assertIn("HTMLFormElement.prototype.submit.call(form)", source)
        self.assertNotIn("form.requestSubmit()", source)
        self.assertIn("form.removeAttribute('data-submitting')", source)


if __name__ == "__main__":
    unittest.main()
