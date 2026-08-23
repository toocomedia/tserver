"""Focused safeguards for visible AI chat output and setup handoffs."""
from __future__ import annotations

import unittest

from plugins.ai_helper.services.setup_handoff import tool_limit_result
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

    def test_setup_evidence_reads_are_bounded_but_plan_tool_is_not(self):
        counts = {"fetch_web_documentation": 2}
        limited = tool_limit_result("app_deploy", "fetch_web_documentation", counts)
        self.assertEqual(limited["status"], "limit_reached")
        self.assertIsNone(tool_limit_result("app_deploy", "propose_stack_install", counts))


if __name__ == "__main__":
    unittest.main()
