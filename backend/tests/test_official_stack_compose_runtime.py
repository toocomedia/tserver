"""Focused proof for panel-owned Compose manifest rendering and safety rules."""
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from services.official_stacks import compose_runtime
from services.official_stacks.manifest_validator import validate_stack_manifest
from services.official_stacks.schema import (
    OfficialStackDefinition, SecretRequirement, ServiceDefinition, VolumeDefinition,
)


class TestOfficialStackComposeRuntime(unittest.TestCase):
    def setUp(self):
        self.stack = OfficialStackDefinition(
            catalog_id="test_analytics", display_name="Test Analytics", vendor_name="Test",
            description="", official_repositories=[], allowed_versions=["v1"], default_version="v1",
            services={
                "db": ServiceDefinition(
                    name="db", image_reference="postgres:16-alpine", pinned_tag="16-alpine",
                    internal_ports=[5432], volumes=[VolumeDefinition("db-data", "/var/lib/postgresql/data")],
                ),
                "web": ServiceDefinition(
                    name="web", image_reference="example.test/analytics:v1", pinned_tag="v1",
                    internal_ports=[8000], depends_on=["db"], is_web_entrypoint=True,
                ),
            },
            startup_order=["db", "web"], web_service_name="web", web_internal_port=8000,
            web_health_path="", required_secrets=[
                SecretRequirement("POSTGRES_PASSWORD", "Database password", "password", "db", "POSTGRES_PASSWORD"),
                SecretRequirement("APP_SECRET", "Application secret", "urlsafe64", "web", "APP_SECRET"),
            ], url_templates={"DATABASE_URL": "postgresql://postgres:{POSTGRES_PASSWORD}@{db}:5432/app"},
        )
        self.app = SimpleNamespace(id=71, host_port=32171)

    def test_persisted_manifest_and_service_scoped_secrets(self):
        manifest = compose_runtime.manifest_json(self.stack)
        restored = compose_runtime.stack_from_runtime(SimpleNamespace(id=71, stack_services=manifest))
        env = compose_runtime.service_environments(
            self.app, restored, "stats.example.test",
            {"POSTGRES_PASSWORD": "db-password", "APP_SECRET": "app-secret"},
            {"DISABLE_REGISTRATION": "invite_only"},
        )
        self.assertEqual(env["db"]["POSTGRES_PASSWORD"], "db-password")
        self.assertNotIn("POSTGRES_PASSWORD", env["web"])
        self.assertEqual(env["web"]["APP_SECRET"], "app-secret")
        self.assertIn("db:5432", env["web"]["DATABASE_URL"])

    def test_render_has_one_loopback_public_port_and_no_secret_values(self):
        env = compose_runtime.service_environments(
            self.app, self.stack, "stats.example.test",
            {"POSTGRES_PASSWORD": "db-password", "APP_SECRET": "app-secret"}, {},
        )
        rendered = compose_runtime.render_compose(self.app, self.stack, env)
        web = rendered["services"]["web"]
        self.assertEqual(web["ports"], ["127.0.0.1:32171:8000"])
        self.assertNotIn("ports", rendered["services"]["db"])
        self.assertNotIn("db-password", str(rendered))
        self.assertNotIn("app-secret", str(rendered))

    def test_rejects_nested_or_unsafe_mounts(self):
        service = self.stack.services["web"]
        unsafe = dict(self.stack.services)
        unsafe["web"] = ServiceDefinition(
            **{**service.__dict__, "volumes": [
                VolumeDefinition("data", "/data"), VolumeDefinition("nested", "/data/cache"),
            ]}
        )
        with self.assertRaises(ValueError):
            validate_stack_manifest(type(self.stack)(**{**self.stack.__dict__, "services": unsafe}))


if __name__ == "__main__":
    unittest.main()
