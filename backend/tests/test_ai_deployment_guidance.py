import sys
import unittest
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from plugins.ai_helper.prompts.skills.app_deploy import SKILL as APP_DEPLOY_SKILL


class TestAiDeploymentGuidance(unittest.TestCase):
    def test_app_deploy_prompt_contains_build_tradeoffs_and_panel_db(self):
        """Prompt instructs the AI to explain build trade-offs and prioritize panel-managed databases."""
        prompt_text = APP_DEPLOY_SKILL.prompt

        # Verify build from source vs pre-built trade-offs are present
        self.assertIn("Build from Source vs Pre-built Image Trade-off", prompt_text)
        self.assertIn("compiling complex applications from source can be difficult", prompt_text)
        self.assertIn("(Recommended)", prompt_text)

        # Verify panel database prioritization to save VPS RAM
        self.assertIn("Managed Database Prioritization", prompt_text)
        self.assertIn("panel_postgres", prompt_text)
        self.assertIn("saves VPS RAM", prompt_text)

        # Verify structured options format
        self.assertIn("[OPTION:Option 1 (Recommended): Official Docker Image with Panel Database", prompt_text)
        self.assertIn("[OPTION:Option 2: Multi-Container Compose Stack", prompt_text)
        self.assertIn("[OPTION:Option 3: Build from Git Source", prompt_text)

    def test_inspection_extracts_official_image_from_compose(self):
        """When a repository contains docker-compose.yml with pre-built image, inspection surfaces it as recommendation."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            (tmppath / "docker-compose.yml").write_text(
                "version: '3'\n"
                "services:\n"
                "  shynet:\n"
                "    image: milesmcc/shynet:latest\n"
                "    expose:\n"
                "      - 8080\n"
                "  db:\n"
                "    image: postgres:13-alpine\n",
                encoding="utf-8",
            )
            (tmppath / "Dockerfile").write_text("FROM python:3.10\n", encoding="utf-8")

            from services.container_app_inspection_service import (
                _parse_compose_details,
            )
            compose_info = _parse_compose_details(tmppath)
            self.assertIn("services", compose_info)
            self.assertEqual(len(compose_info["services"]), 2)

            # Test port extraction from expose
            ports = compose_info["services"][0].get("internal_ports")
            self.assertIn(8080, ports)

            # Test that official image can be derived
            svc_images = [s.get("image") for s in compose_info["services"]]
            self.assertIn("milesmcc/shynet:latest", svc_images)

    def test_image_profiles_and_tag_normalization(self):
        """Image inspect recommendations detect Plausible, Shynet, and Ghost profiles with databases and envs."""
        from services.container_app_image_inspect_service import _without_tag, _recommendations

        # Tag stripping and normalization
        self.assertEqual(_without_tag("plausible/analytics:latest"), "plausible/analytics")
        self.assertEqual(_without_tag("docker.io/plausible/analytics:v2"), "plausible/analytics")
        self.assertEqual(_without_tag("docker.io/library/ghost:alpine"), "ghost")
        self.assertEqual(_without_tag("milesmcc/shynet:latest"), "milesmcc/shynet")

        # Plausible recommendations
        rec_plausible = _recommendations("plausible/analytics:latest", ["8000"], [], None)
        self.assertEqual(rec_plausible["internal_port"], 8000)
        self.assertIn("postgresql", rec_plausible["database_types"])
        self.assertIn("clickhouse", rec_plausible["database_types"])
        self.assertTrue(rec_plausible["requires_multi_container"])
        self.assertIn("SECRET_KEY_BASE", rec_plausible["required_environment_names"])

        # Shynet recommendations
        rec_shynet = _recommendations("milesmcc/shynet:latest", ["8080"], [], None)
        self.assertEqual(rec_shynet["internal_port"], 8080)
        self.assertIn("postgresql", rec_shynet["database_types"])
        self.assertIn("SECRET_KEY", rec_shynet["required_environment_names"])

        # Ghost recommendations
        rec_ghost = _recommendations("ghost:alpine", ["2368"], [], None)
        self.assertEqual(rec_ghost["internal_port"], 2368)
        self.assertIn("mariadb", rec_ghost["database_types"])


if __name__ == "__main__":
    unittest.main()
