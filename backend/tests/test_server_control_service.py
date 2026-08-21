import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from services import server_control_service
from utils.shell import ShellResult


class ServerControlServiceTests(unittest.TestCase):
    def test_reboot_queues_a_non_blocking_systemd_job(self):
        async def execute():
            with patch.object(
                server_control_service,
                "run",
                new=AsyncMock(return_value=ShellResult(True, "", "", 0)),
            ) as run:
                await server_control_service.request_reboot()
            run.assert_awaited_once_with(
                ["systemctl", "reboot", "--no-block"], timeout=10
            )

        asyncio.run(execute())

    def test_reboot_reports_systemd_failure(self):
        async def execute():
            with patch.object(
                server_control_service,
                "run",
                new=AsyncMock(return_value=ShellResult(False, "", "permission denied", 1)),
            ):
                with self.assertRaisesRegex(RuntimeError, "permission denied"):
                    await server_control_service.request_reboot()

        asyncio.run(execute())
