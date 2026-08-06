import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from fastapi import HTTPException
import config
from services import app_hosting_service


class AppPortServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_assigned_port_is_rejected_before_listener_check(self):
        db = AsyncMock()
        db.scalar = AsyncMock(return_value=4)
        with self.assertRaises(HTTPException) as error:
            await app_hosting_service.validate_port(db, config.APP_HOSTING_PORT_START)
        self.assertEqual(409, error.exception.status_code)
        self.assertIn("belongs to another Python app", str(error.exception.detail))

    async def test_free_port_is_checked_against_live_listener(self):
        db = AsyncMock()
        db.scalar = AsyncMock(return_value=None)
        with patch("services.app_ownership_service.require_port_free") as available:
            await app_hosting_service.validate_port(db, config.APP_HOSTING_PORT_START)
        available.assert_called_once_with(config.APP_HOSTING_PORT_START)


if __name__ == "__main__":
    unittest.main()
