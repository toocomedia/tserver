"""Bounded App Engine inspection and reviewed-plan handoff policy."""
from __future__ import annotations

from typing import Mapping

from plugins.ai_helper.tools.definitions import APP_SETUP_TOOL_NAMES


_SETUP_TASK_TYPES = frozenset({"app_deploy", "app_install", "setup_app"})
_DIAGNOSTIC_TASK_TYPES = frozenset({"error_diag", "app_redeploy", "error", "debug"})
SETUP_TOOL_NAMES = APP_SETUP_TOOL_NAMES
APP_DIAGNOSTIC_TOOL_NAMES = frozenset({"get_app_engine_diagnostics", "propose_container_app_patch", "get_app_logs"})
_TOOL_LIMITS = {
    "get_app_engine_capabilities": 2,
    "inspect_app_source": 2,
    "propose_app_install": 1,
    "propose_stack_install": 1,
}
_PROPOSAL_TOOLS = frozenset({"propose_app_install", "propose_stack_install"})


def requires_reviewed_plan(task_type: str | None) -> bool:
    """Whether this chat is an App Engine setup request with a wizard handoff."""
    return (task_type or "").strip().lower() in _SETUP_TASK_TYPES


def is_diagnostic_task(task_type: str | None, has_app_id: bool = False) -> bool:
    """Whether this chat is an App Engine diagnostic/redeploy task."""
    t = (task_type or "").strip().lower()
    return t in _DIAGNOSTIC_TASK_TYPES or (has_app_id and t in {"general", ""})


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
                "App Engine setup can only use capabilities, source inspection, "
                "and a reviewed setup proposal. External docs, DNS, file reads, "
                "image probes, and diagnostics are not part of setup."
            ),
        }
    proposal_count = sum(tool_counts.get(name, 0) for name in _PROPOSAL_TOOLS)
    if tool_name in _PROPOSAL_TOOLS and proposal_count >= 1:
        if allow_stack_correction and tool_name == "propose_stack_install" and proposal_count < 2:
            return None
        return {
            "status": "limit_reached",
            "message": "This App Engine setup reached its maximum proposal attempts.",
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

PLAN_TOOL_REQUIRED_MESSAGE = (
    "This is an App Engine setup request, not a normal answer. You must call exactly "
    "one reviewed setup planning tool now: propose_app_install for a single app, or "
    "propose_stack_install when source inspection shows Compose services or unsupported "
    "single-app datastores. Do not answer in plain text until the planning tool returns."
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


def is_setup_interview_pending(
    setup_source_result: Mapping[str, object] | None,
    user_message: str,
) -> bool:
    """Whether the app setup requires user input or choice before plan generation."""
    if not isinstance(setup_source_result, dict):
        return False
    
    import re
    # Strip URLs so 'github.com' or 'gitlab.com' does not falsely match 'git' keyword
    clean_msg = re.sub(r"https?://\S+", "", (user_message or "").lower())

    # If user explicitly said to proceed, deploy, or answered options, don't block
    if any(token in clean_msg for token in (
        "option 1", "option 2", "option 3", "deploy now", "apply now", "confirm", "proceed",
        "use postgres", "use postgresql", "use mysql", "use mariadb", "use sqlite", "use clickhouse",
        "admin email", "my email", "@", "password:", "email:"
    )):
        return False

    inspection = setup_source_result.get("inspection") if isinstance(setup_source_result.get("inspection"), dict) else setup_source_result
    doc_evidence = inspection.get("documentation_evidence") or {}
    
    # 1. Check if admin setup commands exist in doc_evidence (e.g. registeradmin, createsuperuser)
    if doc_evidence.get("detected_admin_commands") and "@" not in clean_msg and "admin" not in clean_msg:
        return True

    # 2. Check if multiple databases were detected (e.g. Postgres + Clickhouse + Redis, or Postgres + SQLite)
    db_detections = inspection.get("database_detections") or []
    if len(db_detections) > 1 and not any(k in clean_msg for k in ("postgres", "mysql", "mariadb", "sqlite", "clickhouse", "mongo")):
        return True

    # 3. Check if detected docker images or compose services offer build choices
    detected_imgs = doc_evidence.get("detected_docker_images") or []
    has_compose = bool((inspection.get("compose_info") or {}).get("services"))
    if (detected_imgs or has_compose) and not any(k in clean_msg for k in ("docker image", "docker", "railpack", "source build", "compose", "stack")):
        return True

    return False


def is_recommendation_decision_pending(
    setup_source_result: Mapping[str, object] | None,
    user_message: str,
) -> bool:
    """Whether setup decision or interview is awaiting user choice before plan generation."""
    return is_setup_interview_pending(setup_source_result, user_message)


def missing_plan_message(errors: list[str]) -> str:
    """Give the user the final actionable plan error without exposing tool internals."""
    if errors:
        return (
            "The reviewed setup plan could not be created: "
            f"{errors[-1][:420]} Nothing was applied."
        )
    return (
        "No reviewed setup plan was created, so nothing was applied. "
        "The provider stopped before creating the required server-side planning record."
    )
