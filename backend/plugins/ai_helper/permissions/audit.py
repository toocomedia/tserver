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


def record_tool_call(
    tool_name: str,
    arguments: Dict[str, Any],
    status: str,  # "allowed" | "denied" | "error" | "success"
    session_id: str | None = None,
    details: str | None = None,
) -> Dict[str, Any]:
    """Records an AI tool call event."""
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "tool_name": tool_name,
        "arguments": arguments,
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
