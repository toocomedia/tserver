"""Automatic resource usage rows for installed panel plugins."""
from __future__ import annotations

import asyncio
from typing import Any

from plugins import plugin_manager


def _empty_row(plugin: dict[str, Any]) -> dict[str, Any]:
    return {
        "label": plugin.get("name", plugin["id"]),
        "cpu": 0.0,
        "mem": 0.0,
        "memory": "0 MB",
        "count": 0,
        "status": plugin.get("effective_status", "disabled"),
    }


def _process_usage(
    processes: list[dict[str, Any]],
    process_names: set[str],
    total_memory: int,
) -> dict[str, Any]:
    matches = [
        process
        for process in processes
        if str(process.get("name") or "").lower() in process_names
    ]
    memory_bytes = sum(
        int(getattr(process.get("memory_info"), "rss", 0) or 0)
        for process in matches
    )
    return {
        "cpu": round(sum(float(p.get("cpu_percent") or 0) for p in matches), 1),
        "mem": round(memory_bytes / total_memory * 100, 1) if total_memory else 0,
        "memory": (
            f"{memory_bytes / (1024 ** 2):.0f} MB "
            f"({memory_bytes / total_memory * 100:.1f}% of server)"
            if total_memory else "0 MB"
        ),
        "count": len(matches),
        "status": "running" if matches else "stopped",
    }


async def get_plugin_usage(
    processes: list[dict[str, Any]],
    total_memory: int,
) -> dict[str, dict[str, Any]]:
    """Return live metrics for every installed plugin from its usage contract."""
    if not plugin_manager.plugins:
        await asyncio.to_thread(plugin_manager.discover_plugins)

    installed_plugins = []
    for plugin_id in list(plugin_manager.plugins):
        plugin = plugin_manager.get_plugin(plugin_id)
        if plugin and plugin.get("installed"):
            installed_plugins.append(plugin)

    rows: dict[str, dict[str, Any]] = {}
    for plugin in installed_plugins:
        plugin_id = plugin["id"]
        row = _empty_row(plugin)
        rows[plugin_id] = row
        if plugin.get("effective_status") != "active":
            continue

        service = plugin_manager.get_service(plugin_id)
        usage_hook = getattr(service, "get_usage", None)
        if usage_hook:
            try:
                row.update(await asyncio.to_thread(usage_hook))
            except Exception:
                row["status"] = "unhealthy"
            continue

        process_names = {
            str(name).lower()
            for name in (plugin.get("usage") or {}).get("process_names", [])
        }
        if process_names:
            row.update(
                _process_usage(processes, process_names, total_memory)
            )
    return rows
