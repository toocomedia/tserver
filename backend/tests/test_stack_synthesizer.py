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
            "image_reference": "ghcr.io/plausible/community-edition:v2",
            "branch": "master",
            "runtime": "Elixir",
            "framework": "Phoenix",
            "build_mode": "dockerfile",
            "internal_port": 8000,
            "database_types": ["postgresql", "clickhouse"],
            "env_sample": {
                "BASE_URL": "http://localhost:8000",
                "DISABLE_REGISTRATION": "invite_only",
                "CLICKHOUSE_DB": "plausible_events_db",
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
        self.assertEqual(services["analytics"]["image"], "ghcr.io/plausible/community-edition:v2")
        self.assertIn("db migrate", " ".join(services["analytics"]["command"]))
        self.assertIn("analytics_postgres", services)
        self.assertIn("analytics_clickhouse", services)
        self.assertEqual(services["analytics_clickhouse"]["image"], "clickhouse/clickhouse-server:24.3-alpine")

        # Plausible web depends on postgres & clickhouse
        self.assertIn("analytics_postgres", services["analytics"]["depends_on"])
        self.assertIn("analytics_clickhouse", services["analytics"]["depends_on"])

        # Check URL templates
        self.assertIn("DATABASE_URL", manifest["url_templates"])
        self.assertIn("CLICKHOUSE_DATABASE_URL", manifest["url_templates"])

        # Check dynamic secrets deduction for Elixir/Phoenix
        secret_keys = {sec["key"] for sec in manifest["secrets"]}
        self.assertIn("POSTGRES_PASSWORD", secret_keys)
        self.assertIn("CLICKHOUSE_PASSWORD", secret_keys)
        self.assertIn("SECRET_KEY_BASE", secret_keys)
        self.assertIn("TOTP_VAULT_KEY", secret_keys)
        self.assertEqual(services["analytics_clickhouse"]["environment"]["CLICKHOUSE_DEFAULT_ACCESS_MANAGEMENT"], "1")

        # Validate with stack_from_proposal
        stack = stack_from_proposal(manifest, bundle["evidence"])
        self.assertEqual(stack.web_internal_port, 8000)
        self.assertEqual(len(stack.services), 3)

    def test_synthesize_all_infra_compose_stack(self):
        # When a compose file only defines backing datastores (no web service)
        infra_only_compose = {
            "compose_info": {
                "services": [
                    {"name": "op-ch", "image": "clickhouse/clickhouse-server:24.3-alpine", "internal_ports": [8123]},
                    {"name": "op-db", "image": "postgres:16-alpine", "internal_ports": [5432]},
                    {"name": "op-kv", "image": "redis:7-alpine", "internal_ports": [6379]},
                    {"name": "op-rp", "image": "redpandadata/redpanda:v24.1.2", "internal_ports": [9092]},
                    {"name": "op-rp-console", "image": "redpandadata/console:v2.5.2", "internal_ports": [8080]},
                ]
            },
            "internal_port": 3000,
            "runtime": "Node.js",
            "repository_url": "https://github.com/my-org/analytics-app",
        }
        bundle = synthesize_stack_from_compose(infra_only_compose, domain_name="app.example.com", repo_url="https://github.com/my-org/analytics-app")
        self.assertIsNotNone(bundle)
        manifest = bundle["stack_manifest"]

        # Synthesizer must inject the main application container as the web service (never op-db on 5432)
        self.assertEqual(manifest["web_service"], "analytics-app")
        self.assertEqual(manifest["web_port"], 3000)

        web_svc = next(s for s in manifest["services"] if s["name"] == "analytics-app")
        self.assertEqual(web_svc["image"], "my-org/analytics-app:latest")
        self.assertIn("op-ch", web_svc["depends_on"])
        self.assertIn("op-db", web_svc["depends_on"])
        self.assertIn("op-kv", web_svc["depends_on"])
        self.assertIn("op-rp", web_svc["depends_on"])

        # Redpanda broker must have internal advertise flag configured
        rp_svc = next(s for s in manifest["services"] if s["name"] == "op-rp")
        self.assertIn("--advertise-kafka-addr", " ".join(rp_svc["command"]))
        self.assertIn("op-rp:9092", " ".join(rp_svc["command"]))

        # Redpanda Console must have KAFKA_BROKERS wired to op-rp:9092
        rp_console_svc = next(s for s in manifest["services"] if s["name"] == "op-rp-console")
        self.assertEqual(rp_console_svc["environment"]["KAFKA_BROKERS"], "op-rp:9092")

    def test_framework_secret_deductions_without_sample(self):
        # Laravel
        laravel_insp = {
            "repository_url": "https://github.com/example/laravel-app",
            "runtime": "PHP",
            "framework": "Laravel",
            "database_types": ["postgresql", "redis"],
        }
        bundle = synthesize_stack_from_inspection(laravel_insp, repo_url="https://github.com/example/laravel-app")
        self.assertIsNotNone(bundle)
        keys = {s["key"] for s in bundle["stack_manifest"]["secrets"]}
        self.assertIn("APP_KEY", keys)

        # Rails
        rails_insp = {
            "repository_url": "https://github.com/example/rails-app",
            "runtime": "Ruby",
            "framework": "Rails",
            "database_types": ["postgresql", "redis"],
        }
        bundle = synthesize_stack_from_inspection(rails_insp, repo_url="https://github.com/example/rails-app")
        self.assertIsNotNone(bundle)
        keys = {s["key"] for s in bundle["stack_manifest"]["secrets"]}
        self.assertIn("SECRET_KEY_BASE", keys)

        # Django
        django_insp = {
            "repository_url": "https://github.com/example/django-app",
            "runtime": "Python",
            "framework": "Django",
            "database_types": ["postgresql", "redis"],
        }
        bundle = synthesize_stack_from_inspection(django_insp, repo_url="https://github.com/example/django-app")
        self.assertIsNotNone(bundle)
        keys = {s["key"] for s in bundle["stack_manifest"]["secrets"]}
        self.assertIn("SECRET_KEY", keys)

    def test_branch_tag_resolution(self):
        # Tagged release
        insp_tagged = {
            "repository_url": "https://github.com/example/custom-stack",
            "branch": "v2.5.1",
            "database_types": ["postgresql", "redis"],
        }
        bundle = synthesize_stack_from_inspection(insp_tagged, repo_url="https://github.com/example/custom-stack")
        self.assertEqual(bundle["stack_manifest"]["services"][0]["image"], "example/custom-stack:v2.5.1")

        # Numeric version without leading v
        insp_numeric = {
            "repository_url": "https://github.com/example/custom-stack",
            "branch": "2.5.1",
            "database_types": ["postgresql", "redis"],
        }
        bundle = synthesize_stack_from_inspection(insp_numeric, repo_url="https://github.com/example/custom-stack")
        self.assertEqual(bundle["stack_manifest"]["services"][0]["image"], "example/custom-stack:v2.5.1")

        # Default branch resolves to branch name
        insp_main = {
            "repository_url": "https://github.com/example/custom-stack",
            "branch": "main",
            "database_types": ["postgresql", "redis"],
        }
        bundle = synthesize_stack_from_inspection(insp_main, repo_url="https://github.com/example/custom-stack")
        self.assertEqual(bundle["stack_manifest"]["services"][0]["image"], "example/custom-stack:main")


if __name__ == "__main__":
    unittest.main()
