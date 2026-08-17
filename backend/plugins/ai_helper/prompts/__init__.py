"""
prompts/__init__.py — AI Assistant prompts and instruction package.
"""
from plugins.ai_helper.prompts.base_rules import FIXED_CORE_SYSTEM_PROMPT
from plugins.ai_helper.prompts.builder import build_system_prompt

__all__ = ["FIXED_CORE_SYSTEM_PROMPT", "build_system_prompt"]
