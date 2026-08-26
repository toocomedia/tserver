"""Bounded App Engine inspection and reviewed-plan handoff policy."""
from __future__ import annotations

import re
from typing import Mapping
from urllib.parse import urlsplit

from plugins.ai_helper.tools.definitions import APP_SETUP_TOOL_NAMES


_SETUP_TASK_TYPES = frozenset({
    "app_deploy", "app_install", "setup_app", "app_build", "build_architect",
    "nixpacks_build", "docker_build", "stack_architect", "stack_template",
    "compose_stack", "multi_container",
})
_DIAGNOSTIC_TASK_TYPES = frozenset({
    "error_diag", "app_redeploy", "error", "debug",
    "container_fix", "error_resolver", "sre_troubleshoot", "auto_healing",
})
SETUP_TOOL_NAMES = APP_SETUP_TOOL_NAMES
APP_DIAGNOSTIC_TOOL_NAMES = frozenset({"get_app_engine_diagnostics", "propose_container_app_patch", "get_app_logs"})
_TOOL_LIMITS = {
    "get_app_engine_capabilities": 2,
    "inspect_app_source": 2,
    "fetch_web_documentation": 1,
    "propose_app_install": 1,
    "propose_stack_install": 1,
}
_PROPOSAL_TOOLS = frozenset({"propose_app_install", "propose_stack_install"})
_SECRET_INPUT_RE = re.compile(r"(?:pass(?:word)?|secret|token|api[_-]?key|private[_-]?key|encryption[_-]?key)", re.I)


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
                "one direct documentation read, and a reviewed setup proposal. "
                "DNS, general file reads, image probes, and diagnostics are not part of setup."
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
    "sources, repeat documentation reads, check DNS/SSL, reveal or generate secret values, "
    "or emit deployment action tags."
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
    
    # Strip URLs so 'github.com' or 'gitlab.com' does not falsely match 'git' keyword
    clean_msg = re.sub(r"https?://\S+", "", (user_message or "").lower())

    # A staged browser interview returns all values in one message. A chosen
    # deployment method alone never completes documented application inputs.
    if required_setup_inputs(setup_source_result):
        return bool(missing_setup_inputs(setup_source_result, user_message))
    if re.search(r"(?im)^\s*deployment_method\s*:\s*\S+", user_message or ""):
        return False

    # If user explicitly said to proceed, deploy, or answered options, don't block
    if any(token in clean_msg for token in (
        "option 1", "option 2", "option 3", "deploy now", "apply now", "confirm", "proceed",
        "use postgres", "use postgresql", "use mysql", "use mariadb", "use sqlite", "use clickhouse",
        "admin email", "my email", "@", "password:", "email:"
    )):
        return False

    inspection = setup_source_result.get("inspection") if isinstance(setup_source_result.get("inspection"), dict) else setup_source_result
    doc_evidence = inspection.get("documentation_evidence") or {}
    has_compose = bool((inspection.get("compose_info") or {}).get("services"))

    # A Compose topology is already the deployment decision. Do not ask the
    # user to select the only valid private stack merely because docs mention
    # an image as well.
    if has_compose:
        return False

    # Preserve the existing reviewed choice when inspection recommends an official image.
    image_advice = setup_source_result.get("official_image_recommendation") or inspection.get("official_image_recommendation")
    if isinstance(image_advice, dict) and image_advice.get("has_official_image") and not any(
        token in clean_msg for token in (
            "docker image", "use docker", "source build", "build from source", "railpack", "dockerfile",
        )
    ):
        return True

    # Check if multiple databases were detected (e.g. Postgres + Clickhouse + Redis, or Postgres + SQLite)
    db_detections = inspection.get("database_detections") or []
    if len(db_detections) > 1 and not any(k in clean_msg for k in ("postgres", "mysql", "mariadb", "sqlite", "clickhouse", "mongo")):
        return True

    # Check if detected docker images or compose services offer build choices
    detected_imgs = doc_evidence.get("detected_docker_images") or []
    if detected_imgs and not any(k in clean_msg for k in ("docker image", "docker", "railpack", "source build", "compose", "stack")):
        return True

    return False


def is_recommendation_decision_pending(
    setup_source_result: Mapping[str, object] | None,
    user_message: str,
) -> bool:
    """Whether setup decision or interview is awaiting user choice before plan generation."""
    return is_setup_interview_pending(setup_source_result, user_message)


