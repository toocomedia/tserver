"""Focused proof that repository Compose remains source evidence only."""
import sys
import tempfile
import unittest
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from services.apps_engine.compose_evidence import inspect_compose_evidence


class ComposeEvidenceTests(unittest.TestCase):
    def test_exposes_only_service_image_and_port_facts(self):
        with tempfile.TemporaryDirectory() as temporary:
            compose = Path(temporary) / "compose.yml"
            compose.write_text(
                "services:\n"
                "  db:\n"
                "    image: postgres:16-alpine\n"
                "    environment:\n"
                "      POSTGRES_PASSWORD: must-not-leak\n"
                "  web:\n"
                "    image: vendor/app:v1\n"
                "    ports:\n"
                "      - '127.0.0.1:3000:8000'\n",
                encoding="utf-8",
            )
            result = inspect_compose_evidence(compose)

        self.assertEqual(result["file"], "compose.yml")
        self.assertEqual(result["detected_ports"], [8000])
        self.assertEqual(result["services"], [
            {"name": "db", "internal_ports": [], "image": "postgres:16-alpine"},
            {"name": "web", "internal_ports": [8000], "image": "vendor/app:v1"},
        ])
        self.assertNotIn("must-not-leak", str(result))
        self.assertIn("will not be executed", result["notice"])


if __name__ == "__main__":
    unittest.main()
