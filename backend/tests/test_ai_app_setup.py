"""
test_ai_app_setup.py — Unit tests for AI application setup and proposal generator tools.
"""
from pathlib import Path
import sys
import unittest
import uuid
from unittest.mock import AsyncMock, patch

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from database import AsyncSessionLocal, init_db
from models.container_app import ContainerApp
from models.domain import Domain
from plugins.ai_helper.tools import app_setup
from plugins.ai_helper.services import setup_plan_builder


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
                user_id=1,
            )

            self.assertEqual(res["status"], "ok")
            self.assertTrue(res["plan_id"].startswith("plan_"))
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

    async def test_propose_container_app_patch_tool(self):
        """Verify propose_container_app_patch creates a review draft plan."""
        from models.container_app import ContainerApp
        from models.domain import Domain
        from services import container_app_service
        import uuid
        uid = uuid.uuid4().hex[:8]
        async with AsyncSessionLocal() as db:
            rnd_port = await container_app_service.next_host_port(db)
            domain = Domain(name=f"patch-{uid}.local", server_ip="127.0.0.1")
            db.add(domain)
            await db.flush()

            app = ContainerApp(
                domain_id=domain.id,
                container_name=f"test-patch-{uid}",
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

            res = await app_setup.propose_container_app_patch(
                db=db,
                app_id=app.id,
                patch={"internal_port": 8080},
                evidence=["Detected custom port 8080 in logs"],
                summary="Update port to 8080",
                confidence=0.9,
                user_id=1,
            )
            self.assertEqual(res["status"], "ok")
            self.assertTrue(res["plan_id"].startswith("plan_"))

    async def test_guaranteed_automatic_setup_plan_single_app(self):
        """Verify build_automatic_setup_plan creates valid single-app plan from image."""
        async with AsyncSessionLocal() as db:
            plan = await setup_plan_builder.build_automatic_setup_plan(
                db=db,
                session_id="fallback_single_test",
                user_id=1,
                source_type="image",
                image_reference="redis:7-alpine",
                domain_name="cache.example.com",
            )
            self.assertIsNotNone(plan)
            self.assertTrue(plan.plan_id.startswith("plan_"))
            self.assertEqual(plan.action_type, "app_install")

    async def test_guaranteed_automatic_setup_plan_compose(self):
        """Verify build_automatic_setup_plan creates valid stack plan from compose facts."""
        mock_inspection = {
            "repository_url": "https://github.com/example/analytics",
            "branch": "main",
            "compose_info": {
                "services": [
                    {"name": "web", "image": "example/analytics:v1", "internal_ports": [8000]},
                    {"name": "db", "image": "postgres:16-alpine", "internal_ports": [5432]},
                ],
                "evidence": ["Compose detected"],
            },
        }
        async with AsyncSessionLocal() as db:
            plan = await setup_plan_builder.build_automatic_setup_plan(
                db=db,
                session_id="fallback_compose_test",
                user_id=1,
                source_type="git",
                repository_url="https://github.com/example/analytics",
                domain_name="analytics.example.com",
                inspection_result={"status": "ok", "inspection": mock_inspection},
            )
            self.assertIsNotNone(plan)
            self.assertTrue(plan.plan_id.startswith("plan_"))
            self.assertEqual(plan.action_type, "stack_install")

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

    async def test_propose_container_app_patch_git_to_image_transition(self):
        """Verify an existing Git app can be patched in-place to Docker image mode."""
        from models.container_app import ContainerApp
        from models.domain import Domain
        from services import container_app_service
        import uuid
        uid = uuid.uuid4().hex[:8]
        async with AsyncSessionLocal() as db:
            rnd_port = await container_app_service.next_host_port(db)
            domain = Domain(name=f"jellyfin-{uid}.local", server_ip="127.0.0.1")
            db.add(domain)
            await db.flush()

            app = ContainerApp(
                domain_id=domain.id,
                container_name=f"test-jellyfin-{uid}",
                source_type="git",
                build_mode="railpack",
                repository_url="https://github.com/jellyfin/jellyfin",
                branch="master",
                host_port=rnd_port,
                internal_port=3000,
                env_path="/tmp/test_jellyfin.env",
                status="failed",
            )
            db.add(app)
            await db.commit()
            await db.refresh(app)

            res = await app_setup.propose_container_app_patch(
                db=db,
                app_id=app.id,
                patch={
                    "source_type": "image",
                    "build_mode": "image",
                    "image_reference": "jellyfin/jellyfin:latest",
                    "internal_port": 8096,
                    "health_path": "disabled",
                },
                evidence=["Jellyfin is best deployed via official Docker image jellyfin/jellyfin on port 8096."],
                summary="Switch App to official Jellyfin Docker image",
                confidence=1.0,
                user_id=1,
            )
            self.assertEqual(res["status"], "ok")
            self.assertTrue(res["plan_id"].startswith("plan_"))

    def test_source_image_advisor_jellyfin(self):
        """Verify source_image_advisor recognizes Jellyfin Git repo and recommends official image."""
        from services.apps_engine.source_image_advisor import advise_official_image
        advice = advise_official_image("https://github.com/jellyfin/jellyfin")
        self.assertIsNotNone(advice)
        self.assertTrue(advice["has_official_image"])
        self.assertEqual(advice["recommended_image"], "jellyfin/jellyfin:latest")
        self.assertEqual(advice["recommended_port"], 8096)

        no_advice = advise_official_image("https://github.com/myuser/custom-python-app")
        self.assertIsNone(no_advice)

    def test_environment_normalization_helpers(self):
        """Verify build_secrets normalizes keys, values, and separates secrets."""
        from services.apps_engine import build_secrets
        self.assertEqual(build_secrets.normalize_environment_key("node-env"), "NODE_ENV")
        self.assertEqual(build_secrets.normalize_environment_key("app.api_url"), "APP_API_URL")
        self.assertEqual(build_secrets.normalize_environment_key("123port"), "ENV_123PORT")
        self.assertEqual(build_secrets.normalize_environment_key(""), "")

        self.assertEqual(build_secrets.normalize_environment_value('"production"\n'), "production")
        self.assertEqual(build_secrets.normalize_environment_value("'https://example.com'\r\n"), "https://example.com")
        self.assertEqual(build_secrets.normalize_environment_value("`single_line`"), "single_line")

        clean_envs, secrets = build_secrets.normalize_environment_map({
            "node-env": "production\r\n",
            "app.jwt_secret": "raw_secret_value",
            "db-password": "raw_db_password",
            "encryption_key": "raw_enc_key",
            "DATABASE_URL": "postgresql://user:pass@host/db",
        })
        self.assertEqual(clean_envs, {"NODE_ENV": "production"})
        secret_keys = {s["key"] for s in secrets}
        self.assertIn("APP_JWT_SECRET", secret_keys)
        self.assertIn("DB_PASSWORD", secret_keys)
        self.assertIn("ENCRYPTION_KEY", secret_keys)
        self.assertNotIn("DATABASE_URL", clean_envs)

    def test_setup_plan_builder_normalization_and_port_sync(self):
        """Verify setup_plan_builder single app payload normalizes env and syncs PORT."""
        payload = setup_plan_builder.build_single_app_payload(
            source_type="image",
            image_reference="redis:alpine",
            internal_port=6379,
            environment_values={
                "log-level": "info\n",
                "PORT": "3000",
                "auth-token": "secret_token",
            },
        )
        self.assertEqual(payload["environment_values"]["LOG_LEVEL"], "info")
        self.assertEqual(payload["environment_values"]["PORT"], "6379")
        self.assertEqual(len(payload["secret_requirements"]), 1)
        self.assertEqual(payload["secret_requirements"][0]["key"], "AUTH_TOKEN")

    async def test_propose_container_app_patch_environment_normalization(self):
        """Verify propose_container_app_patch safely normalizes environment values and secrets."""
        from models.container_app import ContainerApp
        from models.domain import Domain
        from services import container_app_service
        import uuid
        uid = uuid.uuid4().hex[:8]
        async with AsyncSessionLocal() as db:
            rnd_port = await container_app_service.next_host_port(db)
            domain = Domain(name=f"norm-{uid}.local", server_ip="127.0.0.1")
            db.add(domain)
            await db.flush()

            app = ContainerApp(
                domain_id=domain.id,
                container_name=f"test-norm-{uid}",
                source_type="image",
                build_mode="image",
                image_reference="nginx:alpine",
                host_port=rnd_port,
                internal_port=80,
                env_path="/tmp/test_norm.env",
                status="running",
            )
            db.add(app)
            await db.commit()
            await db.refresh(app)

            res = await app_setup.propose_container_app_patch(
                db=db,
                app_id=app.id,
                patch={"internal_port": 8080},
                environment_values={
                    "node-env": "production\r\n",
                    "app_jwt_secret": "my_secret_token",
                },
                evidence=["Config fix required in environment"],
                summary="Normalize environment variables test",
                confidence=1.0,
                user_id=1,
            )
            self.assertEqual(res["status"], "ok")
            plan_id = res["plan_id"]
            self.assertTrue(plan_id.startswith("plan_"))

            from plugins.ai_helper.services import action_plans
            plan = await action_plans.get_action_plan(db, plan_id, user_id=1)
            self.assertIsNotNone(plan)
            payload = plan["payload"]
            self.assertEqual(payload["environment_values"], {"NODE_ENV": "production"})
            self.assertEqual(len(payload["secret_requirements"]), 1)
            self.assertEqual(payload["secret_requirements"][0]["key"], "APP_JWT_SECRET")

    async def test_propose_container_app_patch_graceful_metadata_filtering(self):
        """Verify propose_container_app_patch filters extraneous stack/diagnostic fields gracefully."""
        from models.container_app import ContainerApp
        from models.domain import Domain
        from services import container_app_service
        import uuid
        uid = uuid.uuid4().hex[:8]
        async with AsyncSessionLocal() as db:
            rnd_port = await container_app_service.next_host_port(db)
            domain = Domain(name=f"patchmeta-{uid}.local", server_ip="127.0.0.1")
            db.add(domain)
            await db.flush()

            app = ContainerApp(
                domain_id=domain.id,
                container_name=f"test-meta-{uid}",
                source_type="image",
                build_mode="image",
                image_reference="nginx:alpine",
                host_port=rnd_port,
                internal_port=80,
                env_path="/tmp/test_meta.env",
                status="running",
            )
            db.add(app)
            await db.commit()
            await db.refresh(app)

            # AI emits extra fields like service / yaml / diagnostic notes in patch
            res = await app_setup.propose_container_app_patch(
                db=db,
                app_id=app.id,
                patch={
                    "service": "op-rp-console",
                    "notes": "Fix broker connection",
                    "internal_port": 8080,
                },
                environment_values={"KAFKA_BROKERS": "op-rp:9092"},
                evidence=["Detected broker config missing in logs"],
                summary="Add KAFKA_BROKERS configuration",
                confidence=1.0,
                user_id=1,
            )
            self.assertEqual(res["status"], "ok")
            self.assertTrue(res["plan_id"].startswith("plan_"))

            from plugins.ai_helper.services import action_plans
            plan = await action_plans.get_action_plan(db, res["plan_id"], user_id=1)
            self.assertIsNotNone(plan)
            payload = plan["payload"]
            self.assertEqual(payload["patch"]["internal_port"], 8080)
            self.assertNotIn("service", payload["patch"])
            self.assertNotIn("notes", payload["patch"])
            self.assertEqual(payload["environment_values"], {"KAFKA_BROKERS": "op-rp:9092"})

    async def test_delete_app_purges_ai_sessions(self):
        """Verify deleting an app cascades and removes associated AI chat sessions."""
        from models.container_app import ContainerApp
        from models.domain import Domain
        from models.ai_helper import AiChatMessage, AiChatSession
        from services import container_app_cleanup_service, container_app_service
        import uuid
        uid = uuid.uuid4().hex[:8]
        sess_id = f"sess_del_{uid}"
        async with AsyncSessionLocal() as db:
            rnd_port = await container_app_service.next_host_port(db)
            domain = Domain(name=f"delsess-{uid}.local", server_ip="127.0.0.1")
            db.add(domain)
            await db.flush()

            app = ContainerApp(
                domain_id=domain.id,
                container_name=f"test-del-{uid}",
                source_type="image",
                build_mode="image",
                image_reference="nginx:alpine",
                host_port=rnd_port,
                internal_port=80,
                env_path="/tmp/test_del.env",
                status="running",
            )
            db.add(app)
            await db.flush()

            session = AiChatSession(
                session_id=sess_id,
                title="Test Chat",
                task_type="app_deploy",
                context_key=f"app:{app.id}",
            )
            db.add(session)
            msg = AiChatMessage(session_id=sess_id, role="user", content="Deploy openpanel")
            db.add(msg)
            await db.commit()

            # Now delete the app
            with patch("services.nginx_service.create_static_site", AsyncMock(return_value="")), \
                 patch("services.nginx_service.reload", AsyncMock(return_value=True)):
                await container_app_cleanup_service.delete_app(db, app)
            await db.commit()

            # Verify session is cleaned up
            from sqlalchemy import select
            remaining_sess = await db.scalar(select(AiChatSession.session_id).where(AiChatSession.session_id == sess_id))
            self.assertIsNone(remaining_sess)

    async def test_propose_container_app_patch_aliases_and_empty(self):
        """Verify propose_container_app_patch gracefully handles web_port/port aliases and empty patch."""
        uid = uuid.uuid4().hex[:6]
        async with AsyncSessionLocal() as db:
            domain = Domain(name=f"patch-test-{uid}.com", server_ip="127.0.0.1")
            db.add(domain)
            await db.flush()

            import random
            rnd_port = random.randint(35000, 39000)
            app = ContainerApp(
                domain_id=domain.id,
                container_name=f"test-patch-{uid}",
                source_type="image",
                build_mode="image",
                image_reference="nginx:alpine",
                host_port=rnd_port,
                internal_port=80,
                env_path="/tmp/test_patch.env",
                status="running",
            )
            db.add(app)
            await db.commit()
            await db.refresh(app)

            # Test 1: Patch with alias web_port: 8080 and web_service
            res1 = await app_setup.propose_container_app_patch(
                db=db,
                app_id=app.id,
                patch={"web_service": "op-rp-console", "web_port": 8080},
                evidence=["Log evidence: listening on 8080"],
                user_id=1,
            )
            self.assertEqual(res1["status"], "ok")
            self.assertIn("plan_id", res1)

            # Test 2: Patch with empty patch and only environment values
            res2 = await app_setup.propose_container_app_patch(
                db=db,
                app_id=app.id,
                patch={},
                environment_values={"KAFKA_BROKERS": "op-rp:9092"},
                evidence=["Log evidence: missing broker"],
                user_id=1,
            )
            self.assertEqual(res2["status"], "ok")
            self.assertIn("plan_id", res2)

    def test_scoped_app_engine_prompt_builder(self):
        """Verify build_system_prompt generates scoped lightweight prompts for container tasks."""
        from plugins.ai_helper.prompts import builder

        # 1. App deploy skill prompt
        deploy_prompt = builder.build_system_prompt(skill="app_deploy")
        self.assertIn("specialized in App Engine", deploy_prompt)
        self.assertNotIn("PHP Engine", deploy_prompt)
        self.assertNotIn("PowerDNS", deploy_prompt)
        self.assertIn("App Engine setup plan", deploy_prompt)

        # 2. Error diag skill prompt
        diag_prompt = builder.build_system_prompt(skill="error_diag")
        self.assertIn("specialized in App Engine", diag_prompt)
        self.assertNotIn("PHP Engine", diag_prompt)
        self.assertIn("Error Diagnosis Mode", diag_prompt)

        # 3. General task prompt still includes full VPS architecture
        general_prompt = builder.build_system_prompt(skill="general")
        self.assertIn("PHP Engine", general_prompt)
        self.assertIn("PowerDNS", general_prompt)


    def test_extract_app_id_from_various_formats(self):
        """Verify _extract_app_id parses ID from failure notifications and context keys."""
        from plugins.ai_helper.services.chat import _extract_app_id

        self.assertEqual(_extract_app_id("Application open.blagh.co (ID #3) failed or is stopped."), 3)
        self.assertEqual(_extract_app_id("Application open.blagh.co (ID 12) is crashing"), 12)
        self.assertEqual(_extract_app_id("app:42"), 42)
        self.assertEqual(_extract_app_id("container:7"), 7)
        self.assertEqual(_extract_app_id("app_id: 15"), 15)
        self.assertIsNone(_extract_app_id("Deploy my python app on example.com"))


if __name__ == "__main__":
    unittest.main()
