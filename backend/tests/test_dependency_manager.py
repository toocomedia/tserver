import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from dependencies.manager import DependencyManager
from services.component_state import ComponentStateValue


class _FakeDockerService:
    def __init__(self, result):
        self.result = result

    def toggle(self, enabled):
        return self.result


class DependencyManagerTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_toggle_rolls_operation_back_to_idle(self):
        manager = DependencyManager()
        manager._services["docker"] = _FakeDockerService((False, "systemctl failed"))
        state_set = AsyncMock()
        with patch(
            "dependencies.manager.component_state_store.get",
            return_value=ComponentStateValue(desired_enabled=True),
        ), patch(
            "dependencies.manager.component_state_store.set", state_set
        ):
            success, message = await manager.toggle("docker", False)

        self.assertFalse(success)
        self.assertIn("failed", message)
        self.assertEqual(state_set.await_args_list[-1].kwargs["operation"], "idle")
        self.assertTrue(state_set.await_args_list[-1].kwargs["desired_enabled"])

    async def test_concurrent_toggle_is_rejected(self):
        manager = DependencyManager()
        manager._operation_locks["docker"] = threading.Lock()
        manager._operation_locks["docker"].acquire()
        try:
            success, message = await manager.toggle("docker", True)
        finally:
            manager._operation_locks["docker"].release()
        self.assertFalse(success)
        self.assertIn("already running", message)

    def test_uninstall_precheck_reports_unmanaged_containers(self):
        manager = DependencyManager()
        service = Mock()
        service.list_containers.return_value = [
            {"id": "owned", "name": "owned", "panel_managed": True},
            {"id": "external", "name": "external", "panel_managed": False},
        ]
        manager._services["docker"] = service
        with patch.object(manager, "get_dependent_plugins", return_value=[]):
            result = manager.precheck("docker", "uninstall")
        self.assertEqual(["external"], [item["id"] for item in result["unmanaged_containers"]])


if __name__ == "__main__":
    unittest.main()
