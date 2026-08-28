from pathlib import Path
import sys
import unittest

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from plugins.ai_helper.services.setup_handoff import (
    missing_plan_message,
    needs_stack_correction,
    tool_limit_result,
    is_recommendation_decision_pending,
    needs_documentation_fallback,
    setup_documentation_url_allowed,
)
from plugins.ai_helper.services.visible_output import VisibleOutputFilter, strip_hidden_reasoning


class VisibleOutputTests(unittest.TestCase):
    def test_fragmented_thinking_is_never_emitted(self):
        output = VisibleOutputFilter()
        self.assertEqual(output.push("Answer: <thi"), "Answer: ")
        self.assertEqual(output.push("nk>private plan</think> ready"), " ready")
        self.assertEqual(output.finish(), "")

    def test_model_authored_unlock_action_is_never_emitted(self):
        output = VisibleOutputFilter()
        self.assertEqual(output.push("Before [ACTION:ALLOW_SE"), "Before ")
        self.assertEqual(output.push("CRETS:session] after"), " after")
        self.assertEqual(output.finish(), "")

    def test_model_authored_setup_button_is_never_emitted(self):
        output = VisibleOutputFilter()
        self.assertEqual(output.push("[ACTION:APP_SETUP_PLAN:plan_fake]"), "")
        self.assertEqual(output.finish(), "")

    def test_unclosed_thinking_is_removed_from_history(self):
        self.assertEqual(strip_hidden_reasoning("Visible <think>private"), "Visible ")

    def test_setup_evidence_reads_and_plan_tools_are_bounded(self):
        counts = {"fetch_web_documentation": 1}
        limited = tool_limit_result("app_deploy", "fetch_web_documentation", counts)
        self.assertEqual(limited["status"], "limit_reached")
        self.assertIsNone(tool_limit_result("app_deploy", "fetch_web_documentation", {}))
        self.assertEqual(
            tool_limit_result("app_deploy", "read_website_file", {})["status"],
            "setup_tool_not_available",
        )
        self.assertEqual(
            tool_limit_result("app_deploy", "propose_stack_install", {"propose_stack_install": 1})["status"],
            "limit_reached",
        )
        self.assertEqual(
            tool_limit_result("app_deploy", "propose_stack_install", {"propose_app_install": 1})["status"],
            "limit_reached",
        )
        self.assertIsNone(
            tool_limit_result(
                "app_deploy",
                "propose_stack_install",
                {"propose_app_install": 1},
                allow_stack_correction=True,
            )
        )

    def test_single_app_datastore_rejection_allows_one_stack_correction(self):
        output = {
            "status": "unsupported",
            "message": (
                "This repository needs unsupported single-app datastore services "
                "(clickhouse). Use a restricted stack setup plan with private internal services."
            ),
        }
        self.assertTrue(needs_stack_correction("propose_app_install", output))
        self.assertFalse(needs_stack_correction("propose_stack_install", output))

    def test_missing_plan_reports_the_last_safe_validation_error(self):
        self.assertIn("manifest", missing_plan_message(["Stack manifest is invalid."]))

    def test_missing_plan_without_tool_error_is_not_user_retry_instruction(self):
        message = missing_plan_message([])
        self.assertIn("server-side planning record", message)
        self.assertNotIn("retry the setup chat", message)

    def test_missing_plan_includes_recovery_action_tags(self):
        msg_with_error = missing_plan_message(["Port 3000 collision"])
        self.assertIn("[ACTION:SETUP_RETRY_PLAN]", msg_with_error)
        self.assertIn("[ACTION:SETUP_EDIT_ANSWERS]", msg_with_error)

        msg_without_error = missing_plan_message([])
        self.assertIn("[ACTION:SETUP_RETRY_PLAN]", msg_without_error)
        self.assertIn("[ACTION:SETUP_EDIT_ANSWERS]", msg_without_error)

    def test_setup_recovery_actions_pass_visible_filter(self):
        text = "Plan failed. [ACTION:SETUP_RETRY_PLAN] [ACTION:SETUP_EDIT_ANSWERS] [ACTION:SETUP_RETRY_INSPECTION:github.com/test/repo] [ACTION:SETUP_CHANGE_SOURCE]"
        filtered = strip_hidden_reasoning(text)
        self.assertIn("[ACTION:SETUP_RETRY_PLAN]", filtered)
        self.assertIn("[ACTION:SETUP_EDIT_ANSWERS]", filtered)
        self.assertIn("[ACTION:SETUP_RETRY_INSPECTION:github.com/test/repo]", filtered)
        self.assertIn("[ACTION:SETUP_CHANGE_SOURCE]", filtered)

    def test_recommendation_decision_pending_behavior(self):
        res_with_advice = {
            "status": "ok",
            "official_image_recommendation": {
                "has_official_image": True,
                "recommended_image": "jellyfin/jellyfin:latest",
                "recommended_port": 8096,
            },
        }
        res_without_advice = {"status": "ok"}

        # When advice is present and user message is open, decision is pending
        self.assertTrue(is_recommendation_decision_pending(res_with_advice, "Deploy https://github.com/jellyfin/jellyfin"))
        self.assertTrue(is_recommendation_decision_pending(res_with_advice, "Setup Jellyfin on cc.blagh.co"))

        # When user explicitly chooses, decision is no longer pending
        self.assertFalse(is_recommendation_decision_pending(res_with_advice, "Use option 1"))
        self.assertFalse(is_recommendation_decision_pending(res_with_advice, "Deploy using docker image"))
        self.assertFalse(is_recommendation_decision_pending(res_with_advice, "Build from source"))

        # When no advice was returned, decision is not pending
        self.assertFalse(is_recommendation_decision_pending(res_without_advice, "Deploy https://github.com/myuser/app"))
        self.assertFalse(is_recommendation_decision_pending(None, "Deploy app"))

    def test_structured_admin_email_hint_keeps_existing_input_interview(self):
        result = {
            "status": "ok",
            "inspection": {
                "documentation_evidence": {
                    "setup_hints": {
                        "required_inputs": [{"name": "admin_email", "secret": False}],
                    },
                },
            },
        }

        self.assertTrue(is_recommendation_decision_pending(result, "Set up this app"))
        self.assertFalse(is_recommendation_decision_pending(result, "email: owner@example.com"))

    def test_documentation_fallback_requires_missing_local_evidence_and_verified_url(self):
        complete = {
            "inspection": {
                "repository_url": "https://github.com/example/app.git",
                "documentation_evidence": {
                    "found": True,
                    "sources": [{"file": "README.md", "heading": "Install", "snippet": "setup"}],
                },
            },
        }
        missing = {
            "inspection": {
                "repository_url": "https://github.com/example/app.git",
                "documentation_evidence": {"found": False, "sources": []},
            },
        }

        self.assertFalse(needs_documentation_fallback(complete))
        self.assertTrue(needs_documentation_fallback(missing))
        self.assertTrue(setup_documentation_url_allowed(
            missing, "Deploy this app", "https://github.com/example/app/blob/main/docs/install.md",
        ))
        self.assertTrue(setup_documentation_url_allowed(
            missing, "Docs: https://docs.example.com/install", "https://docs.example.com/install",
        ))
        self.assertFalse(setup_documentation_url_allowed(
            missing, "Deploy this app", "https://third-party.example/tutorial",
        ))


if __name__ == "__main__":
    unittest.main()
