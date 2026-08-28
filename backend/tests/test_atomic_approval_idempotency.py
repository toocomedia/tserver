from __future__ import annotations

import unittest
from pathlib import Path
import sys

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from database import Base
from plugins.ai_helper.services import action_plans
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


class AtomicApprovalIdempotencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_one_execution_claim_succeeds(self):
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
            async with Session() as db:
                plan = await action_plans.create_action_plan(
                    db, "atomic", "app_spec_install", {"app_spec": {}}, user_id=3,
                )
                await action_plans.begin_plan_execution(
                    db, plan.plan_id, user_id=3, expected_hash=plan.payload_hash,
                    expected_action_types={"app_spec_install"},
                )
                with self.assertRaises(ValueError):
                    await action_plans.begin_plan_execution(
                        db, plan.plan_id, user_id=3, expected_hash=plan.payload_hash,
                        expected_action_types={"app_spec_install"},
                    )
                await action_plans.finish_plan_execution(
                    db, plan.plan_id, user_id=3, expected_hash=plan.payload_hash,
                )
                await db.commit()
                stored = await action_plans.get_action_plan(db, plan.plan_id, user_id=3)
                self.assertEqual(stored["status"], "applied")
        finally:
            await engine.dispose()


if __name__ == "__main__":
    unittest.main()
