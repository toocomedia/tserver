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
                self.assertEqual(res["inspection"]["ports"], [6379])


if __name__ == "__main__":
    unittest.main()
