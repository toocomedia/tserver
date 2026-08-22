"""
permissions/audit.py — Audit logger for AI tool invocations.
"""
from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

# Keep the most recent 100 tool calls in-memory for live panel audit
_AUDIT_LOG: deque[Dict[str, Any]] = deque(maxlen=100)


# Sensitive key patterns that must be redacted in audit logs
_SENSITIVE_KEY_SUBSTRINGS = (
    "pass", "secret", "token", "key", "auth", "cred", "jwt", "private", "pwd", "cert", "content"
)


def _sanitize_arguments(obj: Any, depth: int = 0) -> Any:
    """Recursively redacts secrets and truncates large payload bodies for audit logging."""
    if depth > 5:
        return "<max_depth_reached>"
    if isinstance(obj, dict):
        sanitized = {}
        for k, v in obj.items():
            k_lower = str(k).lower()
            if any(s in k_lower for s in _SENSITIVE_KEY_SUBSTRINGS):
                sanitized[k] = "••••••••"
            else:
                sanitized[k] = _sanitize_arguments(v, depth + 1)
        return sanitized
    elif isinstance(obj, list):
        return [_sanitize_arguments(item, depth + 1) for item in obj[:20]]
    elif isinstance(obj, str):
        if len(obj) > 256:
            return obj[:256] + f"... [truncated {len(obj) - 256} chars]"
        return obj
    return obj


def record_tool_call(
    tool_name: str,
    arguments: Dict[str, Any],
    status: str,  # "allowed" | "denied" | "error" | "success"
    session_id: str | None = None,
    details: str | None = None,
) -> Dict[str, Any]:
    """Records an AI tool call event with sanitized arguments."""
    sanitized_args = _sanitize_arguments(arguments) if arguments else {}
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "tool_name": tool_name,
        "arguments": sanitized_args,
        "status": status,
        "session_id": session_id,
        "details": details,
    }
    _AUDIT_LOG.appendleft(entry)
    logger.info("AI Tool Audit: [%s] %s -> %s", tool_name, status, details or "")
    return entry


def get_recent_audit_logs(limit: int = 50) -> List[Dict[str, Any]]:
    """Returns recent tool call audit records."""
    return list(_AUDIT_LOG)[:limit]

