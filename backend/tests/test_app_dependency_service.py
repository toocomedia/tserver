import sys
import unittest
from pathlib import Path
from unittest.mock import patch

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from fastapi import HTTPException
from models.hosted_app import HostedApp
from services import app_dependency_service
from utils import nginx_templates


class AppDependencyTests(unittest.TestCase):
    def test_only_panel_managed_postgres_requires_manager(self):
        self.assertEqual(["postgres_manager"], app_dependency_service.requirement_ids(HostedApp(postgres_mode="create")))
        self.assertEqual([], app_dependency_service.requirement_ids(HostedApp(postgres_mode="external")))
        self.assertEqual([], app_dependency_service.requirement_ids(HostedApp(postgres_mode="none")))

    def test_runtime_actions_are_blocked_when_postgresql_is_down(self):
        app = HostedApp(postgres_mode="create")
        with patch("plugins.postgres_manager.service.postgres_service.get_status", return_value={"installed": True, "running": False, "port_open": False}):
            with self.assertRaises(HTTPException) as error:
                app_dependency_service.require_available(app)
        self.assertEqual(409, error.exception.status_code)

    def test_offline_template_is_a_non_cached_503_page(self):
        config = nginx_templates.hosted_app_offline_ssl_config("app.example.com", "/cert", "/key")
        self.assertIn("return 503", config)
        self.assertIn('Cache-Control "no-store"', config)
        self.assertIn(".well-known/acme-challenge", config)
        self.assertIn("/_srv-errors/503.html", config)

    def test_proxy_template_uses_static_502_page(self):
        config = nginx_templates.reverse_proxy_config("app.example.com", "127.0.0.1", 9101, "http")
        self.assertIn("proxy_intercept_errors on", config)
        self.assertIn("/_srv-errors/502.html", config)


if __name__ == "__main__":
    unittest.main()
