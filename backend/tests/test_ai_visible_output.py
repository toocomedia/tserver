"""Focused safeguards for visible AI chat output and setup handoffs."""
from __future__ import annotations

import unittest

from plugins.ai_helper.services.setup_handoff import (
    missing_plan_message,
    needs_stack_correction,
    tool_limit_result,
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
        counts = {"fetch_web_documentation": 2}
        limited = tool_limit_result("app_deploy", "fetch_web_documentation", counts)
        self.assertEqual(limited["status"], "setup_tool_not_available")
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


if __name__ == "__main__":
    unittest.main()
