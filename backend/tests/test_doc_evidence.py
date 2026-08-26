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

    def test_collects_at_most_three_bounded_setup_sections(self):
        for index, name in enumerate(("GUIDE.md", "INSTALL.md", "SETUP.md", "README.md"), 1):
            (self.root / name).write_text(
                f"# Project {index}\n## Installation\n" + (f"setup-{index} " * 700),
                encoding="utf-8",
            )

        result = doc_evidence.find_install_instructions(self.root)

        self.assertEqual(len(result["sources"]), 3)
        self.assertLessEqual(sum(len(source["snippet"]) for source in result["sources"]), 6000)
        self.assertTrue(all(source.get("file") and source.get("heading") for source in result["sources"]))

    def test_structured_hints_include_evidence_and_never_secret_values(self):
        (self.root / "GUIDE.md").write_text(
            """# Guide
## Installation
Set the administrator email, then run:
`docker exec web python manage.py createsuperuser --email admin@example.com --password do-not-leak`
Use image example/control-panel:latest.
""",
            encoding="utf-8",
        )
        (self.root / "TEMPLATE.env").write_text(
            "ADMIN_EMAIL=admin@example.com\nADMIN_PASSWORD=do-not-leak\nLICENSE_KEY=license-do-not-leak\nSITE_NAME=Control Panel\n",
            encoding="utf-8",
        )

        env_sample = doc_evidence.parse_expanded_env_samples(self.root)
        result = doc_evidence.find_install_instructions(self.root, env_sample=env_sample)
        hints = result["setup_hints"]

        self.assertIn("admin_email", {item["name"] for item in hints["required_inputs"]})
        self.assertNotIn("site_name", {item["name"] for item in hints["required_inputs"]})
        self.assertIn("ADMIN_PASSWORD", {item["name"] for item in hints["secret_names"]})
        self.assertIn("LICENSE_KEY", {item["name"] for item in hints["secret_names"]})
        self.assertIn("GUIDE.md#Installation", hints["admin_commands"][0]["evidence"])
        self.assertNotIn("do-not-leak", str(result))
        self.assertNotIn("license-do-not-leak", str(result))

    def test_inspection_env_sample_redacts_secret_values(self):
        result = doc_evidence.ai_safe_env_sample({
            "APP_MODE": "production",
            "ADMIN_PASSWORD": "do-not-leak",
            "API_TOKEN": "token-do-not-leak",
        })

        self.assertEqual(result["APP_MODE"], "production")
        self.assertEqual(result["ADMIN_PASSWORD"], "[REDACTED]")
        self.assertEqual(result["API_TOKEN"], "[REDACTED]")

    def test_redacts_inline_script_credentials(self):
        safe = doc_evidence.redact_secret_values(
            "cross-env API_TOKEN=token-do-not-leak npm start --password do-not-leak"
        )

        self.assertNotIn("token-do-not-leak", safe)
        self.assertNotIn("do-not-leak", safe)
        self.assertIn("API_TOKEN=[REDACTED]", safe)

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

    def test_optional_mail_environment_keys_do_not_become_setup_questions(self):
        (self.root / "TEMPLATE.env").write_text(
            "EMAIL_HOST=smtp.example.com\nEMAIL_PORT=465\nEMAIL_HOST_USER=mailer\n"
            "EMAIL_HOST_PASSWORD=do-not-leak\nSERVER_EMAIL=App <noreply@example.com>\n",
            encoding="utf-8",
        )

        result = doc_evidence.find_install_instructions(self.root)
        hints = result["setup_hints"]
        self.assertEqual(hints["required_inputs"], [])
        self.assertIn("EMAIL_HOST_PASSWORD", {item["name"] for item in hints["secret_names"]})
        self.assertNotIn("do-not-leak", str(result))

    def test_explicitly_required_mail_settings_become_setup_questions(self):
        (self.root / "GUIDE.md").write_text(
            "# Setup\n## Configuration\nYou must configure SMTP host, SMTP port, SMTP username, and sender email.\n",
            encoding="utf-8",
        )
        hints = doc_evidence.find_install_instructions(self.root)["setup_hints"]
        self.assertEqual(
            {item["name"] for item in hints["required_inputs"]},
            {"smtp_host", "smtp_port", "smtp_username", "sender_email"},
        )


if __name__ == "__main__":
    unittest.main()
