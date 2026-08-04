"""Focused persistence tests for Resource Guard Slice 2."""
import sys
import tempfile
import unittest
from pathlib import Path

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from database import Base
from models.guard_operation import GuardOperation
from services.resource_guard_operation_service import ResourceGuardOperationService


class GuardOperationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{Path(self.temp.name) / 'guard.db'}")
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.service = ResourceGuardOperationService()

    async def asyncTearDown(self):
        await self.engine.dispose()
        self.temp.cleanup()

    async def test_create_and_finish_operation(self):
        async with self.sessions() as db:
            operation = await self.service.create(
                db, component_type="container_app", component_id="7", operation_type="deploy",
                priority="high", label="Apps Engine: example.com", profile="build_large", status="running",
            )
            await self.service.finish(db, operation.id, "succeeded")
            await db.commit()
        async with self.sessions() as db:
            saved = await db.get(GuardOperation, operation.id)
            self.assertEqual(saved.status, "succeeded")
            self.assertIsNotNone(saved.finished_at)

    async def test_queued_operations_are_numbered_and_cancellable(self):
        async with self.sessions() as db:
            first = await self.service.create(
                db, component_type="container_app", component_id="1", operation_type="deploy",
                priority="high", label="First", profile="build_large", status="queued",
            )
            second = await self.service.create(
                db, component_type="container_app", component_id="2", operation_type="deploy",
                priority="high", label="Second", profile="build_large", status="queued",
            )
            self.assertEqual((first.queue_position, second.queue_position), (1, 2))
            await self.service.cancel(db, first.id)
            await db.commit()
        async with self.sessions() as db:
            saved = await db.get(GuardOperation, second.id)
            self.assertEqual(saved.queue_position, 1)

    async def test_running_operations_survive_new_service_instance(self):
        async with self.sessions() as db:
            operation = await self.service.create(
                db, component_type="container_app", component_id="1", operation_type="deploy",
                priority="high", label="Build", profile="build_large", status="running",
            )
            await db.commit()
        recovered = ResourceGuardOperationService()
        async with self.sessions() as db:
            await recovered.recover(db)
            await db.commit()
            saved = await db.get(GuardOperation, operation.id)
        self.assertEqual(saved.status, "interrupted")

    async def test_monitor_sample_peak_never_decreases(self):
        async with self.sessions() as db:
            operation = await self.service.create(
                db, component_type="container_app", component_id="1", operation_type="deploy",
                priority="high", label="Build", profile="build_large", status="running",
            )
            await self.service.record_sample(db, 1000, 400)
            await self.service.record_sample(db, 1000, 600)
            await db.commit()
        async with self.sessions() as db:
            saved = await db.get(GuardOperation, operation.id)
            self.assertEqual(saved.peak_ram_mb, 600)


if __name__ == "__main__":
    unittest.main(verbosity=2)
