import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException

from models.hosted_app import HostedApp
from services import app_environment_service


class AppEnvironmentValuesTests(unittest.IsolatedAsyncioTestCase):
    async def test_saves_only_non_empty_detected_values(self):
        with tempfile.TemporaryDirectory() as directory:
            app = HostedApp(id=1, env_path=str(Path(directory) / "app.env"))
            db = MagicMock()
            with patch.object(app_environment_service, "keys", new=AsyncMock(return_value=[])):
                await app_environment_service.set_values(
                    db, app, {"SECRET_KEY": "secret", "OPTIONAL_VALUE": ""}
                )
            self.assertEqual(Path(app.env_path).read_text(), "SECRET_KEY=secret\n")
            self.assertEqual(db.add.call_count, 1)

    async def test_rejects_panel_managed_database_url(self):
        app = HostedApp(id=1, env_path="unused")
        with self.assertRaises(HTTPException):
            await app_environment_service.set_values(MagicMock(), app, {"DATABASE_URL": "x"})
