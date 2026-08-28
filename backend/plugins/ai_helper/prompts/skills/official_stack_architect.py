"""
prompts/skills/official_stack_architect.py — Multi-container stack topology and synthesis skill.
Injected when task_type in ("stack_architect", "stack_template", "compose_stack", "multi_container").
"""
from plugins.ai_helper.prompts.skills._base import SkillSpec

SKILL = SkillSpec(
    name="official_stack_architect",
    task_types=["stack_architect", "stack_template", "compose_stack", "multi_container"],
    prompt="""### Evidence-Driven Multi-Container Architect
Build topology only from the inspected repository, image metadata, and official documentation supplied in context. Never use remembered application templates or invent services, ports, mounts, commands, health endpoints, environment keys, or secrets.

- Use private service-key DNS names for observed internal dependencies.
- Use named volumes only for observed persistent paths; never accept host mounts.
- Declare every secret by name, purpose, and an explicitly allowed generator. Never output a value.
- Preserve workers, datastores, caches, and other services proven by source evidence.
- Invoke `propose_app_spec_plan` with the canonical AppSpec and evidence list. It creates a review plan only.
""",
)
