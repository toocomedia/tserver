import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from models.hosted_app import HostedApp
from services import app_cleanup_service, app_lifecycle_service


class AppCleanupTests(unittest.IsolatedAsyncioTestCase):
    async def test_cleanup_removes_owned_files_without_database(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "app"
            env = Path(directory) / "app.env"
            root.mkdir()
            (root / "file").write_text("x", encoding="utf-8")
            env.write_text("SECRET=x", encoding="utf-8")
            app = HostedApp(
                id=7, work_dir=str(root), env_path=str(env), service_name="srv-python-7",
                postgres_mode="none",
            )
            with (
                patch("services.app_runtime_service.stop", new=AsyncMock()),
                patch("services.app_runtime_service.systemctl", new=AsyncMock()),
                patch("services.app_runtime_service.service_unit", return_value=Path(directory) / "unit"),
                patch("services.nginx_service.config_exists", return_value=False),
            ):
                await app_cleanup_service.uninstall(app, None, delete_database=False)
            self.assertFalse(root.exists())
            self.assertFalse(env.exists())

    async def test_managed_database_cleanup_uses_only_recorded_names(self):
        app = HostedApp(
            id=8, work_dir="missing", env_path="missing.env", service_name="srv-python-8",
            postgres_mode="create", database_name="app8", database_user="app8",
        )
        with patch("services.app_cleanup_service.pg.drop_app_database_and_user") as drop:
            await app_cleanup_service._drop_database(app)
        drop.assert_called_once_with("app8", "app8")

    async def test_external_database_is_never_dropped(self):
        app = HostedApp(
            id=9, work_dir="missing", env_path="missing.env", service_name="srv-python-9",
            postgres_mode="external", database_name="external", database_user="external",
        )
        with patch("services.app_cleanup_service.pg.drop_app_database_and_user") as drop:
            await app_cleanup_service._drop_database(app)
        drop.assert_not_called()

    async def test_supabase_database_cleanup_uses_recorded_app_resources(self):
        app = HostedApp(
            id=10, work_dir="missing", env_path="missing.env", service_name="srv-python-10",
            postgres_mode="supabase", supabase_project_id=3,
            database_name="app10", database_user="app10",
        )
        database = object()
        with patch(
            "plugins.supabase.service.deprovision_app_database", new=AsyncMock()
        ) as drop:
            await app_cleanup_service._drop_database(app, database)
        drop.assert_awaited_once_with(3, "app10", "app10", database)


class AppLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancelled_deployment_waits_for_task_exit(self):
        started = asyncio.Event()

        async def deployment():
            started.set()
            await asyncio.Event().wait()

        task = asyncio.create_task(deployment())
        await started.wait()
        app_lifecycle_service.register_deployment(90, task)
        await app_lifecycle_service.cancel_deployment(90)
        self.assertTrue(task.cancelled())
        app_lifecycle_service.unregister_deployment(90, task)
