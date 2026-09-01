import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from dependencies.php.service import PHPDependencyService


class PHPDependencyServiceTests(unittest.TestCase):
    def test_cached_status_never_probes_when_cache_is_empty(self):
        service = PHPDependencyService()
        service._probe = Mock(side_effect=AssertionError("cached status must not probe"))

        status = service.get_cached_status()

        self.assertEqual("unknown", status["state"])
        self.assertFalse(status["healthy"])
        service._probe.assert_not_called()

    def test_forced_status_warms_cached_status(self):
        service = PHPDependencyService()
        snapshot = {"state": "healthy", "healthy": True}
        service._probe = Mock(return_value=snapshot)
        expected = {**snapshot, "operation_in_progress": False}

        self.assertEqual(expected, service.get_status(force=True))
        self.assertEqual(expected, service.get_cached_status())
        service._probe.assert_called_once()

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

    def test_second_runtime_operation_is_rejected(self):
        service = PHPDependencyService()
        service._helper_call = Mock()
        service._operation_lock.acquire()
        try:
            success, message = service.install_version("8.3")
        finally:
            service._operation_lock.release()

        self.assertFalse(success)
        self.assertEqual("Another PHP runtime operation is already running.", message)
        service._helper_call.assert_not_called()

    def test_toggle_disables_all_managed_php_versions(self):
        service = PHPDependencyService()
        service._helper_call = Mock(return_value={"message": "PHP disabled"})
        service.get_status = Mock(return_value={"healthy": False, "running": False})

        success, message = service.toggle(False)

        self.assertTrue(success)
        self.assertEqual("PHP disabled", message)
        service._helper_call.assert_called_once_with(
            "set_all_enabled", enabled=False, timeout=180,
        )

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

    def test_available_versions_uses_one_policy_call_for_all_php_packages(self):
        service = PHPDependencyService()
        service._run = Mock(side_effect=[
            Mock(returncode=0, stdout="php7.4-fpm\nphp8.3-fpm\nphp8.4-cli\n"),
            Mock(returncode=0, stdout=(
                "php7.4-fpm:\n  Candidate: 7.4.33\n"
                "php8.3-fpm:\n  Candidate: 8.3.30\n"
            )),
        ])

        with patch("dependencies.php.service.os.name", "posix"):
            versions = service._available_versions()

        self.assertEqual(["7.4", "8.3"], versions)
        self.assertEqual(2, service._run.call_count)
        self.assertEqual(
            ["apt-cache", "policy", "php7.4-fpm", "php8.3-fpm"],
            service._run.call_args_list[1].args[0],
        )

    def test_external_repository_is_a_fixed_explicit_helper_operation(self):
        service = PHPDependencyService()
        service._helper_call = Mock(return_value={"message": "repository enabled"})

        with patch(
            "dependencies.php.service.platform_support_service.capability_error",
            return_value=None,
        ):
            success, message = service.enable_external_repository()

        self.assertTrue(success)
        self.assertEqual("repository enabled", message)
        service._helper_call.assert_called_once_with("enable_external_repository", timeout=600)

    def test_external_repository_is_rejected_when_platform_is_not_ubuntu(self):
        service = PHPDependencyService()
        service._helper_call = Mock()
        reason = "Php External Repository is not supported on Debian 13."

        with patch(
            "dependencies.php.service.platform_support_service.capability_error",
            return_value=reason,
        ):
            success, message = service.enable_external_repository()

        self.assertFalse(success)
        self.assertEqual(reason, message)
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
        page_template = (BACKEND / "templates" / "pages" / "php_dependency_detail.html").read_text(encoding="utf-8")
        runtime_template = (BACKEND / "templates" / "pages" / "partials" / "php_dependency_runtime.html").read_text(encoding="utf-8")
        router = (BACKEND / "routers" / "dependencies.py").read_text(encoding="utf-8")
        install_script = (BACKEND.parent / "scripts" / "install.sh").read_text(encoding="utf-8")
        update_script = (BACKEND.parent / "scripts" / "update.sh").read_text(encoding="utf-8")

        self.assertIn('"install_version": install_version', helper)
        self.assertIn('"check_available": check_available', helper)
        self.assertIn('"enable_external_repository": enable_external_repository', helper)
        self.assertIn('EXTERNAL_REPOSITORY_PPA = "ppa:ondrej/php"', helper)
        self.assertIn('PPA_SUPPORTED_UBUNTU_CODENAMES = frozenset({"jammy", "noble"})', helper)
        self.assertIn("disable_unpublished_ppa_suite()", helper)
        self.assertIn("require_supported_ppa_platform()", helper)
        self.assertIn('"uninstall_version": uninstall_version', helper)
        self.assertIn('"set_all_enabled": set_all_enabled', helper)
        self.assertIn("fcntl.LOCK_EX | fcntl.LOCK_NB", helper)
        self.assertIn("Another PHP runtime operation is already running.", helper)
        self.assertIn("cannot be adopted automatically", helper)
        self.assertIn('SITE_EXTENSION_NAMES = ("curl", "gd", "intl", "mbstring", "mysql", "xml", "zip", "opcache")', helper)
        self.assertIn("verify_fpm_extensions(item_version, SITE_EXTENSION_NAMES)", helper)
        self.assertIn('"--no-install-recommends", *packages', helper)
        self.assertIn("did not load required extensions", helper)
        self.assertIn("php-runtime-skeleton", page_template)
        self.assertIn("/api/dependencies/php/runtime-view", page_template)
        self.assertIn("data-php-install", runtime_template)
        self.assertIn("data-php-check", runtime_template)
        self.assertIn("data-php-external-repository", runtime_template)
        self.assertIn("data-php-uninstall", runtime_template)
        self.assertIn("data-php-toggle", runtime_template)
        self.assertIn("DISABLE ALL PHP", runtime_template)
        self.assertIn("external_php_source_enabled", runtime_template)
        self.assertIn("/api/dependencies/php/enable-external-repository", router)
        self.assertIn("/api/dependencies/php/toggle", router)
        self.assertIn("/api/dependencies/php/runtime-view", router)
        self.assertIn("install_pecl_extension", helper)
        self.assertIn("uninstall_pecl_extension", helper)
        self.assertIn("packages.sury.org/php", helper)
        self.assertIn("PPA_SUPPORTED_DEBIAN_CODENAMES", helper)
        self.assertIn("data-custom-pecl-submit", runtime_template)
        self.assertIn("getCsrfToken()", page_template)
        self.assertIn("X-CSRF-Token", page_template)
        self.assertIn("/api/dependencies/php/versions/{version}/extensions/{extension}/install-pecl", router)
        self.assertIn("PHP_TOOLS_HELPER", install_script)
        self.assertIn("PHP_TOOLS_HELPER", update_script)
        self.assertIn("$PHP_TOOLS_HELPER$PLUGIN_SUDOERS_COMMANDS", install_script)
        self.assertIn("$PHP_TOOLS_HELPER$PLUGIN_SUDOERS_COMMANDS", update_script)


