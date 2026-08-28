"""Tests for AppSpec Docker Compose schema normalization and template export."""
from __future__ import annotations

import unittest
import yaml

from services.apps_engine.security_policy import validate_app_spec
from services.apps_engine.template_export import app_spec_to_compose_dict, app_spec_to_compose_yaml


class TestTemplateExportAndNormalization(unittest.TestCase):
    def test_compose_keys_are_normalized_into_valid_app_spec(self):
        """Standard Docker Compose keys (image, port, environment, health_path) should not be rejected."""
        raw_spec = {
            "name": "umami",
            "services": {
                "umami": {
                    "image": "ghcr.io/umami-software/umami:latest",
                    "port": 3000,
                    "environment": {"NODE_ENV": "production"},
                    "health_path": "/api/heartbeat",
                },
                "db": {
                    "image": "postgres:15-alpine",
                    "port": 5432,
                    "environment": {"POSTGRES_DB": "umami"},
                },
            },
        }
        spec = validate_app_spec(raw_spec)
        self.assertEqual(spec.name, "umami")
        self.assertEqual(spec.web_service_name, "umami")
        self.assertEqual(spec.web_port, 3000)
        self.assertIn("umami", spec.services)
        self.assertIn("db", spec.services)
        self.assertEqual(spec.services["umami"].image_reference, "ghcr.io/umami-software/umami:latest")
        self.assertEqual(list(spec.services["umami"].internal_ports), [3000])
        self.assertEqual(spec.services["umami"].environment_defaults, {"NODE_ENV": "production"})
        self.assertIsNotNone(spec.services["umami"].health_check)
        self.assertEqual(spec.services["umami"].health_check.http_path, "/api/heartbeat")

    def test_app_spec_to_compose_yaml_renders_valid_yaml(self):
        """Exported Compose template must parse as valid YAML with standard Compose structure."""
        raw_spec = {
            "name": "umami",
            "services": {
                "umami": {
                    "image": "ghcr.io/umami-software/umami:latest",
                    "port": 3000,
                    "environment": {"NODE_ENV": "production"},
                },
                "db": {
                    "image": "postgres:15-alpine",
                    "port": 5432,
                },
            },
        }
        spec = validate_app_spec(raw_spec)
        yaml_content = app_spec_to_compose_yaml(spec)
        self.assertIsInstance(yaml_content, str)
        parsed = yaml.safe_load(yaml_content)
        self.assertEqual(parsed["version"], "3.8")
        self.assertIn("services", parsed)
        self.assertIn("umami", parsed["services"])
        self.assertIn("db", parsed["services"])
        self.assertEqual(parsed["services"]["umami"]["image"], "ghcr.io/umami-software/umami:latest")
        self.assertEqual(parsed["services"]["umami"]["ports"], ["3000:3000"])
        self.assertEqual(parsed["services"]["db"]["expose"], ["5432"])

    def test_direct_apply_token_is_recognized_in_setup_handoff(self):
        from plugins.ai_helper.services.setup_handoff import is_setup_interview_pending
        source_result = {
            "status": "ok",
            "inspection": {
                "compose_info": {"services": [{"name": "web"}, {"name": "db"}]},
                "database_detections": [{"kind": "postgresql"}],
            },
        }
        self.assertTrue(is_setup_interview_pending(source_result, "Please inspect https://github.com/umami-software/umami"))
        self.assertFalse(is_setup_interview_pending(source_result, "Setup interview answers:\nsetup_flow: direct_apply\ndeployment_method: compose_stack"))
        self.assertTrue(is_setup_interview_pending(source_result, "Setup interview answers:\nsetup_flow: check_steps"))

    def test_inspection_cache_and_server_fallback_limits(self):
        from plugins.ai_helper.services.setup_handoff import (
            cache_inspection,
            get_cached_inspection,
            tool_limit_result,
        )
        cache_inspection("sess_123", "https://github.com/umami-software/umami", {"status": "ok", "app": "umami"})
        cached = get_cached_inspection("sess_123", "https://github.com/umami-software/umami")
        self.assertIsNotNone(cached)
        self.assertEqual(cached.get("app"), "umami")

        tool_counts = {"propose_app_spec_plan": 2}
        res = tool_limit_result("app_deploy", "propose_app_spec_plan", tool_counts, is_server_fallback=True)
        self.assertIsNone(res)
