import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from fastapi import HTTPException
from models.hosted_app import HostedApp
from services import app_ownership_service


class AppOwnershipTests(unittest.TestCase):
    def test_identity_is_immutable_app_id_based(self):
        app = HostedApp(id=7, service_name="srv-python-2", work_dir="old", env_path="old")
        with patch.object(app_ownership_service.config, "APP_HOSTING_ROOT", "/apps"), patch.object(app_ownership_service.config, "APP_HOSTING_ENV_ROOT", "/env"):
            app_ownership_service.apply_identity(app)
        self.assertEqual("srv-python-7", app.service_name)
        self.assertEqual("/apps/7", app.work_dir)
        self.assertEqual("/env/7.env", app.env_path)

    def test_missing_environment_is_blocked_before_systemd(self):
        app = HostedApp(id=7, env_path="missing.env", postgres_mode="external")
        with self.assertRaises(HTTPException) as error:
            app_ownership_service.require_environment(app)
        self.assertEqual(409, error.exception.status_code)
        self.assertIn("Re-save DATABASE_URL", str(error.exception.detail))

    def test_mismatched_unit_is_never_adopted(self):
        app = HostedApp(id=7, service_name="srv-python-7", env_path="/env/7.env")
        with tempfile.TemporaryDirectory() as directory:
            unit = Path(directory) / "srv-python-7.service"
            unit.write_text("Description=SRV Panel Python app 8\nEnvironmentFile=/env/8.env\n", encoding="utf-8")
            with patch("services.app_ownership_service.unit_path", return_value=unit), self.assertRaises(HTTPException):
                app_ownership_service.assert_unit_owner(app)


if __name__ == "__main__":
    unittest.main()
