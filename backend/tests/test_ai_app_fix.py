"""
test_ai_app_fix.py — Tests for AI Container Diagnostic, Patch & Redeploy Loop.
Covers root cause patch proposal, alias normalization, and non-destructive redeployment.
"""
from __future__ import annotations

import sys
import unittest
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import config
from database import AsyncSessionLocal, init_db
from models.container_app import ContainerApp
from models.domain import Domain
from plugins.ai_helper.services import action_plans
from plugins.ai_helper.tools import app_setup
from services import container_app_service
from services.apps_engine import deployment_drafts, reviewed_setup_deploy


class TestAiAppFix(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await init_db()
        self.old_key = config.SECRET_KEY
        self.old_ephemeral = getattr(config, "_SECRET_KEY_EPHEMERAL", False)
        config.SECRET_KEY = "test-app-fix-secret-key-32-bytes!"
        config._SECRET_KEY_EPHEMERAL = False

    async def asyncTearDown(self):
        config.SECRET_KEY = self.old_key
        config._SECRET_KEY_EPHEMERAL = self.old_ephemeral

    async def test_propose_container_app_patch_creates_valid_plan(self):
        """Verify that propose_container_app_patch creates a valid container_app_patch plan with exact diff."""
        uid = uuid.uuid4().hex[:8]
        async with AsyncSessionLocal() as db:
            port = await container_app_service.next_host_port(db)
            domain = Domain(name=f"fix-{uid}.local", server_ip="127.0.0.1")
            db.add(domain)
            await db.flush()

            app = ContainerApp(
                domain_id=domain.id,
                container_name=f"app-fix-{uid}",
                source_type="image",
                build_mode="image",
                image_reference="nginx:alpine",
                host_port=port,
                internal_port=80,
                env_path=f"/tmp/test_{uid}.env",
                status="failed",
                last_error="502 Bad Gateway: Connection refused",
            )
            db.add(app)
            await db.commit()
            await db.refresh(app)

            res = await app_setup.propose_container_app_patch(
                db=db,
                app_id=app.id,
                patch={"internal_port": 8080},
                environment_values={"HOST": "0.0.0.0", "PORT": "8080"},
                evidence=["Log line 14: Server listening on 0.0.0.0:8080", "502 Bad Gateway observed on proxy"],
                summary="Fix internal port mismatch from 80 to 8080",
                confidence=0.95,
                reasoning="Container listens on port 8080 but reverse proxy was routing to port 80.",
                user_id=1,
            )
            self.assertEqual(res["status"], "ok")
            self.assertTrue(res["plan_id"].startswith("plan_"))

            plan = await action_plans.get_action_plan(db, res["plan_id"], user_id=1)
            self.assertIsNotNone(plan)
            self.assertEqual(plan["action_type"], "container_app_patch")
            self.assertEqual(plan["payload"]["app_id"], app.id)
            self.assertEqual(plan["payload"]["patch"]["internal_port"], 8080)
            self.assertEqual(plan["payload"]["environment_values"]["PORT"], "8080")
            self.assertEqual(len(plan["payload"]["evidence"]), 2)

    def test_patch_normalization_and_aliases(self):
        """Verify _normalize_patch normalizes aliases (port, web_port, web_health_path, start_command)."""
        app = ContainerApp(
            id=1,
            source_type="image",
            build_mode="image",
            image_reference="redis:alpine",
            internal_port=6379,
            env_path="/tmp/test.env",
        )
        raw_patch = {
            "web_port": 8080,
            "web_health_path": "/healthz",
            "start_command": "npm start",
            "ignored_field_xyz": "value",
        }
        normalized = deployment_drafts._normalize_patch(app, raw_patch)
        self.assertEqual(normalized.get("internal_port"), 8080)
        self.assertEqual(normalized.get("health_path"), "/healthz")
        self.assertEqual(normalized.get("custom_start_command"), "npm start")
        self.assertNotIn("ignored_field_xyz", normalized)

    async def test_deploy_patch_increments_snapshot_and_queues_deployment(self):
        """Verify reviewed_setup_deploy.deploy_plan applies the patch plan and queues a deployment."""
        uid = uuid.uuid4().hex[:8]
        async with AsyncSessionLocal() as db:
            port = await container_app_service.next_host_port(db)
            domain = Domain(name=f"deploy-patch-{uid}.local", server_ip="127.0.0.1")
            db.add(domain)
            await db.flush()

            app = ContainerApp(
                domain_id=domain.id,
                container_name=f"app-redeploy-{uid}",
                source_type="image",
                build_mode="image",
                image_reference="nginx:alpine",
                host_port=port,
                internal_port=80,
                env_path=f"/tmp/test_{uid}.env",
                status="running",
            )
            db.add(app)
            await db.commit()
            await db.refresh(app)

            res = await app_setup.propose_container_app_patch(
                db=db,
                app_id=app.id,
                patch={"internal_port": 3000},
                environment_values={"NODE_ENV": "production"},
                evidence=["Application port changed to 3000"],
                user_id=1,
            )
            self.assertEqual(res["status"], "ok")
            plan_id = res["plan_id"]

            with patch("services.container_app_deployment_service.advance_queue", new_callable=AsyncMock):
                app_id, dep_id = await reviewed_setup_deploy.deploy_plan(db, plan_id, user_id=1)
                self.assertEqual(app_id, app.id)
                self.assertIsInstance(dep_id, int)

            # Confirm plan is marked applied
            applied_plan = await action_plans.get_action_plan(db, plan_id, user_id=1)
            self.assertEqual(applied_plan["status"], "applied")


if __name__ == "__main__":
    unittest.main()
