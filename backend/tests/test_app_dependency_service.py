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
    def test_only_panel_managed_postgres_requires_postgresql(self):
        self.assertEqual(["postgresql"], app_dependency_service.requirement_ids(HostedApp(postgres_mode="create")))
        self.assertEqual([], app_dependency_service.requirement_ids(HostedApp(postgres_mode="external")))
        self.assertEqual([], app_dependency_service.requirement_ids(HostedApp(postgres_mode="none")))

    def test_runtime_actions_are_blocked_when_postgresql_is_down(self):
        app = HostedApp(postgres_mode="create")
        with patch("services.app_dependency_service.dependency_manager.is_healthy", return_value=False):
            with self.assertRaises(HTTPException) as error:
                app_dependency_service.require_available(app)
        self.assertEqual(409, error.exception.status_code)

    def test_offline_template_is_a_non_cached_503_page(self):
        config = nginx_templates.hosted_app_offline_ssl_config("app.example.com", "/cert", "/key")
        self.assertIn("return 503", config)
        self.assertIn('Cache-Control "no-store"', config)
        self.assertIn(".well-known/acme-challenge", config)


if __name__ == "__main__":
    unittest.main()
