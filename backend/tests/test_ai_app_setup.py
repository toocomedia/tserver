"""
test_ai_app_setup.py — Unit tests for AI application setup and proposal generator tools.
"""
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, patch

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from database import AsyncSessionLocal, init_db
from plugins.ai_helper.tools import app_setup


class TestAiAppSetup(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await init_db()

    async def test_propose_app_install_tool(self):
        """Verify propose_app_install creates plan and returns opaque action tag."""
        async with AsyncSessionLocal() as db:
            res = await app_setup.propose_app_install(
                db=db,
                session_id="session_deploy_test",
                source_type="image",
                image_reference="n8nio/n8n:latest",
                internal_port=5678,
                environment_values={"N8N_PORT": "5678", "GENERIC_TIMEZONE": "UTC"},
                database_attachments=[{"kind": "postgres", "provider": "docker", "environment_key": "DB_POSTGRESDB_HOST"}],
                storage_mounts=[{"label": "n8n_data", "mount_path": "/home/node/.n8n"}],
                summary="Deploy n8n automation tool",
                confidence=0.98,
                reasoning="Detected official n8n workflow image with Postgres DB attachment.",
            )

            self.assertEqual(res["status"], "ok")
            self.assertTrue(res["plan_id"].startswith("plan_"))
            self.assertEqual(res["action_tag"], f"[ACTION:APP_PLAN:{res['plan_id']}]")
            self.assertEqual(res["summary"], "Deploy n8n automation tool")
            self.assertEqual(res["confidence"], 0.98)

    async def test_inspect_app_source_docker(self):
        """Verify inspect_app_source delegates to container inspection service."""
        mock_inspect = {
            "image": "redis:7-alpine",
            "ports": [6379],
            "env": {"REDIS_VERSION": "7.0"},
        }
        with patch("services.container_app_image_inspect_service.inspect_image", AsyncMock(return_value=mock_inspect)):
            async with AsyncSessionLocal() as db:
                res = await app_setup.inspect_app_source(
                    db=db,
                    source_type="image",
                    image_reference="redis:7-alpine",
                )
                self.assertEqual(res["status"], "ok")
                self.assertEqual(res["source_type"], "image")
    async def test_redeploy_app_tool(self):
        """Verify redeploy_app tool queues redeployment when app exists."""
        from plugins.ai_helper.tools import apps as ai_apps
        from models.container_app import ContainerApp
        from models.domain import Domain

        import random
        import uuid
        uid = uuid.uuid4().hex[:8]
        rnd_port = random.randint(31000, 45000)
        async with AsyncSessionLocal() as db:
            domain = Domain(name=f"redeploy-{uid}.local", server_ip="127.0.0.1")
            db.add(domain)
            await db.flush()

            app = ContainerApp(
                domain_id=domain.id,
                container_name=f"test-redeploy-{uid}",
                source_type="image",
                build_mode="image",
                image_reference="nginx:alpine",
                host_port=rnd_port,
                internal_port=80,
                env_path="/tmp/test.env",
                status="running",
            )
            db.add(app)
            await db.commit()
            await db.refresh(app)

            with patch("services.container_app_deployment_service.queue_deployment", AsyncMock()) as mock_queue:
                from models.container_app_deployment import ContainerAppDeployment
                mock_dep = ContainerAppDeployment(id=999, app_id=app.id, action="redeploy", status="queued")
                mock_queue.return_value = mock_dep

                res = await ai_apps.redeploy_app(db=db, app_id=app.id, app_type="container")
                self.assertEqual(res["status"], "ok")
                self.assertEqual(res["app_id"], app.id)
                self.assertEqual(res["deployment_id"], 999)
                self.assertEqual(res["action_tag"], f"[ACTION:APP_REDEPLOY:{app.id}]")

    async def test_cancel_deployment(self):
        """Verify cancel_deployment marks deployment as cancelled and unlocks app."""
        from models.container_app import ContainerApp
        from models.container_app_deployment import ContainerAppDeployment
        from models.domain import Domain
        from services import container_app_deployment_service

        import random
        import uuid
        uid = uuid.uuid4().hex[:8]
        rnd_port = random.randint(31000, 45000)
        async with AsyncSessionLocal() as db:
            domain = Domain(name=f"cancel-{uid}.local", server_ip="127.0.0.1")
            db.add(domain)
            await db.flush()

            app = ContainerApp(
                domain_id=domain.id,
                container_name=f"test-cancel-{uid}",
                source_type="image",
                build_mode="image",
                image_reference="nginx:alpine",
                host_port=rnd_port,
                internal_port=80,
                env_path="/tmp/test.env",
                status="pending",
            )
            db.add(app)
            await db.commit()
            await db.refresh(app)

            dep = ContainerAppDeployment(
                app_id=app.id,
                action="deploy",
                status="queued",
                stage="prepare",
            )
            db.add(dep)
            await db.commit()
            await db.refresh(dep)

            cancelled_dep = await container_app_deployment_service.cancel_deployment(db, app.id, dep.id)
            self.assertIsNotNone(cancelled_dep)
            self.assertEqual(cancelled_dep.status, "cancelled")
            self.assertEqual(cancelled_dep.stage, "cancelled")

            await db.refresh(app)
            self.assertIn(app.status, ("stopped", "failed", "running"))


if __name__ == "__main__":
    unittest.main()
