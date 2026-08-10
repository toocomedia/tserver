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
        service._external_repository_configured = Mock(return_value=False)
        service._version_status = Mock(side_effect=lambda version, managed, available: {
            "version": version,
            "installed": version == "8.3",
            "managed": managed,
            "healthy": version == "8.3",
        })

        status = service._probe()

        self.assertEqual(["7.4", "8.0", "8.1", "8.2", "8.3", "8.4", "8.5"], [item["version"] for item in status["versions"]])
        self.assertFalse(status["versions"][2]["installed"])
        self.assertTrue(status["versions"][4]["managed"])
        self.assertEqual("panel_managed", status["install_origin"])
        self.assertFalse(status["external_repository"]["configured"])
        self.assertEqual("ppa:ondrej/php", status["external_repository"]["ppa"])
        service._version_status.assert_called_with("8.5", False, False)

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

    def test_availability_check_requires_an_explicit_helper_operation(self):
        service = PHPDependencyService()
        service._helper_call = Mock(return_value={"message": "availability refreshed"})

        success, message = service.check_available_versions()

        self.assertTrue(success)
        self.assertEqual("availability refreshed", message)
        service._helper_call.assert_called_once_with("check_available", timeout=300)

    def test_external_repository_is_a_fixed_explicit_helper_operation(self):
        service = PHPDependencyService()
        service._helper_call = Mock(return_value={"message": "repository enabled"})

        success, message = service.enable_external_repository()

        self.assertTrue(success)
        self.assertEqual("repository enabled", message)
        service._helper_call.assert_called_once_with("enable_external_repository", timeout=600)

    def test_invalid_version_never_reaches_root_helper(self):
        service = PHPDependencyService()
        service._helper_call = Mock()

        success, message = service.install_version("8.3; rm -rf /")

        self.assertFalse(success)
        self.assertEqual("Invalid PHP version.", message)
        service._helper_call.assert_not_called()

    def test_helper_and_ui_keep_version_lifecycle_allowlisted(self):
        helper = (BACKEND.parent / "scripts" / "php_runtime_helper.py").read_text(encoding="utf-8")
        page_template = (BACKEND / "templates" / "pages" / "php_dependency_detail.html").read_text(encoding="utf-8")
        runtime_template = (BACKEND / "templates" / "pages" / "partials" / "php_dependency_runtime.html").read_text(encoding="utf-8")
        router = (BACKEND / "routers" / "dependencies.py").read_text(encoding="utf-8")
        install_script = (BACKEND.parent / "scripts" / "install.sh").read_text(encoding="utf-8")
        update_script = (BACKEND.parent / "scripts" / "update.sh").read_text(encoding="utf-8")

        self.assertIn('"install_version": install_version', helper)
        self.assertIn('"check_available": check_available', helper)
        self.assertIn('"enable_external_repository": enable_external_repository', helper)
        self.assertIn('EXTERNAL_REPOSITORY_PPA = "ppa:ondrej/php"', helper)
        self.assertIn('"uninstall_version": uninstall_version', helper)
        self.assertIn("cannot be adopted automatically", helper)
        self.assertIn("php-runtime-skeleton", page_template)
        self.assertIn("/api/dependencies/php/runtime-view", page_template)
        self.assertIn("data-php-install", runtime_template)
        self.assertIn("data-php-check", runtime_template)
        self.assertIn("data-php-external-repository", runtime_template)
        self.assertIn("data-php-uninstall", runtime_template)
        self.assertIn("External PHP source enabled", runtime_template)
        self.assertIn("/api/dependencies/php/enable-external-repository", router)
        self.assertIn("/api/dependencies/php/runtime-view", router)
        self.assertIn("PHP_RUNTIME_HELPER", install_script)
        self.assertIn("PHP_RUNTIME_HELPER", update_script)


if __name__ == "__main__":
    unittest.main()
