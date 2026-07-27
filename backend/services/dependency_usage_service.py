"""Usage-page rows for panel runtime dependencies."""
from __future__ import annotations

import asyncio
from typing import Any

from dependencies import dependency_manager


# Docker already has a live daemon/process row in Stack Services.
_STACK_SERVICE_DEPENDENCIES = frozenset({"docker"})


def _usage_detail(status: dict[str, Any]) -> str:
    dependency_id = status["id"]
    if dependency_id == "git":
        return "On demand; no resident process."
    if dependency_id == "python":
        return "Shared runtime; per-app usage is listed above."
    if status.get("running"):
        return "Shared server runtime."
    return "Runtime is not currently running."


def _rows(statuses: list[dict[str, Any]]) -> list[dict[str, str]]:
    return [
        {
            "label": status.get("name", status["id"]),
            "version": status.get("detected_version") or "—",
            "status": status.get("effective_state", "unknown"),
            "details": _usage_detail(status),
        }
        for status in statuses
        if status["id"] not in _STACK_SERVICE_DEPENDENCIES
    ]


async def get_runtime_usage() -> list[dict[str, str]]:
    """Return every registered dependency except ones shown as stack services."""
    statuses = await asyncio.to_thread(
        dependency_manager.get_all_statuses, cached=True
    )
    return _rows(statuses)
