import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from dependencies.docker.service import DockerDependencyService


class DockerDependencyServiceTests(unittest.TestCase):
    def test_health_result_is_cached(self):
        service = DockerDependencyService()
        service._probe = Mock(return_value={"healthy": True, "state": "healthy"})

        first = service.get_status()
        second = service.get_status()

        self.assertEqual(first, second)
        service._probe.assert_called_once()

    @patch("dependencies.docker.service.shutil.which", return_value="/usr/bin/docker")
    def test_timeout_is_reported_as_unhealthy(self, _which):
        service = DockerDependencyService()
        service._run = Mock(side_effect=subprocess.TimeoutExpired(["docker"], 2))

        status = service.get_status(force=True)

        self.assertFalse(status["healthy"])
        self.assertEqual(status["state"], "stopped")
        self.assertIn("timed out", status["error"])

    @patch("dependencies.docker.service.os.name", "nt")
    def test_service_control_is_not_mocked_on_windows(self):
        service = DockerDependencyService()
        success, message = service.toggle(True)
        self.assertFalse(success)
        self.assertIn("Linux", message)

    def test_owned_cleanup_removes_containers_and_networks_but_preserves_volumes(self):
        service = DockerDependencyService()
        service.get_status = Mock(return_value={"healthy": True})
        service._run = Mock(
            side_effect=[
                subprocess.CompletedProcess([], 0, "c1\nc2\n", ""),
                subprocess.CompletedProcess([], 0, "", ""),
                subprocess.CompletedProcess([], 0, "n1\n", ""),
                subprocess.CompletedProcess([], 0, "", ""),
            ]
        )

        success, message = service.cleanup_plugin_resources("mail_client")

        self.assertTrue(success)
        self.assertIn("volumes preserved", message)
        commands = [call.args[0] for call in service._run.call_args_list]
        self.assertIn(["docker", "rm", "-f", "c1", "c2"], commands)
        self.assertIn(["docker", "network", "rm", "n1"], commands)
        self.assertFalse(any("volume" in command for command in commands))


if __name__ == "__main__":
    unittest.main()
