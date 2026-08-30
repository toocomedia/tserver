"""
Unit tests for the Generalized Official Compose Stacks Engine.
"""
import copy
import sys
import unittest
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from models.container_app import ContainerApp
from plugins.ai_helper.tools import app_setup
from services.official_stacks.catalog import (
    clear_catalog,
    get_stack,
    list_stacks,
    match_repository,
    register_stack,
    unregister_stack,
)
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

# Generic test stack definition used across unit tests
GENERIC_TEST_STACK = OfficialStackDefinition(
    catalog_id="generic_analytics",
    display_name="Generic Analytics Stack",
    vendor_name="Generic Vendor",
    description="Generic 3-service stack with Database, Events DB, and Web UI.",
    official_repositories=[
        "https://github.com/example-vendor/analytics-stack",
        "https://github.com/example-vendor/analytics-app",
        "git@github.com:example-vendor/analytics-stack.git",
    ],
    allowed_versions=["v1.0.0"],
    default_version="v1.0.0",
    services={
        "analytics_db": ServiceDefinition(
            name="analytics_db",
            image_reference="postgres:16-alpine",
            pinned_tag="16-alpine",
            internal_ports=[5432],
            volumes=[VolumeDefinition(name_suffix="db-data", container_mount_path="/var/lib/postgresql/data")],
            health_check=HealthCheckDefinition(
                probe_type="command",
                command=["pg_isready", "-U", "postgres"],
                interval_seconds=4,
                timeout_seconds=5,
                retries=15,
                start_period_seconds=15,
            ),
            memory_limit_mb=256,
            environment_defaults={"POSTGRES_USER": "postgres", "POSTGRES_DB": "analytics_db"},
        ),
        "analytics_events": ServiceDefinition(
            name="analytics_events",
            image_reference="clickhouse/clickhouse-server:24.12-alpine",
            pinned_tag="24.12-alpine",
            internal_ports=[8123, 9000],
            volumes=[VolumeDefinition(name_suffix="event-data", container_mount_path="/var/lib/clickhouse")],
            config_files=[
                ConfigFileDefinition(
                    filename="custom-conf.xml",
                    container_target_path="/etc/clickhouse-server/config.d/custom-conf.xml",
                    content="<clickhouse><logger><level>warning</level></logger></clickhouse>",
                ),
            ],
            health_check=HealthCheckDefinition(
                probe_type="command",
                command=["clickhouse-client", "--query", "SELECT 1"],
                interval_seconds=4,
                timeout_seconds=5,
                retries=15,
                start_period_seconds=20,
            ),
            memory_limit_mb=512,
        ),
        "analytics_web": ServiceDefinition(
            name="analytics_web",
            image_reference="example-vendor/analytics-web:v1.0.0",
            pinned_tag="v1.0.0",
            internal_ports=[8000],
            depends_on=["analytics_db", "analytics_events"],
            health_check=HealthCheckDefinition(
                probe_type="http",
                http_path="/api/health",
                http_port=8000,
                interval_seconds=5,
                timeout_seconds=5,
                retries=20,
                start_period_seconds=25,
            ),
            memory_limit_mb=512,
            is_web_entrypoint=True,
        ),
    },
    startup_order=["analytics_db", "analytics_events", "analytics_web"],
    web_service_name="analytics_web",
    web_internal_port=8000,
    web_health_path="/api/health",
    startup_timeout_seconds=60,
    recommended_ram_mb=2048,
    minimum_ram_mb=1024,
    required_secrets=[
        SecretRequirement(key="DB_PASSWORD", purpose="Primary database password", generator="password"),
        SecretRequirement(key="SECRET_KEY_BASE", purpose="Session signing key", generator="urlsafe64"),
    ],
    allowed_nonsecret_settings=["BASE_URL", "TIMEZONE", "DISABLE_REGISTRATION"],
    default_environment={"DISABLE_REGISTRATION": "invite_only"},
    url_templates={
        "DATABASE_URL": "postgresql://postgres:{DB_PASSWORD}@{analytics_db}:5432/analytics_db",
        "EVENTS_URL": "http://{analytics_events}:8123/analytics_events",
    },
)

