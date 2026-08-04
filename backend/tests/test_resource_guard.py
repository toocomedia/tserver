import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import Base
from models.resource_guard import ResourceGuardPriority, ResourceGuardSettings  # noqa: F401
from services import container_app_usage_service
from services.resource_guard_service import ResourceGuardService


class ResourceGuardTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{Path(self.temp.name) / 'guard.db'}")
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.guard = ResourceGuardService()

    async def asyncTearDown(self):
        await self.engine.dispose()
        self.temp.cleanup()

    async def test_auto_mode_is_enabled_only_below_two_gb(self):
        low = SimpleNamespace(percent=50.0, total=1024 ** 3)
        with patch("services.resource_guard_service.psutil", SimpleNamespace(virtual_memory=lambda: low, swap_memory=lambda: SimpleNamespace(percent=0.0))):
            async with self.sessions() as db:
                status = await self.guard.status(db)
        self.assertTrue(status["enabled"])
        self.assertEqual("normal", status["state"])

    async def test_active_guard_blocks_new_work_when_a_managed_job_exists(self):
        high = SimpleNamespace(percent=91.0, total=1024 ** 3)
        self.guard.register("container_app", "7", "high", "Apps Engine: example.test")
        with patch("services.resource_guard_service.psutil", SimpleNamespace(virtual_memory=lambda: high, swap_memory=lambda: SimpleNamespace(percent=0.0))):
            async with self.sessions() as db:
                with self.assertRaises(RuntimeError):
                    await self.guard.allow_start(db)

    async def test_priority_override_is_saved(self):
        async with self.sessions() as db:
            await self.guard.save_priority(db, "plugin", "sample", "background")
            await db.commit()
        async with self.sessions() as db:
            self.assertEqual("background", await self.guard.priority(db, "plugin", "sample"))

    async def test_limit_validation_rejects_unsafe_values(self):
        async with self.sessions() as db:
            with self.assertRaises(ValueError):
                await self.guard.save_settings(db, "enabled", 96)


class ContainerAppUsageTests(unittest.TestCase):
    def test_docker_memory_parser_and_total_do_not_double_count(self):
        self.assertEqual(128 * 1024 ** 2, container_app_usage_service._memory("128MiB"))
        rows = [
            {"cpu": 1.0, "memory_bytes": 100 * 1024 ** 2, "count": 2},
            {"cpu": 2.0, "memory_bytes": 50 * 1024 ** 2, "count": 1},
        ]
        total = container_app_usage_service._total(rows, 1024 ** 3)
        self.assertEqual(150 * 1024 ** 2, total["memory_bytes"])
        self.assertEqual(3, total["count"])


if __name__ == "__main__":
    unittest.main()
