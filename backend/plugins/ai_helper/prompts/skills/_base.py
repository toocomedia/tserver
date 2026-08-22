"""
prompts/skills/_base.py — Base definitions and skill registry for task-specific AI prompt extensions.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class SkillSpec:
    name: str
    task_types: List[str] = field(default_factory=list)
    prompt: str = ""


SKILL_REGISTRY: Dict[str, SkillSpec] = {}


def register_skill(skill: SkillSpec) -> None:
    """Registers a skill specification for its name and aliases."""
    SKILL_REGISTRY[skill.name.lower().strip()] = skill
    for alias in skill.task_types:
        SKILL_REGISTRY[alias.lower().strip()] = skill


def get_skill(name: str) -> Optional[SkillSpec]:
    """Retrieves a skill specification by name or task_type alias."""
    if not name:
        return None
    return SKILL_REGISTRY.get(name.lower().strip())
