"""Comprehensive test suite for PHP Websites backend, schemas, service logic, and HTML pages."""
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

# Mock Linux-only pwd module if running on Windows
if sys.platform == "win32":
    import types
    mock_pwd = types.ModuleType("pwd")
    mock_pwd.getpwnam = Mock(return_value=Mock(pw_uid=1000, pw_gid=1000))
    sys.modules["pwd"] = mock_pwd

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from schemas.php_sites import (
    Confirmation,
    ControlRequest,
    DatabaseCreate,
    DocumentRootChange,
    RuntimeChange,
    SiteCreate,
    WordPressRetry,
    WordPressSetup,
    validate_document_root,
    validate_php_version,
)


class PHPWebsitesSchemaTests(unittest.TestCase):
    """Test Pydantic schema validation for PHP Website API request bodies."""

    def test_document_root_validation(self):
        self.assertEqual("public", validate_document_root("public"))
        self.assertEqual("web/public", validate_document_root("/web/public/"))
        with self.assertRaises(ValueError):
            validate_document_root("../outside")
        with self.assertRaises(ValueError):
            validate_document_root("")

    def test_php_version_validation(self):
        self.assertEqual("8.3", validate_php_version("8.3"))
        with self.assertRaises(ValueError):
            validate_php_version("8.3.1")
        with self.assertRaises(ValueError):
            validate_php_version("php83")

    def test_wordpress_setup_schema(self):
        wp = WordPressSetup(
            site_title="Test Site",
            admin_user="wpadmin",
            admin_email="admin@example.com",
            admin_password="a-very-long-secure-password-123",
        )
        self.assertEqual("wpadmin", wp.admin_user)
        self.assertEqual("admin@example.com", wp.admin_email)

        # Invalid password length (<12 chars)
        with self.assertRaises(Exception):
            WordPressSetup(
                site_title="Test Site",
                admin_user="wpadmin",
                admin_email="admin@example.com",
                admin_password="short",
            )

        # Invalid email format (missing @)
        with self.assertRaises(Exception):
            WordPressSetup(
                site_title="Test Site",
                admin_user="wpadmin",
                admin_email="invalidemail",
                admin_password="a-very-long-secure-password-123",
            )

    def test_site_create_preset_validation(self):
        # Plain PHP preset
        site = SiteCreate(
            domain_id=1,
            preset="php",
            php_version="8.3",
            document_root="public",
            create_database=True,
            ssl=True,
        )
        self.assertEqual("php", site.preset)
        self.assertTrue(site.create_database)

        # WordPress preset requires wordpress administrator details
        with self.assertRaises(Exception):
            SiteCreate(
                domain_id=1,
                preset="wordpress",
                php_version="8.3",
                document_root="public",
                wordpress=None,
            )

        laravel = SiteCreate(
            domain_id=1,
            preset="laravel",
            php_version="8.3",
            document_root="public",
        )
        self.assertEqual("laravel", laravel.preset)
        with self.assertRaises(Exception):
            SiteCreate(
                domain_id=1,
                preset="laravel",
                php_version="8.3",
                document_root="web",
            )
        with self.assertRaises(Exception):
            SiteCreate(
                domain_id=1,
                preset="laravel",
                php_version="8.2",
                document_root="public",
            )

    def test_control_request_action_validation(self):
        ctrl = ControlRequest(action="enable")
        self.assertEqual("enable", ctrl.action)
        with self.assertRaises(Exception):
            ControlRequest(action="invalid_action")


class PHPWebsitesServiceTests(unittest.IsolatedAsyncioTestCase):
    """Test business logic and dependency checks for PHP Websites."""

    @patch("dependencies.dependency_manager.get_status")
    def test_selectable_versions_filters_healthy_managed_runtimes(self, mock_get_status):
        from services.php_site_service import selectable_versions
        mock_get_status.return_value = {
            "versions": [
                {"version": "8.1", "installed": True, "managed": True, "healthy": True},
                {"version": "8.2", "installed": True, "managed": False, "healthy": True},
                {"version": "8.3", "installed": True, "managed": True, "healthy": False},
            ]
        }
        versions = selectable_versions()
        self.assertEqual(1, len(versions))
        self.assertEqual("8.1", versions[0]["version"])

    @patch("dependencies.dependency_manager.get_status")
    def test_require_mariadb_checks_origin(self, mock_get_status):
        from services.php_site_service import require_mariadb
        from fastapi import HTTPException

        mock_get_status.return_value = {"healthy": True, "install_origin": "external"}
        with self.assertRaises(HTTPException) as ctx:
            require_mariadb()
        self.assertEqual(409, ctx.exception.status_code)
        self.assertIn("panel-managed local MariaDB", str(ctx.exception.detail))

    @patch("services.php_site_service.selectable_versions", return_value=[{"version": "8.3"}])
    @patch("dependencies.dependency_manager.get_status", return_value={"healthy": True, "install_origin": "panel_managed"})
    async def test_service_options_returns_dict(self, mock_get_status, mock_vers):
        from services.php_site_service import options
        db = AsyncMock()
        mock_scalars = Mock()
        mock_scalars.all.return_value = []
        db.scalars.return_value = mock_scalars
        res = await options(db)
        self.assertIn("domains", res)
        self.assertIn("php_versions", res)
        self.assertIn("wordpress", res)
        self.assertIn("laravel", res)
        self.assertIn("mariadb", res)


class PHPWebsitesAPIRoutesTests(unittest.TestCase):
    """Test HTML page routes and conditional visibility logic."""

    @patch("dependencies.dependency_manager.is_healthy", return_value=False)
    def test_php_page_redirects_when_dependency_inactive(self, mock_healthy):
        from routers.php_sites import _php_page_redirect

        response = _php_page_redirect()
        self.assertIsNotNone(response)
        self.assertEqual(303, response.status_code)
        self.assertEqual("/dependencies", response.headers["location"])

    @patch("dependencies.dependency_manager.is_healthy", return_value=True)
    def test_php_page_has_no_redirect_when_dependency_active(self, mock_healthy):
        from routers.php_sites import _php_page_redirect

        self.assertIsNone(_php_page_redirect())

    @patch("dependencies.dependency_manager.is_healthy", return_value=True)
    def test_templating_is_php_active_returns_true(self, mock_healthy):
        from templating import is_php_active
        self.assertTrue(is_php_active())

    @patch("dependencies.dependency_manager.is_healthy", return_value=False)
    def test_templating_is_php_active_returns_false(self, mock_healthy):
        from templating import is_php_active
        self.assertFalse(is_php_active())


if __name__ == "__main__":
    unittest.main()
