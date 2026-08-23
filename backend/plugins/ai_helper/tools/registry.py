"""
tools/registry.py — Central tool registry, dispatcher, and permission enforcement for AI Helper.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Optional
from sqlalchemy.ext.asyncio import AsyncSession

from plugins.ai_helper.permissions import audit
from plugins.ai_helper.permissions.policy import PermissionDeniedError, check_tool_permission
from plugins.ai_helper.tools import app_setup, apps, databases, dns, domains_proxy, files, web_reader

logger = logging.getLogger(__name__)


def _audit_arguments(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Keep tool audit useful without retaining possible secret values from model input."""
    def clean(value: Any, key: str = "") -> Any:
        lowered = key.lower()
        if lowered == "secret_requirements" and isinstance(value, list):
            return [
                {name: clean(item.get(name), name) for name in ("key", "purpose", "rotate", "credential") if name in item}
                for item in value if isinstance(item, dict)
            ]
        if isinstance(value, dict):
            return {str(name): clean(item, str(name)) for name, item in value.items()}
        if any(token in lowered for token in ("password", "secret", "token", "api_key", "private_key", "database_url", "mysql_url", "redis_url", "mongodb_uri")):
            return "[REDACTED]"
        return value
    return clean(arguments) if isinstance(arguments, dict) else {}

TOOL_HANDLERS: Dict[str, Callable[..., Any]] = {
    "get_domains_and_ssl": domains_proxy.get_domains_and_ssl,
    "get_reverse_proxy_routes": domains_proxy.get_reverse_proxy_routes,
    "get_dns_records": dns.get_dns_records,
    "get_apps_overview": apps.get_apps_overview,
    "get_app_logs": apps.get_app_logs,
    "get_databases_overview": databases.get_databases_overview,
    "list_website_directory": files.list_website_directory,
    "read_website_file": files.read_website_file,
    "fetch_web_documentation": web_reader.fetch_web_documentation,
    "inspect_app_source": app_setup.inspect_app_source,
    "search_app_source": app_setup.search_app_source,
    "read_app_source_file": app_setup.read_app_source_file,
    "inspect_official_image": app_setup.inspect_official_image,
    "propose_app_install": app_setup.propose_app_install,
    "propose_container_app_patch": app_setup.propose_container_app_patch,
}



async def execute_tool(
    db: AsyncSession,
    tool_name: str,
    arguments: Dict[str, Any],
    session_id: Optional[str] = None,
    user_id: Optional[int] = None,
    secrets_allowed: bool = False,
) -> Dict[str, Any]:
    """
    Validates permissions and dispatches execution of an AI tool call.
    Returns structured JSON output for the LLM.
    """
    handler = TOOL_HANDLERS.get(tool_name)
    audit_arguments = _audit_arguments(arguments)
    if not handler:
        audit.record_tool_call(tool_name, audit_arguments, "error", session_id, f"Tool '{tool_name}' is not registered.")
        return {
            "status": "error",
            "message": f"Tool '{tool_name}' is not supported by the system.",
        }

    # 1. Evaluate Permission Policy
    try:
        await check_tool_permission(db, tool_name, audit_arguments, session_id=session_id)
    except PermissionDeniedError as exc:
        return {
            "status": "permission_denied",
            "tool": tool_name,
            "message": exc.reason,
            "instruction": "Inform the user that permission for this resource is currently disabled in AI Assistant Settings -> Permissions.",
        }

    # 2. Execute Handler
    try:
        # Pass secrets_allowed to file tools only (others don't use it)
        if tool_name in ("list_website_directory", "read_website_file"):
            result = await handler(db=db, secrets_allowed=secrets_allowed, **arguments)
        elif tool_name in {"propose_container_app_patch", "propose_app_install"}:
            result = await handler(db=db, session_id=session_id, user_id=user_id, **arguments)
        else:
            result = await handler(db=db, **arguments)
        audit.record_tool_call(tool_name, audit_arguments, "success", session_id, "Executed successfully")
        return result
    except Exception as exc:
        logger.error("Error executing AI tool %s with args %s: %s", tool_name, audit_arguments, exc, exc_info=True)
        audit.record_tool_call(tool_name, audit_arguments, "error", session_id, str(exc))
        return {
            "status": "error",
            "tool": tool_name,
            "message": f"Failed to execute {tool_name}: {str(exc)}",
        }
