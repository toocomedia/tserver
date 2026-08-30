"""
prompts/skills/__init__.py — Skills package for task-specific system prompt augmentations.
"""
from plugins.ai_helper.prompts.skills._base import SkillSpec, get_skill, register_skill
from plugins.ai_helper.prompts.skills.app_deploy import SKILL as APP_DEPLOY_SKILL
from plugins.ai_helper.prompts.skills.app_inspect import SKILL as APP_INSPECT_SKILL
from plugins.ai_helper.prompts.skills.app_recovery import SKILL as APP_RECOVERY_SKILL
from plugins.ai_helper.prompts.skills.app_redeploy import SKILL as APP_REDEPLOY_SKILL
from plugins.ai_helper.prompts.skills.container_error_resolver import SKILL as CONTAINER_ERROR_RESOLVER_SKILL
from plugins.ai_helper.prompts.skills.database import SKILL as DATABASE_SKILL
from plugins.ai_helper.prompts.skills.error_diag import SKILL as ERROR_DIAG_SKILL
from plugins.ai_helper.prompts.skills.file_explorer import SKILL as FILE_EXPLORER_SKILL
from plugins.ai_helper.prompts.skills.security_audit import SKILL as SECURITY_AUDIT_SKILL

# Register active built-in skills
for skill in (
    APP_DEPLOY_SKILL,
    APP_INSPECT_SKILL,
    APP_REDEPLOY_SKILL,
    APP_RECOVERY_SKILL,
    CONTAINER_ERROR_RESOLVER_SKILL,
    DATABASE_SKILL,
    ERROR_DIAG_SKILL,
    FILE_EXPLORER_SKILL,
    SECURITY_AUDIT_SKILL,
):
    register_skill(skill)

__all__ = ["SkillSpec", "get_skill", "register_skill"]

