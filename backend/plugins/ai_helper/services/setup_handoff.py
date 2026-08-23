"""Bounded App Engine inspection and reviewed-plan handoff policy."""
from __future__ import annotations

from typing import Mapping


_SETUP_TASK_TYPES = frozenset({"app_deploy", "app_install", "setup_app"})
_TOOL_LIMITS = {
    "get_app_engine_capabilities": 1,
    "inspect_app_source": 1,
    "inspect_official_image": 1,
    "fetch_web_documentation": 2,
}


def requires_reviewed_plan(task_type: str | None) -> bool:
    """Whether this chat is an App Engine setup request with a wizard handoff."""
    return (task_type or "").strip().lower() in _SETUP_TASK_TYPES


def tool_limit_result(
    task_type: str | None,
    tool_name: str,
    tool_counts: Mapping[str, int],
) -> dict[str, str] | None:
    """Avoid repeated slow evidence reads once setup has enough facts to plan."""
    if not requires_reviewed_plan(task_type):
        return None
    limit = _TOOL_LIMITS.get(tool_name)
    if limit is None or tool_counts.get(tool_name, 0) < limit:
        return None
    return {
        "status": "limit_reached",
        "message": (
            "This App Engine setup already has the allowed evidence for "
            f"{tool_name}. Use the collected evidence to create the reviewed setup plan now."
        ),
    }


PLAN_REQUIRED_MESSAGE = (
    "Do not produce a final answer yet. The App Engine setup must end with exactly one "
    "validated server-side review plan. Use propose_app_install or propose_stack_install now. "
    "Do not inspect more sources, reveal or generate secret values, or emit action tags."
)

def missing_plan_message(errors: list[str]) -> str:
    """Give the user the final actionable plan error without exposing tool internals."""
    if errors:
        return (
            "The reviewed setup plan could not be created: "
            f"{errors[-1][:420]} Nothing was applied."
        )
    return (
        "No reviewed setup plan was created, so nothing was applied. "
        "The provider did not call the required planning tool; retry the setup chat."
    )
