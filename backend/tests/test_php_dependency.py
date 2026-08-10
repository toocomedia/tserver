import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from dependencies.php.service import PHPDependencyService


class PHPDependencyServiceTests(unittest.TestCase):
    def test_status_reports_each_version_without_installing_anything(self):
        service = PHPDependencyService()
        service._available_versions = Mock(return_value=["8.1", "8.3"])
        service._managed_versions = Mock(return_value={"8.3"})
        service._version_status = Mock(side_effect=lambda version, managed: {
            "version": version,
            "installed": version == "8.3",
            "managed": managed,
            "healthy": version == "8.3",
        })

        status = service._probe()

        self.assertEqual(["8.1", "8.3"], [item["version"] for item in status["versions"]])
        self.assertFalse(status["versions"][0]["installed"])
        self.assertTrue(status["versions"][1]["managed"])
        self.assertEqual("panel_managed", status["install_origin"])
        service._version_status.assert_called_with("8.3", True)

    def test_install_version_requires_verified_managed_socket(self):
        service = PHPDependencyService()
        service._helper_call = Mock(return_value={"message": "installed"})
        service.get_status = Mock(return_value={"versions": [{
            "version": "8.3", "managed": True, "healthy": True,
        }]})

        success, message = service.install_version("8.3")

        self.assertTrue(success)
        self.assertEqual("installed", message)
        service._helper_call.assert_called_once_with("install_version", version="8.3")

    def test_uninstall_rejects_an_external_version(self):
        service = PHPDependencyService()
        service.get_status = Mock(return_value={"versions": [{
            "version": "8.1", "installed": True, "managed": False,
        }]})
        service._helper_call = Mock()

        success, message = service.uninstall_version("8.1")

        self.assertFalse(success)
        self.assertIn("outside SRV Panel", message)
        service._helper_call.assert_not_called()

    def test_invalid_version_never_reaches_root_helper(self):
        service = PHPDependencyService()
        service._helper_call = Mock()

        success, message = service.install_version("8.3; rm -rf /")

        self.assertFalse(success)
        self.assertEqual("Invalid PHP version.", message)
        service._helper_call.assert_not_called()

    def test_helper_and_ui_keep_version_lifecycle_allowlisted(self):
        helper = (BACKEND.parent / "scripts" / "php_runtime_helper.py").read_text(encoding="utf-8")
        template = (BACKEND / "templates" / "pages" / "php_dependency_detail.html").read_text(encoding="utf-8")
        install_script = (BACKEND.parent / "scripts" / "install.sh").read_text(encoding="utf-8")
        update_script = (BACKEND.parent / "scripts" / "update.sh").read_text(encoding="utf-8")

        self.assertIn('"install_version": install_version', helper)
        self.assertIn('"uninstall_version": uninstall_version', helper)
        self.assertIn("cannot be adopted automatically", helper)
        self.assertIn("data-php-install", template)
        self.assertIn("data-php-uninstall", template)
        self.assertIn("PHP versions are never installed automatically", template)
        self.assertIn("PHP_RUNTIME_HELPER", install_script)
        self.assertIn("PHP_RUNTIME_HELPER", update_script)


if __name__ == "__main__":
    unittest.main()
