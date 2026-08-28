from __future__ import annotations

import unittest
from pathlib import Path
import sys

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from database import Base
from models.container_app import ContainerApp
from models.container_app_snapshot import ContainerAppSnapshot
from plugins.ai_helper.tools.app_spec_setup import propose_app_spec_plan
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from tests.app_spec_fixtures import canonical_app_spec


class AiAppSpecPlanOnlyTests(unittest.IsolatedAsyncioTestCase):
    async def test_proposal_creates_only_action_plan(self):
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
            async with Session() as db:
                result = await propose_app_spec_plan(
                    db,
                    app_spec=canonical_app_spec(),
                    domain_name="app.example.test",
                    evidence=["repository README and compose inspection"],
                    session_id="app_spec_plan",
                    user_id=7,
                )
                self.assertEqual(result["status"], "ok")
                self.assertEqual(await db.scalar(select(func.count(ContainerApp.id))), 0)
                self.assertEqual(await db.scalar(select(func.count(ContainerAppSnapshot.id))), 0)
        finally:
            await engine.dispose()


if __name__ == "__main__":
    unittest.main()
