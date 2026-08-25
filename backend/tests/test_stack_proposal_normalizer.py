import sys
import unittest
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from services.official_stacks.proposal_normalizer import normalize_stack_proposal_manifest
from services.official_stacks.proposal_manifest import stack_from_proposal


class TestStackProposalNormalizer(unittest.TestCase):
    def test_normalize_top_level_and_service_aliases(self):
        raw_manifest = {
            "catalog_id": "test_app",
            "title": "Test App Display",
            "vendor": "Test Vendor",
            "repositories": ["https://github.com/example/test-app"],
            "version": "v1.0.0",
            "services": [
                {
                    "service_name": "db",
                    "container_image": "postgres:16-alpine",
                    "port": 5432,
                    "env": {"POSTGRES_USER": "postgres"},
                    "volume": [{"target": "/var/lib/postgresql/data", "label": "db-data"}],
                    "health_check": {"probe_type": "command", "cmd": ["pg_isready", "-U", "postgres"]},
                },
                {
                    "service_name": "web",
                    "container_image": "example/web:v1.0.0",
                    "port": 8000,
                    "dependencies": ["db"],
                    "env": {"APP_ENV": "production"},
                },
            ],
            "order": ["db", "web"],
            "web_service_name": "web",
            "port": 8000,
            "health_path": "/health",
            "allowed_settings": ["BASE_URL"],
            "environment": {"BASE_URL": "https://example.com"},
            "required_secrets": [
                {"key": "DB_PASS", "purpose": "Password", "generator": "password", "service": "db", "environment": "POSTGRES_PASSWORD"}
            ],
            "documentation_url": "https://docs.example.com",
        }

        normalized = normalize_stack_proposal_manifest(raw_manifest)
        self.assertEqual(normalized["name"], "test_app")
        self.assertEqual(normalized["display_name"], "Test App Display")
        self.assertEqual(normalized["vendor_name"], "Test Vendor")
        self.assertEqual(normalized["source_repositories"], ["https://github.com/example/test-app"])
        self.assertEqual(normalized["startup_order"], ["db", "web"])
        self.assertEqual(normalized["web_service"], "web")
        self.assertEqual(normalized["web_port"], 8000)
        self.assertEqual(normalized["web_health_path"], "/health")
        self.assertEqual(normalized["allowed_nonsecret_settings"], ["BASE_URL"])
        self.assertEqual(normalized["default_environment"], {"BASE_URL": "https://example.com"})
        self.assertEqual(normalized["docs_url"], "https://docs.example.com")

        # Verify service normalization
        db_svc = normalized["services"][0]
        self.assertEqual(db_svc["name"], "db")
        self.assertEqual(db_svc["image"], "postgres:16-alpine")
        self.assertEqual(db_svc["ports"], [5432])
        self.assertEqual(db_svc["environment"], {"POSTGRES_USER": "postgres"})
        self.assertEqual(db_svc["volumes"][0]["mount_path"], "/var/lib/postgresql/data")
        self.assertEqual(db_svc["volumes"][0]["name"], "db-data")
        self.assertEqual(db_svc["health"]["type"], "command")
        self.assertEqual(db_svc["health"]["command"], ["pg_isready", "-U", "postgres"])

        web_svc = normalized["services"][1]
        self.assertEqual(web_svc["name"], "web")
        self.assertEqual(web_svc["depends_on"], ["db"])
        self.assertEqual(web_svc["ports"], [8000])

        # Verify it can be cleanly parsed by stack_from_proposal with evidence
        stack = stack_from_proposal(raw_manifest, ["source evidence from repo"])
        self.assertEqual(stack.catalog_id, "test_app")
        self.assertEqual(stack.web_internal_port, 8000)
        self.assertIn("db", stack.services)
        self.assertIn("web", stack.services)

    def test_normalize_dict_services_and_ports_list(self):
        raw_manifest = {
            "name": "dict_services_app",
            "services": {
                "db": {"image": "postgres:16-alpine", "ports": [5432]},
                "web": {"image": "app:latest", "ports": [3000], "depends_on": ["db"]},
            },
            "web_service": "web",
            "ports": [3000],
            "startup_order": ["db", "web"],
        }
        normalized = normalize_stack_proposal_manifest(raw_manifest)
        self.assertIsInstance(normalized["services"], list)
        self.assertEqual(len(normalized["services"]), 2)
        self.assertEqual(normalized["web_port"], 3000)

    def test_reject_dangerous_fields(self):
        dangerous_manifest = {
            "name": "insecure_app",
            "privileged": True,
            "services": [{"name": "web", "image": "nginx:latest", "ports": [80]}],
            "web_service": "web",
            "web_port": 80,
            "startup_order": ["web"],
        }
        with self.assertRaises(ValueError):
            stack_from_proposal(dangerous_manifest, ["evidence"])

    def test_synthesizer_dynamic_clickhouse_db_naming(self):
        from services.official_stacks.stack_synthesizer import synthesize_stack_from_compose
        
        # Test Openpanel ClickHouse synthesis
        openpanel_compose = {
            "compose_info": {
                "services": [
                    {"name": "op-ch", "image": "clickhouse/clickhouse-server:24.3-alpine", "internal_ports": [8123]},
                    {"name": "op-rp", "image": "redpandadata/redpanda:v24.1.2", "internal_ports": [9092]},
                    {"name": "op-rp-console", "image": "redpandadata/console:v2.5.2", "internal_ports": [8080]},
                    {"name": "openpanel", "image": "openpanel/openpanel:latest", "internal_ports": [3000]},
                ]
            },
            "repository_url": "https://github.com/openpanel-dev/openpanel",
        }
        bundle = synthesize_stack_from_compose(openpanel_compose, domain_name="blagh.co", repo_url="https://github.com/openpanel-dev/openpanel")
        self.assertIsNotNone(bundle)
        manifest = bundle["stack_manifest"]
        
        # Verify ClickHouse DB is 'openpanel' and NOT hardcoded 'plausible_events_db'
        ch_svc = next(s for s in manifest["services"] if s["name"] == "op-ch")
        self.assertEqual(ch_svc["environment"]["CLICKHOUSE_DB"], "openpanel")
        self.assertIn("/openpanel", manifest["url_templates"]["CLICKHOUSE_DATABASE_URL"])
        
        # Verify Redpanda Console has KAFKA_BROKERS auto-wired to op-rp:9092
        rp_console_svc = next(s for s in manifest["services"] if s["name"] == "op-rp-console")
        self.assertEqual(rp_console_svc["environment"].get("KAFKA_BROKERS"), "op-rp:9092")

        # Verify Web Service is 'openpanel' on port 3000 and NEVER op-rp (broker)
        self.assertEqual(manifest["web_service"], "openpanel")
        self.assertEqual(manifest["web_port"], 3000)

    def test_synthesizer_plausible_clickhouse_db_naming(self):
        from services.official_stacks.stack_synthesizer import synthesize_stack_from_compose
        plausible_compose = {
            "compose_info": {
                "services": [
                    {"name": "plausible_events_db", "image": "clickhouse/clickhouse-server:24.3-alpine", "internal_ports": [8123]},
                    {"name": "plausible", "image": "ghcr.io/plausible/community-edition:v2", "internal_ports": [8000]},
                ]
            },
            "repository_url": "https://github.com/plausible/analytics",
        }
        bundle = synthesize_stack_from_compose(plausible_compose, domain_name="stats.co", repo_url="https://github.com/plausible/analytics")
        self.assertIsNotNone(bundle)
        manifest = bundle["stack_manifest"]
        ch_svc = next(s for s in manifest["services"] if "plausible_events_db" in s["name"])
        self.assertEqual(ch_svc["environment"]["CLICKHOUSE_DB"], "plausible_events_db")


if __name__ == "__main__":
    unittest.main()