def needs_documentation_fallback(setup_source_result: Mapping[str, object] | None) -> bool:
    """Allow one official read only when bounded local setup sections are absent."""
    if not isinstance(setup_source_result, dict):
        return True
    inspection = setup_source_result.get("inspection") if isinstance(setup_source_result.get("inspection"), dict) else setup_source_result
    evidence = inspection.get("documentation_evidence") if isinstance(inspection, dict) else None
    return not (isinstance(evidence, dict) and evidence.get("found") and evidence.get("sources"))


def setup_documentation_url_allowed(
    setup_source_result: Mapping[str, object] | None,
    user_message: str,
    requested_url: str,
) -> bool:
    """Restrict fallback reads to URLs evidenced by inspection or supplied by the user."""
    requested = _normalized_https_url(requested_url)
    if not requested:
        return False
    allowed: list[str] = re.findall(r"https://[^\s<>\"']+", user_message or "", flags=re.IGNORECASE)

    def collect(value: object) -> None:
        if isinstance(value, dict):
            for item in value.values():
                collect(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                collect(item)
        elif isinstance(value, str):
            allowed.extend(re.findall(r"https://[^\s<>\"']+", value, flags=re.IGNORECASE))

    collect(setup_source_result)
    request_parts = urlsplit(requested)
    for candidate in allowed:
        normalized = _normalized_https_url(candidate)
        if not normalized:
            continue
        parts = urlsplit(normalized)
        base_path = parts.path.rstrip("/").removesuffix(".git")
        if request_parts.hostname == parts.hostname and (
            requested == normalized
            or not base_path
            or request_parts.path.rstrip("/").startswith(base_path + "/")
        ):
            return True
    return False


def required_setup_inputs(setup_source_result: Mapping[str, object] | None) -> list[dict[str, str]]:
    """Return documented user-owned fields, excluding every vault-managed field."""
    if not isinstance(setup_source_result, Mapping):
        return []
    inspection = setup_source_result.get("inspection") if isinstance(setup_source_result.get("inspection"), Mapping) else setup_source_result
    evidence = inspection.get("documentation_evidence") if isinstance(inspection, Mapping) else {}
    hints = evidence.get("setup_hints") if isinstance(evidence, Mapping) else {}
    raw_inputs = hints.get("required_inputs") if isinstance(hints, Mapping) else []
    results: list[dict[str, str]] = []
    names: set[str] = set()
    for item in raw_inputs if isinstance(raw_inputs, list) else []:
        if not isinstance(item, Mapping):
            continue
        name = str(item.get("name") or "").strip().lower()
        if not name or name in names or bool(item.get("secret")) or _SECRET_INPUT_RE.search(name):
            continue
        names.add(name)
        results.append({
            "name": name,
            "label": str(item.get("label") or name.replace("_", " ").title()),
            "placeholder": str(item.get("placeholder") or _input_placeholder(name)),
        })
    if not results and isinstance(evidence, Mapping) and evidence.get("detected_admin_commands"):
        results.append({"name": "admin_email", "label": "Admin Email", "placeholder": "admin@example.com"})
    return results


def _input_placeholder(name: str) -> str:
    return {
        "admin_email": "admin@example.com",
        "sender_email": "noreply@example.com",
        "smtp_host": "smtp.example.com",
        "smtp_port": "587",
        "smtp_username": "mailbox username",
    }.get(name, "")


def missing_setup_inputs(setup_source_result: Mapping[str, object] | None, user_message: str) -> list[dict[str, str]]:
    """Check named, combined interview answers without treating a choice as an input."""
    message = user_message or ""
    missing: list[dict[str, str]] = []
    for item in required_setup_inputs(setup_source_result):
        key = re.escape(item["name"])
        found = re.search(rf"(?im)^\s*(?:[-*]\s*)?{key}\s*:\s*(\S.*)$", message)
        if not found and item["name"] == "admin_email":
            found = re.search(r"(?i)\b[\w.+-]+@[\w.-]+\.[A-Z]{2,}\b", message)
        if not found:
            missing.append(item)
    return missing


def _normalized_https_url(value: str) -> str:
    clean = (value or "").strip().rstrip(".,);]")
    parsed = urlsplit(clean)
    if parsed.scheme.lower() != "https" or not parsed.hostname or parsed.username or parsed.password:
        return ""
    return clean


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