AI_STACK_MANIFEST = {
    "name": "generic_analytics",
    "display_name": "Generic Analytics Stack",
    "vendor_name": "Generic Vendor",
    "source_repositories": ["https://github.com/example-vendor/analytics-stack"],
    "version": "v1.0.0",
    "services": [
        {
            "name": "analytics_db", "image": "postgres:16-alpine", "ports": [5432],
            "environment": {"POSTGRES_USER": "postgres", "POSTGRES_DB": "analytics_db"},
            "volumes": [{"name": "db-data", "mount_path": "/var/lib/postgresql/data"}],
            "resources": {"memory_mb": 256, "cpu": "0.5"},
            "health": {"type": "command", "command": ["pg_isready", "-U", "postgres"]},
        },
        {
            "name": "analytics_web", "image": "example-vendor/analytics-web:v1.0.0", "ports": [8000],
            "depends_on": ["analytics_db"], "resources": {"memory_mb": 512, "cpu": "1.0"},
        },
    ],
    "startup_order": ["analytics_db", "analytics_web"], "web_service": "analytics_web", "web_port": 8000,
    "secrets": [
        {"key": "DB_PASSWORD", "purpose": "Database password", "generator": "password", "service": "analytics_db", "environment": "POSTGRES_PASSWORD"},
        {"key": "SECRET_KEY_BASE", "purpose": "Application secret", "generator": "urlsafe64", "service": "analytics_web", "environment": "SECRET_KEY_BASE"},
    ],
    "url_templates": {"DATABASE_URL": "postgresql://postgres:{DB_PASSWORD}@{analytics_db}:5432/analytics_db"},
}


