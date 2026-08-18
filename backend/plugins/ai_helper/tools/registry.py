"""
tools/registry.py — Central tool registry, dispatcher, and permission enforcement for AI Helper.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Optional
from sqlalchemy.ext.asyncio import AsyncSession

from plugins.ai_helper.permissions import audit
from plugins.ai_helper.permissions.policy import PermissionDeniedError, check_tool_permission
from plugins.ai_helper.tools import apps, databases, dns, domains_proxy, files

logger = logging.getLogger(__name__)

TOOL_HANDLERS: Dict[str, Callable[..., Any]] = {
    "get_domains_and_ssl": domains_proxy.get_domains_and_ssl,
    "get_reverse_proxy_routes": domains_proxy.get_reverse_proxy_routes,
    "get_dns_records": dns.get_dns_records,
    "get_apps_overview": apps.get_apps_overview,
    "get_app_logs": apps.get_app_logs,
    "get_databases_overview": databases.get_databases_overview,
    "list_website_directory": files.list_website_directory,
    "read_website_file": files.read_website_file,
}


async def execute_tool(
    db: AsyncSession,
    tool_name: str,
    arguments: Dict[str, Any],
    session_id: Optional[str] = None,
    secrets_allowed: bool = False,
) -> Dict[str, Any]:
    """
    Validates permissions and dispatches execution of an AI tool call.
    Returns structured JSON output for the LLM.
    """
    handler = TOOL_HANDLERS.get(tool_name)
    if not handler:
        audit.record_tool_call(tool_name, arguments, "error", session_id, f"Tool '{tool_name}' is not registered.")
        return {
            "status": "error",
            "message": f"Tool '{tool_name}' is not supported by the system.",
        }

    # 1. Evaluate Permission Policy
    try:
        await check_tool_permission(db, tool_name, arguments, session_id=session_id)
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
        else:
            result = await handler(db=db, **arguments)
        audit.record_tool_call(tool_name, arguments, "success", session_id, "Executed successfully")
        return result
    except Exception as exc:
        logger.error("Error executing AI tool %s with args %s: %s", tool_name, arguments, exc, exc_info=True)
        audit.record_tool_call(tool_name, arguments, "error", session_id, str(exc))
        return {
            "status": "error",
            "tool": tool_name,
            "message": f"Failed to execute {tool_name}: {str(exc)}",
        }
