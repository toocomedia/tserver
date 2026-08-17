"""
permissions/policy.py — Permission policy manager & gatekeeper for AI Helper tools.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.ai_helper import AiPermissionPolicy
from plugins.ai_helper.permissions import audit

logger = logging.getLogger(__name__)


class PermissionDeniedError(Exception):
    def __init__(self, tool_name: str, reason: str):
        super().__init__(f"Permission denied for tool '{tool_name}': {reason}")
        self.tool_name = tool_name
        self.reason = reason


TOOL_CATEGORY_MAP: Dict[str, str] = {
    "get_domains_and_ssl": "domains_proxy",
    "get_reverse_proxy_routes": "domains_proxy",
    "get_dns_records": "dns",
    "get_apps_overview": "apps",
    "get_app_logs": "apps",
    "get_databases_overview": "databases",
    "list_website_directory": "files",
    "read_website_file": "files",
}


def _parse_list(json_or_csv: str | None) -> List[str]:
    if not json_or_csv:
        return []
    raw = json_or_csv.strip()
    if raw.startswith("["):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return [str(x).strip().lower() for x in parsed if str(x).strip()]
        except Exception:
            pass
    return [x.strip().lower() for x in raw.split(",") if x.strip()]


async def get_or_create_policy(db: AsyncSession) -> AiPermissionPolicy:
    """Retrieves current permission policy or initializes default."""
    stmt = select(AiPermissionPolicy).where(AiPermissionPolicy.id == 1)
    result = await db.execute(stmt)
    policy = result.scalar_one_or_none()
    if not policy:
        policy = AiPermissionPolicy(
            id=1,
            global_mode="full_read_only",
            allow_domains_proxy=True,
            allow_dns=True,
            allow_php_sites=True,
            allow_container_apps=True,
            allow_databases=True,
            allow_files_read=True,
            allowed_domains="[]",
            allowed_app_ids="[]",
            allowed_databases="[]",
            allowed_file_targets="[]",
            ask_on_demand=False,
        )
        db.add(policy)
        await db.commit()
        await db.refresh(policy)
    return policy


async def update_policy(db: AsyncSession, data: Dict[str, Any]) -> AiPermissionPolicy:
    """Updates AI Helper permission policy."""
    policy = await get_or_create_policy(db)

    if "global_mode" in data:
        mode = str(data["global_mode"]).strip().lower()
        if mode in ("full_read_only", "selective", "disabled"):
            policy.global_mode = mode

    for flag in (
        "allow_domains_proxy",
        "allow_dns",
        "allow_php_sites",
        "allow_container_apps",
        "allow_databases",
        "allow_files_read",
        "ask_on_demand",
    ):
        if flag in data:
            setattr(policy, flag, bool(data[flag]))

    for list_field in ("allowed_domains", "allowed_app_ids", "allowed_databases", "allowed_file_targets"):
        if list_field in data:
            val = data[list_field]
            if isinstance(val, list):
                setattr(policy, list_field, json.dumps([str(x).strip() for x in val if str(x).strip()]))
            else:
                setattr(policy, list_field, str(val or "[]").strip())

    await db.commit()
    await db.refresh(policy)
    return policy


async def check_tool_permission(
    db: AsyncSession,
    tool_name: str,
    arguments: Dict[str, Any],
    session_id: Optional[str] = None,
) -> bool:
    """
    Validates whether the AI Assistant is allowed to execute a given tool call.
    Raises PermissionDeniedError with a clean explanation if blocked.
    """
    policy = await get_or_create_policy(db)

    # 1. Global Kill Switch
    if policy.global_mode == "disabled":
        audit.record_tool_call(tool_name, arguments, "denied", session_id, "AI tool access is globally disabled")
        raise PermissionDeniedError(tool_name, "AI tool execution is currently disabled in panel settings.")

    # 2. Category Flag Check
    category = TOOL_CATEGORY_MAP.get(tool_name)
    if category == "domains_proxy" and not policy.allow_domains_proxy:
        audit.record_tool_call(tool_name, arguments, "denied", session_id, "Domains & Reverse Proxy category disabled")
        raise PermissionDeniedError(tool_name, "Permission to inspect Domains and Reverse Proxy is disabled.")

    if category == "dns" and not policy.allow_dns:
        audit.record_tool_call(tool_name, arguments, "denied", session_id, "DNS inspection disabled")
        raise PermissionDeniedError(tool_name, "Permission to inspect DNS records is disabled.")

    if category == "apps":
        app_type = (arguments.get("app_type") or "").lower()
        if app_type == "php" and not policy.allow_php_sites:
            audit.record_tool_call(tool_name, arguments, "denied", session_id, "PHP websites inspection disabled")
            raise PermissionDeniedError(tool_name, "Permission to inspect PHP websites is disabled.")
        elif app_type in ("python", "container") and not policy.allow_container_apps:
            audit.record_tool_call(tool_name, arguments, "denied", session_id, "Container / Python apps inspection disabled")
            raise PermissionDeniedError(tool_name, "Permission to inspect Python / Container apps is disabled.")
        elif not (policy.allow_php_sites or policy.allow_container_apps):
            audit.record_tool_call(tool_name, arguments, "denied", session_id, "All applications inspection disabled")
            raise PermissionDeniedError(tool_name, "Permission to inspect server applications is disabled.")

    if category == "databases" and not policy.allow_databases:
        audit.record_tool_call(tool_name, arguments, "denied", session_id, "Database inspection disabled")
        raise PermissionDeniedError(tool_name, "Permission to inspect database instances is disabled.")

    if category == "files" and not policy.allow_files_read:
        audit.record_tool_call(tool_name, arguments, "denied", session_id, "File manager read access disabled")
        raise PermissionDeniedError(tool_name, "Permission to read website files is disabled.")

    # 3. Granular Resource Scopes (if global_mode == 'selective')
    if policy.global_mode == "selective":
        allowed_domains = _parse_list(policy.allowed_domains)
        allowed_apps = _parse_list(policy.allowed_app_ids)
        allowed_dbs = _parse_list(getattr(policy, "allowed_databases", "[]"))
        allowed_files = _parse_list(getattr(policy, "allowed_file_targets", "[]"))

        # Check domain argument if present
        domain_arg = str(arguments.get("domain") or arguments.get("domain_name") or "").strip().lower()
        if domain_arg and allowed_domains and domain_arg not in allowed_domains:
            audit.record_tool_call(tool_name, arguments, "denied", session_id, f"Domain '{domain_arg}' not in granular whitelist")
            raise PermissionDeniedError(tool_name, f"Domain '{domain_arg}' is not in the allowed granular scope whitelist.")

        # Check app_id argument if present
        app_id_arg = str(arguments.get("app_id") or "").strip().lower()
        if app_id_arg and allowed_apps and app_id_arg not in allowed_apps:
            audit.record_tool_call(tool_name, arguments, "denied", session_id, f"App ID '{app_id_arg}' not in granular whitelist")
            raise PermissionDeniedError(tool_name, f"App ID '{app_id_arg}' is not in the allowed granular scope whitelist.")

        # Check database filter if database name is queried or filtered
        db_name_arg = str(arguments.get("database_name") or arguments.get("db_name") or "").strip().lower()
        if db_name_arg and allowed_dbs and db_name_arg not in allowed_dbs:
            audit.record_tool_call(tool_name, arguments, "denied", session_id, f"Database '{db_name_arg}' not in granular whitelist")
            raise PermissionDeniedError(tool_name, f"Database '{db_name_arg}' is not in the allowed granular scope whitelist.")

        # Check target_id argument for files
        target_id_arg = str(arguments.get("target_id") or "").strip().lower()
        if target_id_arg and allowed_files and target_id_arg not in allowed_files:
            audit.record_tool_call(tool_name, arguments, "denied", session_id, f"File target '{target_id_arg}' not in granular whitelist")
            raise PermissionDeniedError(tool_name, f"File target '{target_id_arg}' is not in the allowed granular scope whitelist.")

    audit.record_tool_call(tool_name, arguments, "allowed", session_id, "Access granted by policy")
    return True
