import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from models.hosted_app import HostedApp
from services import app_release_service, app_update_service


class GitUpdateStateTests(unittest.TestCase):
    def test_update_ready_compares_revisions(self):
        app = HostedApp(deployed_revision="old", available_revision="new")
        self.assertTrue(app_update_service.has_update(app))
        app.available_revision = "old"
        self.assertFalse(app_update_service.has_update(app))


class ReleaseRollbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_listener_switches_back_to_previous_release(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old, new = root / "releases" / "10", root / "releases" / "11"
            for release in (old, new):
                (release / "source").mkdir(parents=True)
                (release / ".venv").mkdir()
            app = HostedApp(
                work_dir=str(root),
                active_release="10",
                status="running",
                port=9100,
                service_name="srv-python-test",
            )
            prepared = app_release_service.PreparedRelease("11", new, "newsha")
            switches = []
            health = AsyncMock(side_effect=[HTTPException(400, "failed"), None])
            with (
                patch.object(app_release_service, "_switch_current", side_effect=lambda _a, p: switches.append(p)),
                patch("services.app_runtime_service.stop", new=AsyncMock()),
                patch("services.app_runtime_service.prepare_environment", new=AsyncMock()),
                patch("services.app_runtime_service.snapshot_environment", return_value=None),
                patch("services.app_runtime_service.restore_environment"),
                patch("services.app_runtime_service.install_unit", new=AsyncMock()),
                patch("services.app_runtime_service.start", new=AsyncMock()),
                patch("services.app_hosting_health_service.wait_for_listener", new=health),
            ):
                with self.assertRaises(app_release_service.ReleaseFailure) as raised:
                    await app_release_service.cutover(app, prepared)
            self.assertEqual(raised.exception.rollback_status, "succeeded")
            self.assertEqual(switches, [new, old])

    async def test_cancelled_update_restores_but_does_not_restart_old_app(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old, new = root / "releases" / "10", root / "releases" / "11"
            for release in (old, new):
                (release / "source").mkdir(parents=True)
                (release / ".venv").mkdir()
            app = HostedApp(
                work_dir=str(root),
                active_release="10",
                status="running",
                port=9100,
                service_name="srv-python-test",
            )
            prepared = app_release_service.PreparedRelease("11", new, "newsha")
            started = AsyncMock()

            async def reporter(stage, _message):
                if stage == "rollback":
                    raise HTTPException(409, "Deployment was stopped by the user.")

            with (
                patch.object(app_release_service, "_switch_current"),
                patch("services.app_runtime_service.stop", new=AsyncMock()),
                patch("services.app_runtime_service.prepare_environment", new=AsyncMock()),
                patch("services.app_runtime_service.snapshot_environment", return_value=None),
                patch("services.app_runtime_service.restore_environment"),
                patch("services.app_runtime_service.install_unit", new=AsyncMock()),
                patch("services.app_runtime_service.start", new=started),
                patch(
                    "services.app_hosting_health_service.wait_for_listener",
                    new=AsyncMock(side_effect=HTTPException(400, "failed")),
                ),
            ):
                with self.assertRaises(app_release_service.ReleaseFailure):
                    await app_release_service.cutover(app, prepared, reporter)
            self.assertEqual(started.await_count, 1)
