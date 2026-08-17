"""
prompts/builder.py — Multi-layered prompt assembler.
Combines immutable core rules + tool rules + action tags + active page context + custom admin instructions.
"""
from __future__ import annotations

from plugins.ai_helper.prompts.action_tags import ACTION_TAGS_SPEC
from plugins.ai_helper.prompts.base_rules import FIXED_CORE_SYSTEM_PROMPT
from plugins.ai_helper.prompts.tool_rules import TOOL_USAGE_RULES


def build_system_prompt(
    context: str | None = None,
    custom_rules: str | None = None,
    include_tools_rules: bool = True,
) -> str:
    """
    Assembles the multi-layered system prompt:
    1. Fixed Core Rules (immutable, panel-aware guardrails)
    2. Tool Usage Rules (immutable, read-only boundaries & permissions awareness)
    3. Structured Action Tags (UI copy/apply button triggers)
    4. Active Page / Task Context (dynamic context from calling wizard, error log, or file editor)
    5. Custom User Rules (configured by the admin in AI Settings)
    """
    sections = [
        FIXED_CORE_SYSTEM_PROMPT.strip(),
    ]

    if include_tools_rules:
        sections.append(TOOL_USAGE_RULES.strip())

    sections.append(ACTION_TAGS_SPEC.strip())

    if context and context.strip():
        sections.append(
            f"### Active Page Context & Technical Details:\n{context.strip()}"
        )

    if custom_rules and custom_rules.strip():
        sections.append(
            f"### Custom Server Administrator Instructions:\n{custom_rules.strip()}"
        )

    return "\n\n".join(sections)
