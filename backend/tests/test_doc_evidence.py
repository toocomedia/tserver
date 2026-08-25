"""
test_doc_evidence.py — Unit tests for markdown documentation discovery and expanded env templates.
"""
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from services.apps_engine import doc_evidence
from services import container_app_inspection_service


class TestDocEvidence(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.root = Path(self.temp_dir)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_find_install_instructions_in_guide_md(self):
        guide_content = """# Usage Guide
## Table of Contents
- [Installation](#installation)

## Installation
1. Pull the latest version of Shynet using `docker run --env-file=.env milesmcc/shynet:latest`.
2. Configure database settings.
3. Create an admin user by running `docker run --env-file=.env milesmcc/shynet:latest ./manage.py registeradmin admin@example.com`.
4. Launch your webserver on port 8080.
"""
        (self.root / "GUIDE.md").write_text(guide_content, encoding="utf-8")
        (self.root / "README.md").write_text("# Project\nSome other content.", encoding="utf-8")

        result = doc_evidence.find_install_instructions(self.root)
        self.assertTrue(result.get("found"))
        self.assertEqual(result.get("file"), "GUIDE.md")
        self.assertIn("manage.py registeradmin", result.get("snippet", ""))
        self.assertTrue(len(result.get("detected_admin_commands", [])) > 0)
        self.assertIn("registeradmin", result["detected_admin_commands"][0])

    def test_find_install_instructions_in_custom_named_md(self):
        setup_content = """# Custom Setup
### Getting Started
To install this application, run:
```bash
docker run -p 3000:3000 myapp:latest
```
"""
        (self.root / "shynet_setup_notes.md").write_text(setup_content, encoding="utf-8")

        result = doc_evidence.find_install_instructions(self.root)
        self.assertTrue(result.get("found"))
        self.assertEqual(result.get("file"), "shynet_setup_notes.md")
        self.assertIn("docker run -p 3000:3000", result.get("snippet", ""))

    def test_snippet_character_cap(self):
        large_content = "# Guide\n## Installation\n" + ("x" * 10000)
        (self.root / "INSTALL.md").write_text(large_content, encoding="utf-8")

        result = doc_evidence.find_install_instructions(self.root)
        self.assertTrue(result.get("found"))
        self.assertLessEqual(len(result.get("snippet", "")), doc_evidence.MAX_DOC_SNIPPET_CHARS + 50)

    def test_parse_expanded_env_samples_template_env(self):
        template_env = """# Database configuration
DB_NAME=shynet_db
DB_USER=shynet_db_user
DB_PASSWORD=shynet_db_user_password
DB_HOST=db
DB_PORT=5432

# General settings
DJANGO_SECRET_KEY=secret_sample_key
ALLOWED_HOSTS=example.com
"""
        (self.root / "TEMPLATE.env").write_text(template_env, encoding="utf-8")

        result = doc_evidence.parse_expanded_env_samples(self.root)
        self.assertEqual(result.get("DB_NAME"), "shynet_db")
        self.assertEqual(result.get("DB_PORT"), "5432")
        self.assertEqual(result.get("DJANGO_SECRET_KEY"), "secret_sample_key")
        self.assertEqual(result.get("ALLOWED_HOSTS"), "example.com")


if __name__ == "__main__":
    unittest.main()
