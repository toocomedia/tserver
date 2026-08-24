import sys
import unittest
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from services.official_stacks.proposal_manifest import stack_from_proposal
from services.official_stacks.stack_synthesizer import (
    requires_multi_container_stack,
    synthesize_stack_from_compose,
    synthesize_stack_from_inspection,
)


class TestStackSynthesizer(unittest.TestCase):
    def test_requires_multi_container_stack_detection(self):
        # Single app with standard single DB
        single_app = {"database_types": ["postgresql"]}
        self.assertFalse(requires_multi_container_stack(single_app))

        # App with ClickHouse (must run as stack)
        clickhouse_app = {"database_types": ["clickhouse"]}
        self.assertTrue(requires_multi_container_stack(clickhouse_app))

        # App with Plausible combination (PostgreSQL + ClickHouse)
        plausible_app = {"database_types": ["postgresql", "clickhouse"]}
        self.assertTrue(requires_multi_container_stack(plausible_app))

        # App with multi-database (PostgreSQL + Redis)
        multi_db_app = {"database_types": ["postgresql", "redis"]}
        self.assertTrue(requires_multi_container_stack(multi_db_app))

        # Compose with 2+ services
        compose_app = {"compose_info": {"services": [{"name": "web"}, {"name": "db"}]}}
        self.assertTrue(requires_multi_container_stack(compose_app))

    def test_synthesize_plausible_stack_from_inspection(self):
        inspection = {
            "repository_url": "https://github.com/plausible/analytics",
            "branch": "master",
            "runtime": "Elixir",
            "framework": "Elixir",
            "build_mode": "dockerfile",
            "internal_port": 8000,
            "database_types": ["postgresql", "clickhouse"],
            "env_sample": {
                "SECRET_KEY_BASE": "generate_secret",
                "BASE_URL": "http://localhost:8000",
                "DISABLE_REGISTRATION": "invite_only",
            },
        }

        bundle = synthesize_stack_from_inspection(inspection, domain_name="cc.tooco.net", repo_url="https://github.com/plausible/analytics")
        self.assertIsNotNone(bundle)
        self.assertEqual(bundle["domain_name"], "cc.tooco.net")
        self.assertEqual(bundle["nonsecret_settings"]["BASE_URL"], "https://cc.tooco.net")

        manifest = bundle["stack_manifest"]
        self.assertEqual(manifest["web_port"], 8000)
        self.assertEqual(manifest["web_service"], "analytics")

        services = {s["name"]: s for s in manifest["services"]}
        self.assertIn("analytics", services)
        self.assertIn("analytics_postgres", services)
        self.assertIn("analytics_clickhouse", services)

        # Plausible web depends on postgres & clickhouse
        self.assertIn("analytics_postgres", services["analytics"]["depends_on"])
        self.assertIn("analytics_clickhouse", services["analytics"]["depends_on"])

        # Check URL templates
        self.assertIn("DATABASE_URL", manifest["url_templates"])
        self.assertIn("CLICKHOUSE_DATABASE_URL", manifest["url_templates"])

        # Check secrets
        secret_keys = {sec["key"] for sec in manifest["secrets"]}
        self.assertIn("POSTGRES_PASSWORD", secret_keys)
        self.assertIn("SECRET_KEY_BASE", secret_keys)

        # Validate with stack_from_proposal
        stack = stack_from_proposal(manifest, bundle["evidence"])
        self.assertEqual(stack.web_internal_port, 8000)
        self.assertEqual(len(stack.services), 3)


if __name__ == "__main__":
    unittest.main()
