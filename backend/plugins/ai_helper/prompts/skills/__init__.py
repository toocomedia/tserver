"""
prompts/skills/__init__.py — Skills package for task-specific system prompt augmentations.
"""
from plugins.ai_helper.prompts.skills._base import SkillSpec, get_skill

__all__ = ["SkillSpec", "get_skill"]