class TestOfficialStacks(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        register_stack(GENERIC_TEST_STACK)

    def tearDown(self):
        clear_catalog()

    def test_official_stacks_schema_and_catalog(self):
        """Verify generic stack catalog registration, service graph, and hashing."""
        stacks = list_stacks()
        self.assertGreaterEqual(len(stacks), 1)

        stack = get_stack("generic_analytics")
        self.assertIsNotNone(stack)
        self.assertEqual(stack.catalog_id, "generic_analytics")
        self.assertEqual(len(stack.services), 3)

        # Test manifest fingerprinting
        hash_val = compute_stack_manifest_hash(stack, "v1.0.0")
        self.assertEqual(len(hash_val), 64)
        self.assertEqual(hash_val, compute_stack_manifest_hash(stack, "v1.0.0"))

    def test_generic_manifest_validation(self):
        """Verify strict parameter and version validation."""
        stack, clean = validate_stack_request("generic_analytics", "v1.0.0", {"TIMEZONE": "UTC"})
        self.assertEqual(clean["TIMEZONE"], "UTC")

        with self.assertRaises(ValueError):
            validate_stack_request("non_existent", "v1.0.0", {})

        with self.assertRaises(ValueError):
            validate_stack_request("generic_analytics", "v99.9.9", {})

        with self.assertRaises(ValueError):
            validate_stack_request("generic_analytics", "v1.0.0", {"UNAUTHORIZED_INJECTION": "bad"})

    def test_generic_environment_and_url_templates(self):
        """Verify dynamic URL template rendering across isolated internal network."""
        stack = get_stack("generic_analytics")
        app = ContainerApp(id=42, domain_id=1)
        vault_secrets = {
            "DB_PASSWORD": "super#secret!password",
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
        self.assertIn("srv-stack-42-analytics_db:5432/analytics_db", env["DATABASE_URL"])
        self.assertIn("super%23secret%21password", env["DATABASE_URL"])
        self.assertEqual(env["EVENTS_URL"], "http://srv-stack-42-analytics_events:8123/analytics_events")

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
            "https://github.com/example-vendor/analytics-stack.git",
            "https://github.com/example-vendor/analytics-stack",
            "git@github.com:example-vendor/analytics-stack.git",
            "https://github.com/example-vendor/analytics-app.git",
        ]
        for url in urls:
            matched = match_repository(url)
            self.assertIsNotNone(matched, f"Failed matching {url}")
            self.assertEqual(matched[0].catalog_id, "generic_analytics")

            detection = detect_official_stack(url)
            self.assertTrue(detection["is_official_stack"])
            self.assertEqual(detection["catalog_id"], "generic_analytics")

        self.assertIsNone(match_repository("https://github.com/expressjs/express.git"))
        non_stack = detect_official_stack("https://github.com/expressjs/express.git")
        self.assertFalse(non_stack["is_official_stack"])

    async def _make_test_db(self):
        from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
        from database import Base, _migrate_sync
        test_engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
        async with test_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await conn.run_sync(_migrate_sync)
        return test_engine, async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)

    async def test_ai_propose_general_stack_plan(self):
        """Verify general structured manifest creates an immutable plan without catalog lookup."""
        engine, SessionFactory = await self._make_test_db()
        try:
            async with SessionFactory() as db:
                res = await app_setup.propose_stack_install(
                    db=db,
                    stack_manifest=AI_STACK_MANIFEST,
                    domain_name="stats.mysite.com",
                    evidence=["https://github.com/example-vendor/analytics-stack/README.md"],
                    session_id="stack_ai_test_session",
                    user_id=1,
                    reasoning="Generic stack test plan generation.",
                )
                self.assertEqual(res["status"], "ok")
                self.assertTrue(res["plan_id"].startswith("plan_"))

                from plugins.ai_helper.services import action_plans
                plan = await action_plans.get_action_plan(db, res["plan_id"], user_id=1)
                self.assertIsNotNone(plan)
                self.assertIn(plan["action_type"], {"app_spec_install", "stack_install"})
                if plan["action_type"] == "app_spec_install":
                    self.assertEqual(plan["payload"]["deploy_type"], "app_spec")
                    self.assertEqual(len(plan["payload"]["app_spec"]["services"]), 2)
                else:
                    self.assertEqual(plan["payload"]["stack_catalog_id"], "generic_analytics")
                    self.assertEqual(plan["payload"]["services_count"], 2)
        finally:
            await engine.dispose()

    def test_ai_stack_proposal_rejects_host_volume_aliases(self):
        """Host paths and forbidden sockets remain forbidden in AppSpec validation."""
        from services.apps_engine.security_policy import validate_app_spec
        manifest = {
            "name": "analytics_db",
            "services": {
                "db": {
                    "image": "postgres:16-alpine",
                    "volumes": [{"name_suffix": "data", "container_mount_path": "/var/run/docker.sock"}],
                }
            }
        }
        with self.assertRaises(ValueError):
            validate_app_spec(manifest)

    def test_server_fallback_builds_stack_args_from_compose_inspection(self):
        """If the provider stops, the panel can create a generic stack plan from inspection facts."""
        source_result = {
            "status": "ok",
            "source_type": "compose_stack",
            "inspection": {
                "repository_url": "https://github.com/example/app",
                "branch": "main",
                "internal_port": 8000,
                "env_sample": {"BASE_URL": "", "SECRET_KEY_BASE": ""},
                "compose_info": {
                    "services": [
                        {"name": "db", "image": "postgres:16-alpine", "internal_ports": []},
                        {"name": "events", "image": "clickhouse/clickhouse-server:24.12-alpine", "internal_ports": []},
                        {"name": "web", "image": "example/app:v1.2.3", "internal_ports": [8000]},
                    ],
                    "evidence": ["docker-compose.yml: service 'web' uses image 'example/app:v1.2.3'."],
                },
            },
        }
        args = app_setup.stack_plan_args_from_inspection(source_result, domain_name="stats.example.com")
        self.assertIsNotNone(args)
        manifest = args["stack_manifest"]
        self.assertEqual(manifest["web_service"], "web")
        self.assertEqual(manifest["web_port"], 8000)
        self.assertEqual(manifest["services"][0]["volumes"][0]["mount_path"], "/var/lib/postgresql/data")
        self.assertIn({"key": "SECRET_KEY_BASE", "purpose": "Application secret", "generator": "base64_48", "service": "web", "environment": "SECRET_KEY_BASE"}, manifest["secrets"])
        self.assertEqual(args["nonsecret_settings"]["BASE_URL"], "https://stats.example.com")

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
        engine, SessionFactory = await self._make_test_db()
        try:
            async with SessionFactory() as db:
                domain = Domain(
                    name="test-stack-flow.example.com",
                    server_ip="127.0.0.1",
                    nginx_config_path="/tmp",
                    webroot_path="/tmp",
                    project_type="static",
                )
                db.add(domain)
                await db.commit()
                await db.refresh(domain)

                cat_id = "generic_analytics"
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
                self.assertEqual(app.stack_catalog_id, "generic_analytics")

                real_vault_secrets = {}
                secret_reqs_for_snapshot = []
                for sec_req in stack.required_secrets:
                    sec_rec, _ = await secret_vault.ensure_secret(db, app.id, sec_req.key, sec_req.purpose)
                    real_vault_secrets[sec_req.key] = await secret_vault.secret_value(db, sec_rec.id)
                    secret_reqs_for_snapshot.append({
                        "key": sec_req.key,
                        "purpose": sec_req.purpose,
                        "generator": sec_req.generator,
                    })

                compiled_env = stack_runtime_service.compile_stack_environment(
                    app, stack, domain.name, real_vault_secrets, clean_settings,
                )
                container_app_service.write_env(Path(app.env_path), compiled_env)
                self.assertTrue(Path(app.env_path).exists())

                snapshot, statuses = await snapshots.create_snapshot(
                    db, app, secret_requirements=secret_reqs_for_snapshot,
                    environment_patch=compiled_env, created_by_user_id=1,
                )
                self.assertIsNotNone(snapshot)
                self.assertEqual(snapshot.app_id, app.id)
        finally:
            await engine.dispose()
            config.SECRET_KEY = old_key
            config._SECRET_KEY_EPHEMERAL = old_ephemeral


if __name__ == "__main__":
    unittest.main()
