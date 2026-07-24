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
    def test_intentionally_disabled_dependency_hides_expected_daemon_error(self):
        manager = DependencyManager()
        service = Mock()
        service.get_status.return_value = {
            "installed": True,
            "running": False,
            "healthy": False,
            "state": "stopped",
            "error": "Cannot connect to the Docker daemon.",
        }
        manager._services["docker"] = service

        with patch(
            "dependencies.manager.component_state_store.get",
            return_value=ComponentStateValue(desired_enabled=False),
        ):
            status = manager.get_status("docker")

        self.assertEqual("disabled", status["effective_state"])
        self.assertIsNone(status["last_error"])

    def test_enabled_dependency_keeps_unexpected_daemon_error(self):
        manager = DependencyManager()
        service = Mock()
        service.get_status.return_value = {
            "installed": True,
            "running": False,
            "healthy": False,
            "state": "stopped",
            "error": "Cannot connect to the Docker daemon.",
        }
        manager._services["docker"] = service

        with patch(
            "dependencies.manager.component_state_store.get",
            return_value=ComponentStateValue(desired_enabled=True),
        ):
            status = manager.get_status("docker")

        self.assertEqual("stopped", status["effective_state"])
        self.assertEqual("Cannot connect to the Docker daemon.", status["last_error"])

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

    async def test_successful_install_is_marked_panel_managed(self):
        manager = DependencyManager()
        service = Mock()
        service.install.return_value = (True, "installed")
        manager._services["docker"] = service
        state_set = AsyncMock()
        with patch(
            "dependencies.manager.component_state_store.get",
            return_value=ComponentStateValue(desired_enabled=True),
        ), patch(
            "dependencies.manager.component_state_store.set", state_set
        ):
            success, _ = await manager.install("docker")

        self.assertTrue(success)
        final = state_set.await_args_list[-1].kwargs
        self.assertEqual("panel_managed", final["install_origin"])
        self.assertTrue(final["desired_enabled"])

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
