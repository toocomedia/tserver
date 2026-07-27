import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from dependencies.postgresql.service import PostgreSQLDependencyService


class PostgreSQLDependencyTests(unittest.TestCase):
    def test_status_is_cached(self):
        service = PostgreSQLDependencyService()
        service._probe = Mock(return_value={"healthy": True})
        self.assertEqual({"healthy": True}, service.get_status())
        self.assertEqual({"healthy": True}, service.get_status())
        service._probe.assert_called_once()

    def test_stop_verifies_postgresql_is_not_running(self):
        service = PostgreSQLDependencyService()
        service._run = Mock(return_value=Mock(returncode=0, stdout="", stderr=""))
        with patch("dependencies.postgresql.service.os.name", "posix"), patch.object(
            service, "get_status", return_value={"running": False}
        ):
            success, _ = service.toggle(False)
        self.assertTrue(success)
        self.assertEqual(["systemctl", "disable", "--now", "postgresql"], service._run.call_args.args[0])

    def test_start_requires_healthy_postgresql(self):
        service = PostgreSQLDependencyService()
        service._run = Mock(return_value=Mock(returncode=0, stdout="", stderr=""))
        with patch("dependencies.postgresql.service.os.name", "posix"), patch.object(
            service, "get_status", return_value={"healthy": False, "error": "port closed"}
        ):
            success, message = service.toggle(True)
        self.assertFalse(success)
        self.assertEqual("port closed", message)


if __name__ == "__main__":
    unittest.main()
