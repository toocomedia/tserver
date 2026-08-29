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


if __name__ == "__main__":
    unittest.main()
