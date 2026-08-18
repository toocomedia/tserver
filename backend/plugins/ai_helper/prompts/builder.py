"""
prompts/builder.py — Multi-layered prompt assembler.
Combines immutable core rules + tool rules + result format + action tags +
optional skill injection + active page context + custom admin instructions.
"""
from __future__ import annotations

from plugins.ai_helper.prompts.action_tags import ACTION_TAGS_SPEC
from plugins.ai_helper.prompts.base_rules import FIXED_CORE_SYSTEM_PROMPT
from plugins.ai_helper.prompts.result_format import RESULT_FORMAT_RULES
from plugins.ai_helper.prompts.tool_rules import TOOL_USAGE_RULES


def build_system_prompt(
    context: str | None = None,
    custom_rules: str | None = None,
    include_tools_rules: bool = True,
    skill: str | None = None,
    secrets_allowed: bool = False,
) -> str:
    """
    Assembles the multi-layered system prompt:
    1. Fixed Core Rules (immutable, panel-aware guardrails + anti-monologue + mandatory output)
    2. Tool Usage Rules + Credentials Policy (immutable, read-only boundaries)
    3. Result Format Rules (exact markdown syntax for UI card rendering)
    4. Structured Action Tags (UI copy/apply button triggers)
    5. Skill Injection (task-specific pre-prompt, auto-selected by task_type)
    6. Secrets Consent Notice (dynamic — appended when user granted consent this session)
    7. Active Page / Task Context (dynamic context from wizard, error log, or file editor)
    8. Custom User Rules (configured by the admin in AI Settings)
    """
    sections = [
        FIXED_CORE_SYSTEM_PROMPT.strip(),
    ]

    if include_tools_rules:
        sections.append(TOOL_USAGE_RULES.strip())

    sections.append(RESULT_FORMAT_RULES.strip())
    sections.append(ACTION_TAGS_SPEC.strip())

    # Skill injection — task-specific augmentation
    if skill:
        try:
            from plugins.ai_helper.prompts.skills._base import get_skill
            skill_spec = get_skill(skill)
            if skill_spec and skill_spec.prompt.strip():
                sections.append(skill_spec.prompt.strip())
        except Exception:
            pass  # Skill loading failure is non-fatal

    # Secrets consent notice — appended dynamically
    if secrets_allowed:
        sections.append(
            "### Secrets Consent Active:\n"
            "The user has explicitly granted permission to view credential values for this session. "
            "File reads will return unmasked content where the tool permits. "
            "Still NEVER display private keys or certificates in full — summarize them instead."
        )

    if context and context.strip():
        sections.append(
            f"### Active Page Context & Technical Details:\n{context.strip()}"
        )

    if custom_rules and custom_rules.strip():
        sections.append(
            f"### Custom Server Administrator Instructions:\n{custom_rules.strip()}"
        )

    return "\n\n".join(sections)
