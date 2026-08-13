import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from dependencies.mariadb.service import MariaDBDependencyService


class MariaDBDependencyTests(unittest.TestCase):
    def test_cached_status_never_probes_when_cache_is_empty(self):
        service = MariaDBDependencyService()
        service._probe = Mock(side_effect=AssertionError("cached status must not probe"))

        status = service.get_cached_status()

        self.assertIn(status["state"], {"unknown", "not_installed"})
        self.assertFalse(status["healthy"])
        service._probe.assert_not_called()

    def test_status_is_cached(self):
        service = MariaDBDependencyService()
        service._probe = Mock(return_value={"healthy": True})
        self.assertEqual({"healthy": True}, service.get_status())
        self.assertEqual({"healthy": True}, service.get_status())
        service._probe.assert_called_once()

    def test_stop_verifies_mariadb_is_not_running(self):
        service = MariaDBDependencyService()
        service._run = Mock(return_value=Mock(returncode=0, stdout="", stderr=""))
        with patch("dependencies.mariadb.service.os.name", "posix"), patch.object(
            service, "get_status", return_value={"running": False}
        ):
            success, _ = service.toggle(False)
        self.assertTrue(success)
        self.assertEqual(["systemctl", "disable", "--now", "mariadb"], service._run.call_args.args[0])

    def test_update_check_marks_major_release_blocked(self):
        service = MariaDBDependencyService()
        service._script_path = Mock(return_value=Path("check_mariadb_update.sh"))
        service._run = Mock(return_value=Mock(
            returncode=0,
            stdout="installed=10.11.0\ncandidate=11.4.0\navailable=true\nmajor_change=true\nsource=Configured APT repositories\n",
            stderr="",
        ))
        with patch("dependencies.mariadb.service.os.name", "posix"):
            success, _ = service.check_update()
        self.assertTrue(success)
        update = service.get_cached_update_status()
        self.assertEqual("major_available", update["state"])
        self.assertFalse(update["available"])

    def test_update_check_marks_patch_release_available(self):
        service = MariaDBDependencyService()
        service._script_path = Mock(return_value=Path("check_mariadb_update.sh"))
        service._run = Mock(return_value=Mock(
            returncode=0,
            stdout="installed=10.11.0\ncandidate=10.11.1\navailable=true\nmajor_change=false\nsource=Configured APT repositories\n",
            stderr="",
        ))
        with patch("dependencies.mariadb.service.os.name", "posix"):
            success, _ = service.check_update()
        self.assertTrue(success)
        update = service.get_cached_update_status()
        self.assertEqual("available", update["state"])
        self.assertTrue(update["available"])


if __name__ == "__main__":
    unittest.main()
