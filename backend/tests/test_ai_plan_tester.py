"""
tests/test_ai_plan_tester.py — Unit and integration tests for the AI Plan & Spec Dev Tester.
"""
import unittest
from pathlib import Path

from tools.ai_plan_tester.catalog import find_app_by_slug, get_catalog, resolve_app_target
from tools.ai_plan_tester.reporter import format_app_report_text, format_scorecard_table, save_app_output_files
from tools.ai_plan_tester.runner import RunResult, ToolActivityRecord
from tools.ai_plan_tester.validator import validate_plan_payload


class AiPlanTesterTests(unittest.IsolatedAsyncioTestCase):

    def test_catalog_resolution(self):
        catalog = get_catalog()
        self.assertGreaterEqual(len(catalog), 10)

        vault = find_app_by_slug("vaultwarden")
        self.assertIsNotNone(vault)
        self.assertEqual(vault.expected_port, 80)
        self.assertEqual(vault.source_type, "image")

        shynet = find_app_by_slug("shynet")
        self.assertIsNotNone(shynet)
        self.assertTrue(shynet.is_multi_container)
        self.assertEqual(shynet.expected_database, "postgresql")

        # Custom Git target resolution
        custom_git = resolve_app_target("https://github.com/example/my-cool-app.git")
        self.assertEqual(custom_git.source_type, "git")
        self.assertEqual(custom_git.slug, "my-cool-app")

        # Custom Docker image target resolution
        custom_img = resolve_app_target("myregistry.io/org/app:v1.2")
        self.assertEqual(custom_img.source_type, "image")
        self.assertEqual(custom_img.slug, "app-v1-2")

    def test_validator_detects_prohibited_mounts(self):
        app = find_app_by_slug("vaultwarden")
        bad_plan = {
            "action_type": "app_install",
            "payload": {
                "services": {
                    "app": {
                        "image_reference": "vaultwarden/server:latest",
                        "volumes": [{"container_mount_path": "/var/run/docker.sock"}],
                    }
                },
                "internal_port": 80,
            }
        }
        res = validate_plan_payload(bad_plan, app)
        self.assertFalse(res.is_valid)
        self.assertEqual(res.status, "FAIL")
        err_fields = [i.field for i in res.issues if i.severity == "ERROR"]
        self.assertIn("services.app.volumes", err_fields)
        self.assertTrue(any("FIX HERE" in i.fix_advice or "Remove host mount" in i.fix_advice for i in res.issues))

    def test_validator_detects_privileged_mode(self):
        app = find_app_by_slug("vaultwarden")
        bad_plan = {
            "action_type": "app_install",
            "payload": {
                "services": {
                    "app": {
                        "image_reference": "vaultwarden/server:latest",
                        "privileged": True,
                    }
                },
                "internal_port": 80,
            }
        }
        res = validate_plan_payload(bad_plan, app)
        self.assertFalse(res.is_valid)
        self.assertEqual(res.status, "FAIL")
        err_fields = [i.field for i in res.issues if i.severity == "ERROR"]
        self.assertIn("services.app.privileged", err_fields)

    def test_validator_detects_missing_database_url(self):
        app = find_app_by_slug("umami")  # Expects postgresql
        plan = {
            "action_type": "app_install",
            "payload": {
                "services": {"web": {"image_reference": "umami:latest"}},
                "internal_port": 3000,
                "environment_values": {"NODE_ENV": "production"},
            }
        }
        res = validate_plan_payload(plan, app)
        # Should be a warning (not hard fatal error) but flagged with advice
        warns = [i for i in res.issues if i.severity == "WARNING" and i.field == "environment_values"]
        self.assertTrue(len(warns) > 0)
        self.assertIn("DATABASE_URL", warns[0].message)
        self.assertIn("DATABASE_URL", warns[0].fix_advice)

    def test_validator_exports_clean_compose_yaml(self):
        app = find_app_by_slug("vaultwarden")
        valid_plan = {
            "action_type": "app_install",
            "payload": {
                "image_reference": "vaultwarden/server:latest",
                "internal_port": 80,
                "environment_values": {"SIGNUPS_ALLOWED": "false"},
            }
        }
        res = validate_plan_payload(valid_plan, app)
        self.assertTrue(res.is_valid)
        self.assertEqual(res.status, "PASS")
        self.assertIn("vaultwarden/server:latest", res.compose_yaml)
        self.assertIn("80:80", res.compose_yaml)
        self.assertIn("SIGNUPS_ALLOWED", res.compose_yaml)

    def test_reporter_formatting(self):
        app = find_app_by_slug("vaultwarden")
        val = validate_plan_payload({"payload": {"internal_port": 80}}, app)
        result = RunResult(
            app=app,
            provider_name="Test Provider",
            model_name="test-model",
            activities=[
                ToolActivityRecord("inspect_app_source", "done", "Local inspect", "vaultwarden/server", 10),
            ],
            turn1_prompt="Analyze vaultwarden",
            turn1_response="Inspected vaultwarden successfully.",
            plan_data={"action_type": "app_install", "payload": {"internal_port": 80}},
            validation=val,
            duration_ms=45,
        )
        report_text = format_app_report_text(result)
        self.assertIn("AI PLAN & SPEC TEST REPORT: VAULTWARDEN", report_text)
        self.assertIn("[1. AI SEARCH & TOOL EXECUTION TRACE]", report_text)
        self.assertIn("[2. GENERATED PLAN (JSON)]", report_text)
        self.assertIn("[3. EXPORTED DOCKER COMPOSE (YAML)]", report_text)
        self.assertIn("[4. VALIDATION RESULTS & ISSUES TO FIX]", report_text)

        scorecard = format_scorecard_table([result])
        self.assertIn("vaultwarden", scorecard)
        self.assertIn("PASS", scorecard)
        self.assertIn("TOTAL APPS: 1", scorecard)

    def test_dev_tester_template_compiles(self):
        from jinja2 import ChoiceLoader, Environment, FileSystemLoader
        backend_dir = Path(__file__).resolve().parents[1]
        env = Environment(loader=ChoiceLoader([
            FileSystemLoader(str(backend_dir / "templates")),
            FileSystemLoader(str(backend_dir / "plugins" / "ai_helper" / "templates")),
        ]))
        tmpl = env.get_template("ai_spec_tester.html")
        self.assertIsNotNone(tmpl)

    async def test_dev_tester_router_endpoints(self):
        import json
        from database import AsyncSessionLocal
        from plugins.ai_helper.router_dev_tester import get_catalog_api, run_test_api, RunTestRequest

        cat = await get_catalog_api()
        self.assertGreaterEqual(len(cat["catalog"]), 10)

        async with AsyncSessionLocal() as db:
            req = RunTestRequest(app_slug="vaultwarden", offline=True)
            res = await run_test_api(req, db)
            self.assertEqual(res.status_code, 200)
            data = json.loads(res.body.decode("utf-8"))
            self.assertEqual(data["status"], "ok")
            self.assertEqual(data["app"]["slug"], "vaultwarden")
            self.assertEqual(data["validation"]["verdict"], "PASS")
            self.assertTrue(len(data["compose_yaml"]) > 0)
            self.assertTrue(len(data["report_text"]) > 0)


if __name__ == "__main__":
    unittest.main()

