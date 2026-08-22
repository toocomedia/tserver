"""
prompts/skills/__init__.py — Skills package for task-specific system prompt augmentations.
"""
from plugins.ai_helper.prompts.skills._base import SkillSpec, get_skill, register_skill
from plugins.ai_helper.prompts.skills.app_deploy import SKILL as APP_DEPLOY_SKILL
from plugins.ai_helper.prompts.skills.database import SKILL as DATABASE_SKILL
from plugins.ai_helper.prompts.skills.error_diag import SKILL as ERROR_DIAG_SKILL
from plugins.ai_helper.prompts.skills.file_explorer import SKILL as FILE_EXPLORER_SKILL
from plugins.ai_helper.prompts.skills.security_audit import SKILL as SECURITY_AUDIT_SKILL

# Register all built-in skills
for skill in (APP_DEPLOY_SKILL, DATABASE_SKILL, ERROR_DIAG_SKILL, FILE_EXPLORER_SKILL, SECURITY_AUDIT_SKILL):
    register_skill(skill)

__all__ = ["SkillSpec", "get_skill", "register_skill"]
