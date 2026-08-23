"""Bounded App Engine inspection and reviewed-plan handoff policy."""
from __future__ import annotations

from typing import Mapping

from plugins.ai_helper.tools.definitions import APP_SETUP_TOOL_NAMES


_SETUP_TASK_TYPES = frozenset({"app_deploy", "app_install", "setup_app"})
SETUP_TOOL_NAMES = APP_SETUP_TOOL_NAMES
_TOOL_LIMITS = {
    "get_app_engine_capabilities": 1,
    "inspect_app_source": 1,
    "propose_app_install": 1,
    "propose_stack_install": 1,
}
_PROPOSAL_TOOLS = frozenset({"propose_app_install", "propose_stack_install"})


def requires_reviewed_plan(task_type: str | None) -> bool:
    """Whether this chat is an App Engine setup request with a wizard handoff."""
    return (task_type or "").strip().lower() in _SETUP_TASK_TYPES


def tool_limit_result(
    task_type: str | None,
    tool_name: str,
    tool_counts: Mapping[str, int],
    *,
    allow_stack_correction: bool = False,
) -> dict[str, str] | None:
    """Avoid repeated slow evidence reads once setup has enough facts to plan."""
    if not requires_reviewed_plan(task_type):
        return None
    if tool_name not in SETUP_TOOL_NAMES:
        return {
            "status": "setup_tool_not_available",
            "message": (
                "App Engine setup can only use capabilities, one source inspection, "
                "and one reviewed setup proposal. External docs, DNS, file reads, "
                "image probes, and diagnostics are not part of setup."
            ),
        }
    if (
        tool_name in _PROPOSAL_TOOLS
        and any(tool_counts.get(name, 0) for name in _PROPOSAL_TOOLS)
        and not (allow_stack_correction and tool_name == "propose_stack_install")
    ):
        return {
            "status": "limit_reached",
            "message": "This App Engine setup already used its one reviewed setup proposal attempt.",
        }
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
    "The App Engine setup must create exactly one validated server-side review plan "
    "from the capabilities and source inspection already provided. Do not inspect more "
    "sources, fetch docs, check DNS/SSL, reveal or generate secret values, or emit action tags."
)

STACK_CORRECTION_MESSAGE = (
    "The single-app proposal was rejected by server validation because the inspected "
    "source needs private internal stack services. Do not inspect more sources or fetch "
    "documentation. Create one restricted stack review plan now with propose_stack_install "
    "from the existing capabilities and source inspection evidence."
)


def needs_stack_correction(tool_name: str, tool_output: Mapping[str, object]) -> bool:
    """Whether a rejected single-app proposal should get one stack-plan correction."""
    if tool_name != "propose_app_install" or tool_output.get("status") == "ok":
        return False
    message = str(tool_output.get("message") or "").lower()
    return (
        "restricted stack setup plan" in message
        or "unsupported single-app datastore" in message
        or "compose service evidence" in message
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
