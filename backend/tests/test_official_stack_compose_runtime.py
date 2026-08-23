"""Focused proof for panel-owned Compose manifest rendering and safety rules."""
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from services.official_stacks import compose_runtime
from services.official_stacks.catalog import get_stack
from services.official_stacks.manifest_validator import validate_stack_manifest
from services.official_stacks.schema import ServiceDefinition, VolumeDefinition


class TestOfficialStackComposeRuntime(unittest.TestCase):
    def setUp(self):
        self.stack = get_stack("plausible_ce")
        self.assertIsNotNone(self.stack)
        self.app = SimpleNamespace(id=71, host_port=32171)

    def test_persisted_manifest_and_service_scoped_secrets(self):
        manifest = compose_runtime.manifest_json(self.stack)
        restored = compose_runtime.stack_from_runtime(SimpleNamespace(id=71, stack_services=manifest))
        env = compose_runtime.service_environments(
            self.app, restored, "stats.example.test",
            {"POSTGRES_PASSWORD": "db-password", "SECRET_KEY_BASE": "app-secret"},
            {"DISABLE_REGISTRATION": "invite_only"},
        )
        self.assertEqual(env["plausible_db"]["POSTGRES_PASSWORD"], "db-password")
        self.assertNotIn("POSTGRES_PASSWORD", env["plausible"])
        self.assertEqual(env["plausible"]["SECRET_KEY_BASE"], "app-secret")
        self.assertIn("plausible_db:5432", env["plausible"]["DATABASE_URL"])

    def test_render_has_one_loopback_public_port_and_no_secret_values(self):
        env = compose_runtime.service_environments(
            self.app, self.stack, "stats.example.test",
            {"POSTGRES_PASSWORD": "db-password", "SECRET_KEY_BASE": "app-secret"}, {},
        )
        rendered = compose_runtime.render_compose(self.app, self.stack, env)
        web = rendered["services"]["plausible"]
        self.assertEqual(web["ports"], ["127.0.0.1:32171:8000"])
        self.assertNotIn("ports", rendered["services"]["plausible_db"])
        self.assertNotIn("db-password", str(rendered))
        self.assertNotIn("app-secret", str(rendered))

    def test_rejects_nested_or_unsafe_mounts(self):
        service = self.stack.services["plausible"]
        unsafe = dict(self.stack.services)
        unsafe["plausible"] = ServiceDefinition(
            **{**service.__dict__, "volumes": [
                VolumeDefinition("data", "/data"), VolumeDefinition("nested", "/data/cache"),
            ]}
        )
        with self.assertRaises(ValueError):
            validate_stack_manifest(type(self.stack)(**{**self.stack.__dict__, "services": unsafe}))


if __name__ == "__main__":
    unittest.main()
