"""Usage-page rows for panel runtime dependencies."""
from __future__ import annotations

import asyncio
from typing import Any

from dependencies import dependency_manager


# Docker already has a live daemon/process row in Stack Services.
_STACK_SERVICE_DEPENDENCIES = frozenset({"docker"})


def _matches_process(process_name: str, process_names: set[str]) -> bool:
    if not process_name:
        return False
    if process_name in process_names:
        return True
    if any(process_name.startswith(name) for name in process_names):
        return True
    if "php-fpm" in process_names and ("php" in process_name and "fpm" in process_name):
        return True
    return False


def _process_metrics(
    processes: list[dict[str, Any]],
    process_names: set[str],
    total_memory: int,
) -> dict[str, Any]:
    matches = [
        process for process in processes
        if _matches_process(str(process.get("name") or "").lower(), process_names)
    ]
    memory_bytes = sum(
        int(getattr(process.get("memory_info"), "rss", 0) or 0)
        for process in matches
    )
    memory_percent = memory_bytes / total_memory * 100 if total_memory else 0
    return {
        "count": len(matches),
        "cpu": round(sum(float(p.get("cpu_percent") or 0) for p in matches), 1),
        "memory": f"{memory_bytes / (1024 ** 2):.0f} MB ({memory_percent:.1f}% of server)",
    }


def _rows(
    statuses: list[dict[str, Any]],
    processes: list[dict[str, Any]],
    total_memory: int,
) -> list[dict[str, Any]]:
    rows = []
    for status in statuses:
        if status["id"] in _STACK_SERVICE_DEPENDENCIES:
            continue
        process_names = {
            str(name).lower()
            for name in (status.get("usage") or {}).get("process_names", [])
        }
        metrics = (
            _process_metrics(processes, process_names, total_memory)
            if process_names else {"count": 0, "cpu": 0.0, "memory": "—"}
        )
        rows.append({
            "label": status.get("name", status["id"]),
            "version": status.get("detected_version") or "—",
            "status": status.get("effective_state", "unknown"),
            **metrics,
        })
    return rows


async def get_runtime_usage(
    processes: list[dict[str, Any]], total_memory: int,
) -> list[dict[str, Any]]:
    """Return dependency status and manifest-declared process metrics."""
    statuses = await asyncio.to_thread(
        dependency_manager.get_all_statuses, cached=True
    )
    return _rows(statuses, processes, total_memory)
