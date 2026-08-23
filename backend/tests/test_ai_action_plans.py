"""
test_ai_action_plans.py — Unit tests for immutable AI Action Plans, hashing, TTL, and replay protection.
"""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import unittest

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from database import AsyncSessionLocal, init_db
from models.ai_helper import AiActionEvent, AiActionPlan
from plugins.ai_helper.services import action_plans
from sqlalchemy import select


class TestAiActionPlans(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await init_db()

    async def test_create_action_plan_and_hash(self):
        """Verify action plan creation calculates SHA-256 payload hash and records audit event."""
        payload = {
            "source_type": "image",
            "image_reference": "ghost:5-alpine",
            "internal_port": 2368,
            "environment_values": {"NODE_ENV": "production"},
        }
        async with AsyncSessionLocal() as db:
            plan = await action_plans.create_action_plan(
                db=db,
                session_id="test_sess_001",
                action_type="app_install",
                payload=payload,
                summary="Deploy Ghost CMS",
                confidence=0.95,
                reasoning="Standard Ghost alpine container",
            )
            self.assertTrue(plan.plan_id.startswith("plan_"))
            self.assertEqual(plan.status, "awaiting_approval")
            self.assertEqual(plan.summary, "Deploy Ghost CMS")
            self.assertEqual(plan.confidence, 0.95)
            self.assertTrue(len(plan.payload_hash) == 64)

            # Query plan back
            retrieved = await action_plans.get_action_plan(db, plan.plan_id)
            self.assertIsNotNone(retrieved)
            self.assertEqual(retrieved["status"], "awaiting_approval")
            self.assertEqual(retrieved["payload"]["internal_port"], 2368)
            self.assertEqual(retrieved["payload_hash"], plan.payload_hash)

            # Verify audit event
            stmt = select(AiActionEvent).where(AiActionEvent.plan_id == plan.plan_id)
            events = (await db.execute(stmt)).scalars().all()
            self.assertTrue(any(e.event_type == "created" for e in events))

    async def test_mark_plan_applied_and_replay_protection(self):
        """Verify that a plan can be marked as applied once, and subsequent attempts are rejected."""
        payload = {"source_type": "git", "repository_url": "https://github.com/example/app"}
        async with AsyncSessionLocal() as db:
            plan = await action_plans.create_action_plan(
                db=db,
                session_id="test_sess_002",
                action_type="app_install",
                payload=payload,
            )

            # First apply succeeds
            res = await action_plans.mark_plan_applied(db, plan.plan_id, user_id=1)
            self.assertEqual(res["status"], "ok")
            self.assertEqual(res["plan_status"], "applied")

            # Second apply raises ValueError (replay protection)
            with self.assertRaises(ValueError) as ctx:
                await action_plans.mark_plan_applied(db, plan.plan_id, user_id=1)
            self.assertIn("replay prevented", str(ctx.exception).lower())

    async def test_action_plan_ttl_expiration(self):
        """Verify that expired action plans transition to 'expired' and cannot be applied."""
        payload = {"test": 123}
        async with AsyncSessionLocal() as db:
            # Create plan with -5 minute TTL (already expired)
            plan = await action_plans.create_action_plan(
                db=db,
                session_id="test_sess_003",
                action_type="app_install",
                payload=payload,
                ttl_minutes=-5,
            )

            # get_action_plan should detect expiration
            retrieved = await action_plans.get_action_plan(db, plan.plan_id)
            self.assertIsNotNone(retrieved)
            self.assertEqual(retrieved["status"], "expired")
            self.assertTrue(retrieved["is_expired"])

            # Applying expired plan must fail
            with self.assertRaises(ValueError) as ctx:
                await action_plans.mark_plan_applied(db, plan.plan_id)
            self.assertIn("expired", str(ctx.exception).lower())

    async def test_owned_plan_rejects_foreign_user_and_tampered_payload(self):
        payload = {"app_id": 7, "base_configuration_revision": 1, "patch": {"health_path": "/health"}}
        async with AsyncSessionLocal() as db:
            plan = await action_plans.create_action_plan(
                db=db, session_id="test_sess_owner", action_type="container_app_patch",
                payload=payload, user_id=101,
            )
            self.assertIsNone(await action_plans.get_action_plan(db, plan.plan_id, user_id=202))
            with self.assertRaises(ValueError):
                await action_plans.mark_plan_applied(db, plan.plan_id, user_id=202)
            plan.payload_json = json.dumps({"app_id": 7, "patch": {"health_path": "/unsafe"}})
            await db.commit()
            with self.assertRaises(ValueError) as ctx:
                await action_plans.mark_plan_applied(db, plan.plan_id, user_id=101)
            self.assertIn("integrity", str(ctx.exception).lower())


if __name__ == "__main__":
    unittest.main()
