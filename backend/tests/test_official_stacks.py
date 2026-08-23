"""
Unit tests for the Generalized Official Compose Stacks Engine.
"""
import sys
import unittest
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from database import AsyncSessionLocal, init_db
from models.container_app import ContainerApp
from plugins.ai_helper.tools import app_setup
from services.official_stacks.catalog import get_stack, list_stacks, match_repository, register_stack
from services.official_stacks.manifest_validator import compute_stack_manifest_hash, validate_stack_request
from services.official_stacks.schema import (
    ConfigFileDefinition,
    HealthCheckDefinition,
    OfficialStackDefinition,
    SecretRequirement,
    ServiceDefinition,
    VolumeDefinition,
)
from services.official_stacks.source_detector import detect_official_stack
from services.official_stacks import stack_runtime_service
from services.resource_guard_profiles import classify_deployment


class TestOfficialStacks(unittest.IsolatedAsyncioTestCase):
    def test_official_stacks_schema_and_catalog(self):
        """Verify generic stack catalog registration, service graph, and hashing."""
        stacks = list_stacks()
        self.assertGreaterEqual(len(stacks), 1)

        stack = get_stack("plausible_ce")
        self.assertIsNotNone(stack)
        self.assertEqual(stack.catalog_id, "plausible_ce")
        self.assertEqual(len(stack.services), 3)

        # Test manifest fingerprinting
        hash_val = compute_stack_manifest_hash(stack, "v3.2.1")
        self.assertEqual(len(hash_val), 64)
        self.assertEqual(hash_val, compute_stack_manifest_hash(stack, "v3.2.1"))

    def test_generic_manifest_validation(self):
        """Verify strict parameter and version validation."""
        stack, clean = validate_stack_request("plausible_ce", "v3.2.1", {"TIMEZONE": "UTC"})
        self.assertEqual(clean["TIMEZONE"], "UTC")

        with self.assertRaises(ValueError):
            validate_stack_request("non_existent", "v1.0.0", {})

        with self.assertRaises(ValueError):
            validate_stack_request("plausible_ce", "v99.9.9", {})

        with self.assertRaises(ValueError):
            validate_stack_request("plausible_ce", "v3.2.1", {"UNAUTHORIZED_INJECTION": "bad"})

    def test_generic_environment_and_url_templates(self):
        """Verify dynamic URL template rendering across isolated internal network."""
        stack = get_stack("plausible_ce")
        app = ContainerApp(id=42, domain_id=1)
        vault_secrets = {
            "POSTGRES_PASSWORD": "super#secret!password",
            "SECRET_KEY_BASE": "vault-random-key",
        }
        env = stack_runtime_service.compile_stack_environment(
            app=app,
            stack=stack,
            domain_name="analytics.example.com",
            vault_secrets=vault_secrets,
            settings={"TIMEZONE": "Europe/Berlin"},
        )
        self.assertEqual(env["BASE_URL"], "https://analytics.example.com")
        self.assertEqual(env["TIMEZONE"], "Europe/Berlin")
        self.assertEqual(env["SECRET_KEY_BASE"], "vault-random-key")
        self.assertIn("srv-stack-42-plausible_db:5432/plausible_db", env["DATABASE_URL"])
        self.assertIn("super%23secret%21password", env["DATABASE_URL"])
        self.assertEqual(env["CLICKHOUSE_DATABASE_URL"], "http://srv-stack-42-plausible_events_db:8123/plausible_events_db")

    def test_generic_resource_guard_and_naming(self):
        """Verify resource guard and container naming conventions."""
        app = ContainerApp(id=5, deploy_type="official_stack")
        profile = classify_deployment(app)
        self.assertEqual(profile, "official_stack_pull")

        cname = stack_runtime_service.stack_container_name(5, "web")
        self.assertEqual(cname, "srv-stack-5-web")
        net = stack_runtime_service.stack_network_name(5)
        self.assertEqual(net, "srv-stack-net-5")
        vol = stack_runtime_service.stack_volume_name(5, "data")
        self.assertEqual(vol, "srv-stack-5-data")

    def test_repository_matching_and_source_detection(self):
        """Verify catalog repository matcher matches git repos and SSH URLs."""
        urls = [
            "https://github.com/plausible/community-edition.git",
            "https://github.com/plausible/community-edition",
            "git@github.com:plausible/community-edition.git",
            "https://github.com/plausible/analytics.git",
            "https://github.com/plausible/hosting.git",
        ]
        for url in urls:
            matched = match_repository(url)
            self.assertIsNotNone(matched, f"Failed matching {url}")
            self.assertEqual(matched[0].catalog_id, "plausible_ce")

            detection = detect_official_stack(url)
            self.assertTrue(detection["is_official_stack"])
            self.assertEqual(detection["catalog_id"], "plausible_ce")

        self.assertIsNone(match_repository("https://github.com/expressjs/express.git"))
        non_stack = detect_official_stack("https://github.com/expressjs/express.git")
        self.assertFalse(non_stack["is_official_stack"])

    async def test_ai_propose_official_stack_plan(self):
        """Verify AI Helper propose_official_stack_install tool generates immutable plan."""
        async with AsyncSessionLocal() as db:
            res = await app_setup.propose_official_stack_install(
                db=db,
                catalog_id="plausible_ce",
                version="v3.2.1",
                domain_name="stats.mysite.com",
                session_id="stack_ai_test_session",
                user_id=1,
                reasoning="Generic official stack test plan generation.",
            )
            self.assertEqual(res["status"], "ok")
            self.assertTrue(res["plan_id"].startswith("plan_"))

            from plugins.ai_helper.services import action_plans
            plan = await action_plans.get_action_plan(db, res["plan_id"], user_id=1)
            self.assertIsNotNone(plan)
            self.assertEqual(plan["action_type"], "official_stack_install")
            self.assertEqual(plan["payload"]["stack_catalog_id"], "plausible_ce")
            self.assertEqual(plan["payload"]["stack_version"], "v3.2.1")
            self.assertEqual(plan["payload"]["services_count"], 3)


    async def test_official_stack_create_flow(self):
        """Verify the create flow logic handles official_stack deploy_type correctly."""
        import config
        from models.domain import Domain
        from services import container_app_service
        from services.apps_engine import secret_vault, snapshots

        old_key = config.SECRET_KEY
        old_ephemeral = getattr(config, "_SECRET_KEY_EPHEMERAL", False)
        config.SECRET_KEY = "test_official_stacks_secret_key_32_bytes!!"
        config._SECRET_KEY_EPHEMERAL = False
        try:
            import uuid
            dom_name = f"test-stack-{uuid.uuid4().hex[:8]}.example.com"
            async with AsyncSessionLocal() as db:
                domain = Domain(
                    name=dom_name,
                    server_ip="127.0.0.1",
                    nginx_config_path="/tmp",
                    webroot_path="/tmp",
                    project_type="static",
                )
                db.add(domain)
                await db.commit()
                await db.refresh(domain)

                cat_id = "plausible_ce"
                stack = get_stack(cat_id)
                v = stack.default_version
                _, clean_settings = validate_stack_request(cat_id, v, {})

                app = await container_app_service.create_app(
                    db, domain=domain, source_type="image", build_mode="image",
                    deploy_type="official_stack", stack_catalog_id=cat_id, stack_version=v,
                    repository_url=stack.official_repositories[0],
                    branch=v, image_reference=stack.services[stack.web_service_name].image_reference,
                    internal_port=stack.web_internal_port,
                    ssl_requested=False,
                    environment_values={},
                    health_path=stack.web_health_path,
                    startup_timeout_seconds=stack.startup_timeout_seconds,
                )
                self.assertEqual(app.deploy_type, "official_stack")
                self.assertEqual(app.stack_catalog_id, "plausible_ce")

                real_vault_secrets = {}
                secret_reqs_for_snapshot = []
                for sec_req in stack.required_secrets:
                    sec_rec, _ = await secret_vault.ensure_secret(db, app.id, sec_req.key, sec_req.purpose)
                    real_vault_secrets[sec_req.key] = await secret_vault.secret_value(db, sec_rec.id)
                    secret_reqs_for_snapshot.append({"key": sec_req.key, "purpose": sec_req.purpose})

                compiled_env = stack_runtime_service.compile_stack_environment(
                    app, stack, domain.name, real_vault_secrets, clean_settings,
                )
                container_app_service.write_env(Path(app.env_path), compiled_env)
                self.assertTrue(Path(app.env_path).exists())

                snapshot = await snapshots.create_snapshot(
                    db, app, secret_requirements=secret_reqs_for_snapshot,
                    environment_patch=compiled_env, created_by_user_id=1,
                )
                self.assertIsNotNone(snapshot)
                self.assertEqual(snapshot.app_id, app.id)
        finally:
            config.SECRET_KEY = old_key
            config._SECRET_KEY_EPHEMERAL = old_ephemeral


if __name__ == "__main__":
    unittest.main()
