"""Regression tests for staged setup interviews and provider capability records."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from plugins.ai_helper.services import setup_handoff
from services.apps_engine import database_provider_capabilities as providers


class SetupInterviewTests(unittest.TestCase):
    def test_ambiguous_database_names_never_select_panel_provider(self):
        for value in ("postgresql", "postgres", "mariadb", "mysql", "managed"):
            with self.assertRaises(providers.ProviderChoiceRequired):
                providers.canonical_provider("postgresql", value)
        self.assertEqual(providers.canonical_provider("postgresql", "panel_postgres"), "panel_postgres")

    def test_provider_records_expose_managed_dependency_state(self):
        fake_statuses = {
            "postgresql": {"healthy": False, "installed": True, "operation": "idle", "can_toggle": True},
            "mariadb": {"healthy": False, "installed": False, "operation": "idle", "can_toggle": False},
        }
        with patch("dependencies.dependency_manager") as manager:
            manager.is_healthy.return_value = True
            manager.get_status.side_effect = lambda dep_id, **_: fake_statuses[dep_id]
            records = providers.provider_capabilities(force=True)
        postgresql = next(item for item in records if item["kind"] == "postgresql")
        managed = next(item for item in postgresql["providers"] if item["provider_id"] == "panel_postgres")
        self.assertEqual(managed["managed_dependency_state"], "stopped")
        self.assertTrue(managed["can_activate"])
        mariadb = next(item for item in records if item["kind"] == "mariadb")
        self.assertEqual(next(item for item in mariadb["providers"] if item["provider_id"] == "panel_mariadb")["managed_dependency_state"], "not_installed")

    def test_inactive_managed_provider_is_rejected_before_plan_creation(self):
        records = [{"kind": "postgresql", "providers": [{
            "id": "panel_postgres", "dependency_id": "postgresql", "state": "stopped",
        }]}]
        with patch.object(providers, "provider_capabilities", return_value=records):
            with self.assertRaises(providers.ProviderChoiceRequired) as caught:
                providers.require_available("postgresql", "panel_postgres")
        self.assertEqual(caught.exception.state, "stopped")
        self.assertEqual(caught.exception.dependency_id, "postgresql")

    def test_deploy_handoff_does_not_reinterpret_a_database_kind_as_provider(self):
        from fastapi import HTTPException
        from services.apps_engine.reviewed_setup_deploy import _database_provider

        with self.assertRaises(HTTPException):
            _database_provider("postgresql", "postgresql")

    def test_required_inputs_are_sequential_and_secret_values_are_discarded(self):
        result = {"inspection": {"documentation_evidence": {"setup_hints": {"required_inputs": [
            {"name": "admin_username", "label": "Admin Username"},
            {"name": "admin_email", "label": "Admin Email"},
            {"name": "smtp_password", "secret": True},
            {"name": "api_key"},
        ]}}}}
        self.assertEqual([item["name"] for item in setup_handoff.required_setup_inputs(result)], ["admin_username", "admin_email"])
        self.assertTrue(setup_handoff.is_setup_interview_pending(result, "deployment_method: git_build\nadmin_username: owner"))
        self.assertFalse(setup_handoff.is_setup_interview_pending(result, "Setup interview answers:\ndeployment_method: git_build\nadmin_username: owner\nadmin_email: owner@example.com"))

    def test_documented_admin_command_keeps_the_existing_admin_email_handoff(self):
        result = {"inspection": {"documentation_evidence": {"detected_admin_commands": ["registeradmin EMAIL"]}}}
        self.assertEqual(setup_handoff.required_setup_inputs(result)[0]["name"], "admin_email")

    def test_staged_browser_module_defers_chat_callback(self):
        source = (BACKEND / "plugins" / "ai_helper" / "static" / "js" / "modules" / "chat_setup_interview.js").read_text(encoding="utf-8")
        self.assertIn("ai-decision-card", source)
        self.assertIn("ai-setup-submit-btn", source)
        self.assertIn("Confirm Configuration", source)
        self.assertIn("if (typeof this.onComplete === \"function\") this.onComplete", source)
        self.assertIn("SECRET_INPUT", source)
        chat_source = (BACKEND / "plugins" / "ai_helper" / "services" / "chat.py").read_text(encoding="utf-8")
        self.assertIn('if has_compose:', chat_source)
        self.assertIn('Docker Compose Stack', chat_source)
        self.assertIn('Run Docker Image', chat_source)
        self.assertIn('Build from Git Source (Railpack)', chat_source)

    def test_retry_message_preserves_history_answers(self):
        result = {"inspection": {"documentation_evidence": {"setup_hints": {"required_inputs": [
            {"name": "admin_email", "label": "Admin Email", "required": True},
        ]}}}}
        history = [
            "Please analyze this repo: https://github.com/example/app",
            "Here are the options...",
            "Setup interview answers:\ndeployment_method: git_build\nadmin_email: admin@example.com",
            "The reviewed setup plan could not be created...",
        ]
        retry_msg = "Please retry creating the reviewed setup plan with the confirmed settings."
        self.assertFalse(setup_handoff.is_setup_interview_pending(result, retry_msg, history_texts=history))
        self.assertEqual(setup_handoff.missing_setup_inputs(result, retry_msg, history_texts=history), [])

    def test_skipped_inputs_do_not_block_setup_interview(self):
        result = {"inspection": {"documentation_evidence": {"setup_hints": {"required_inputs": [
            {"name": "admin_email", "label": "Admin Email"},
            {"name": "smtp_host", "label": "SMTP Host"},
            {"name": "smtp_port", "label": "SMTP Port"},
        ]}}}}
        self.assertEqual([item["name"] for item in setup_handoff.required_setup_inputs(result)], ["admin_email", "smtp_host", "smtp_port"])
        # If user skips SMTP inputs in interview answers, interview is NOT pending
        answers_with_skips = (
            "Setup interview answers:\n"
            "deployment_method: git_build\n"
            "admin_email: owner@example.com\n"
            "smtp_host: [skip]\n"
            "smtp_port: [skip]"
        )
        self.assertFalse(setup_handoff.is_setup_interview_pending(result, answers_with_skips))
        self.assertEqual(setup_handoff.missing_setup_inputs(result, answers_with_skips), [])

        # If user submits interview without non-critical unlisted inputs, it is also not pending
        answers_minimal = (
            "Setup interview answers:\n"
            "deployment_method: git_build\n"
            "admin_email: owner@example.com"
        )
        self.assertFalse(setup_handoff.is_setup_interview_pending(result, answers_minimal))

    def test_extract_setup_source_handles_registry_image_choice(self):
        from plugins.ai_helper.services.chat import _extract_setup_source
        stype, repo, img = _extract_setup_source("Setup interview answers:\ndeployment_method: registry_image:msgbyte/tianji:latest\nadmin_email: admin@example.com")
        self.assertEqual(stype, "image")
        self.assertEqual(img, "msgbyte/tianji:latest")
        self.assertEqual(repo, "")


if __name__ == "__main__":
    unittest.main()

