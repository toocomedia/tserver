"""Tests for the panel AppSpec template system, dynamic documentation, and template export."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import yaml

from services.apps_engine.app_spec import AppSpec, ServiceSpec, VolumeSpec
from services.apps_engine.app_spec_codec import app_spec_from_dict, app_spec_to_dict
from services.apps_engine.security_policy import validate_app_spec
from services.official_stacks.proposal_manifest import stack_from_proposal
from plugins.railpack_apps.documentation_service import get_app_documentation
from plugins.railpack_apps.router_template import _resolve_compose_yaml


class TestPanelTemplateSystem(unittest.IsolatedAsyncioTestCase):
    def test_app_spec_codec_preserves_post_install_message_and_docs_url(self):
        """AppSpec codec must serialize and deserialize post_install_message and docs_url."""
        spec = AppSpec(
            name="testapp",
            display_name="Test Application",
            web_service_name="web",
            web_port=8080,
            services={
                "web": ServiceSpec(
                    name="web",
                    image_reference="test/app:latest",
                    internal_ports=[8080],
                ),
            },
            post_install_message="Initial Setup: docker exec -it {target} ./manage.py registeradmin {admin_email}",
            docs_url="https://example.com/docs",
        )
        data = app_spec_to_dict(spec)
        self.assertEqual(data["post_install_message"], "Initial Setup: docker exec -it {target} ./manage.py registeradmin {admin_email}")
        self.assertEqual(data["docs_url"], "https://example.com/docs")

        validated = validate_app_spec(data)
        self.assertEqual(validated.post_install_message, "Initial Setup: docker exec -it {target} ./manage.py registeradmin {admin_email}")
        self.assertEqual(validated.docs_url, "https://example.com/docs")

    def test_stack_from_proposal_accepts_post_install_message(self):
        """proposal_manifest must accept post_install_message without rejecting it as unknown field."""
        raw_manifest = {
            "name": "shynet_stack",
            "display_name": "Shynet Analytics Stack",
            "web_service": "web",
            "web_port": 8080,
            "startup_order": ["db", "web"],
            "services": [
                {
                    "name": "web",
                    "image": "milesmcc/shynet:latest",
                    "ports": [8080],
                    "depends_on": ["db"],
                },
                {
                    "name": "db",
                    "image": "postgres:16-alpine",
                    "ports": [5432],
                },
            ],
            "post_install_message": "Initial Setup: docker exec -it {target} ./manage.py registeradmin {admin_email}",
        }
        stack = stack_from_proposal(raw_manifest, evidence=["GUIDE.md#Setup"])
        self.assertEqual(stack.post_install_message, "Initial Setup: docker exec -it {target} ./manage.py registeradmin {admin_email}")

    def test_documentation_service_extracts_admin_command_from_post_install_message(self):
        """App documentation must dynamically extract superuser command from post_install_message with zero hardcoding."""
        app = SimpleNamespace(
            id=42,
            container_name="srv-custom-app",
            image_reference="custom/analytics:v1",
            repository_url=None,
            stack_catalog_id=None,
            wordpress_admin_email="riadh@example.com",
            deploy_type="railpack",
        )
        domain = SimpleNamespace(name="analytics.example.com")
        active_snapshot = SimpleNamespace(
            config_json=json.dumps({
                "post_install_message": "Initial Setup: ./manage.py registeradmin {admin_email}",
                "setup_notes": ["Ensure PostgreSQL is running."],
            }),
        )

        doc = get_app_documentation(app, domain, active_snapshot)
        self.assertIn("Ensure PostgreSQL is running.", doc["setup_notes"])
        admin_cmds = [c["command"] for c in doc["admin_commands"]]
        self.assertTrue(any("docker exec -it srv-custom-app ./manage.py registeradmin riadh@example.com" in cmd for cmd in admin_cmds))

    async def test_resolve_compose_yaml_exports_complete_multi_service_stack_with_attached_db(self):
        """Single-container app with attached database must export complete multi-service template with database container."""
        app = SimpleNamespace(
            id=10,
            container_name="srv-shynet-10",
            image_reference="milesmcc/shynet:latest",
            internal_port=8080,
            health_path="/healthz",
            storage_mounts=json.dumps([{"label": "shynet_data", "mount_path": "/var/local/shynet"}]),
            data_volume=None,
            data_mount_path=None,
            env_path=None,
            active_snapshot_id=100,
            pending_snapshot_id=None,
        )
        db_session = AsyncMock()
        
        # Snapshot with post_install_message
        snapshot = SimpleNamespace(
            id=100,
            config_json=json.dumps({
                "post_install_message": "docker exec -it {target} ./manage.py registeradmin {admin_email}",
            }),
        )
        db_session.get.return_value = snapshot

        # Mock attached database
        attached_db = SimpleNamespace(id=1, kind="postgresql", provider="docker")
        with patch("services.container_app_database_service.attachments_for", AsyncMock(return_value=[attached_db])):
            yaml_str, filename = await _resolve_compose_yaml(db_session, app)

        self.assertEqual(filename, "srv-shynet-10-compose.yml")
        self.assertIn("# Initial Setup: docker exec -it {target} ./manage.py registeradmin {admin_email}", yaml_str)
        
        # Parse YAML and verify structure
        parsed = yaml.safe_load(yaml_str)
        self.assertEqual(parsed["version"], "3.8")
        self.assertIn("srv-shynet-10", parsed["services"])
        self.assertIn("db", parsed["services"])

        web = parsed["services"]["srv-shynet-10"]
        self.assertEqual(web["image"], "milesmcc/shynet:latest")
        self.assertEqual(web["ports"], ["8080:8080"])
        self.assertIn("db", web["depends_on"])
        self.assertIn("shynet_data:/var/local/shynet", web["volumes"])

        db_svc = parsed["services"]["db"]
        self.assertEqual(db_svc["image"], "postgres:16-alpine")
        self.assertIn("POSTGRES_DB", db_svc["environment"])
        self.assertIn("volumes", parsed)
        self.assertIn("shynet_data", parsed["volumes"])
        self.assertIn("db_data", parsed["volumes"])


if __name__ == "__main__":
    unittest.main()
