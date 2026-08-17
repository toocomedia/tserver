"""
permissions/__init__.py — AI Helper permissions & security policy package.
"""
from plugins.ai_helper.permissions.policy import (
    PermissionDeniedError,
    check_tool_permission,
    get_or_create_policy,
    update_policy,
)

__all__ = [
    "PermissionDeniedError",
    "check_tool_permission",
    "get_or_create_policy",
    "update_policy",
]
