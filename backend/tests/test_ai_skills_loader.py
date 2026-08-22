"""
test_ai_skills_loader.py — Unit tests verifying prompt skills registry and app_deploy skill loader.
"""
from pathlib import Path
import sys
import unittest

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from plugins.ai_helper.prompts import skills


class TestAiSkillsLoader(unittest.TestCase):
    def test_skills_registered(self):
        """Verify that all core skills are registered without circular import errors."""
        registered_skills = ["app_deploy", "app_redeploy", "database", "error_diag", "file_explorer", "security_audit"]
        for skill_name in registered_skills:
            skill = skills.get_skill(skill_name)
            self.assertIsNotNone(skill, f"Skill '{skill_name}' was not found in registry")
            self.assertEqual(skill.name, skill_name)
            self.assertTrue(len(skill.prompt) > 20)

    def test_app_deploy_skill_aliases(self):
        """Verify app_deploy skill can be retrieved via aliases."""
        skill_main = skills.get_skill("app_deploy")
        skill_alias1 = skills.get_skill("app_install")
        skill_alias2 = skills.get_skill("setup_app")

        self.assertIsNotNone(skill_main)
        self.assertEqual(skill_main, skill_alias1)
        self.assertEqual(skill_main, skill_alias2)
        self.assertIn("[ACTION:APP_PLAN:", skill_main.prompt)
        self.assertIn("NEVER ask the user for passwords", skill_main.prompt)

    def test_app_redeploy_skill_aliases(self):
        """Verify app_redeploy skill can be retrieved via aliases."""
        skill_main = skills.get_skill("app_redeploy")
        skill_alias1 = skills.get_skill("redeploy")
        skill_alias2 = skills.get_skill("rebuild")
        skill_alias3 = skills.get_skill("fix_deploy")

        self.assertIsNotNone(skill_main)
        self.assertEqual(skill_main, skill_alias1)
        self.assertEqual(skill_main, skill_alias2)
        self.assertEqual(skill_main, skill_alias3)
        self.assertIn("[ACTION:APP_REDEPLOY:", skill_main.prompt)


if __name__ == "__main__":
    unittest.main()