class PHPToolsServiceTests(unittest.TestCase):
    def test_tools_status_returns_composer_and_wp(self):
        from dependencies.php.tools_service import php_tools_service
        tools = php_tools_service.get_tools_status()
        tool_ids = {t["id"] for t in tools}
        self.assertIn("composer", tool_ids)
        self.assertIn("wp", tool_ids)

    def test_tools_install_and_uninstall(self):
        from dependencies.php.tools_service import php_tools_service
        with patch.object(php_tools_service, "_call", return_value={"message": "Composer installed", "tool": {"installed": True}}):
            success, msg, info = php_tools_service.install_tool("composer")
            self.assertTrue(success)
            self.assertEqual("Composer installed", msg)


class PHPExtensionServiceTests(unittest.TestCase):
    def test_list_extensions_validates_version(self):
        from dependencies.php.extension_service import php_extension_service
        with self.assertRaises(ValueError):
            php_extension_service.list_extensions("invalid_ver; rm -rf /")

    def test_list_extensions_returns_catalog(self):
        from dependencies.php.extension_service import php_extension_service
        res = php_extension_service.list_extensions("8.3")
        self.assertEqual("8.3", res["version"])
        ext_names = {e["name"] for e in res["extensions"]}
        self.assertIn("curl", ext_names)
        self.assertIn("mysql", ext_names)
        self.assertIn("redis", ext_names)

    def test_search_extensions(self):
        from dependencies.php.extension_service import php_extension_service
        with patch.object(php_extension_service, "_call", return_value={"version": "8.3", "results": [{"name": "swoole", "package": "php8.3-swoole"}]}):
            res = php_extension_service.search_extensions("8.3", "swoole")
            self.assertEqual(1, len(res["results"]))
            self.assertEqual("swoole", res["results"][0]["name"])

    def test_pecl_extension_install_and_uninstall(self):
        from dependencies.php.extension_service import php_extension_service
        with patch.object(php_extension_service, "_call", return_value={"message": "PECL extension yaml compiled"}):
            success, msg = php_extension_service.install_pecl_extension("8.3", "yaml")
            self.assertTrue(success)
            self.assertIn("yaml", msg)

        with patch.object(php_extension_service, "_call", return_value={"message": "PECL extension yaml uninstalled"}):
            success, msg = php_extension_service.uninstall_pecl_extension("8.3", "yaml")
            self.assertTrue(success)
            self.assertIn("yaml", msg)


if __name__ == "__main__":
    unittest.main()

