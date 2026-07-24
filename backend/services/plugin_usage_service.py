"""Manifest-driven resource usage rows for installed panel plugins."""
from __future__ import annotations

import asyncio
from typing import Any

from plugins import plugin_manager


def _empty_row(plugin: dict[str, Any]) -> dict[str, Any]:
    return {
        "label": plugin.get("name", plugin["id"]),
        "cpu": 0.0,
        "mem": 0.0,
        "memory_mb": 0.0,
        "memory_limit_mb": None,
        "count": 0,
        "status": plugin.get("effective_status", "disabled"),
    }


def _process_totals(
    processes: list[dict[str, Any]],
    process_names: set[str],
    total_memory: int,
) -> tuple[float, float, float, int]:
    cpu = 0.0
    memory_bytes = 0
    count = 0
    for process in processes:
        name = str(process.get("name") or "").lower()
        if name not in process_names:
            continue
        cpu += float(process.get("cpu_percent") or 0.0)
        memory_info = process.get("memory_info")
        memory_bytes += int(getattr(memory_info, "rss", 0) or 0)
        count += 1
    memory_percent = (
        (memory_bytes / total_memory) * 100 if total_memory > 0 else 0.0
    )
    return (
        round(cpu, 1),
        round(memory_percent, 1),
        round(memory_bytes / (1024 ** 2), 1),
        count,
    )


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

    active_docker_ids = [
        plugin["id"]
        for plugin in installed_plugins
        if plugin.get("effective_status") == "active"
        and (plugin.get("usage") or {}).get("source") == "docker"
    ]
    docker_rows: dict[str, dict[str, Any]] = {}
    if active_docker_ids:
        from dependencies import dependency_manager

        docker_service = dependency_manager.get_service("docker")
        if docker_service is not None:
            docker_rows = await asyncio.to_thread(
                docker_service.get_plugin_usage, active_docker_ids
            )

    rows: dict[str, dict[str, Any]] = {}
    for plugin in installed_plugins:
        plugin_id = plugin["id"]
        row = _empty_row(plugin)
        rows[plugin_id] = row
        if plugin.get("effective_status") != "active":
            continue

        usage = plugin.get("usage") or {}
        source = usage.get("source")
        if source == "none":
            continue
        if source == "process":
            cpu, memory_percent, memory_mb, count = _process_totals(
                processes,
                {str(name).lower() for name in usage.get("process_names", [])},
                total_memory,
            )
            row.update(
                cpu=cpu,
                mem=memory_percent,
                memory_mb=memory_mb,
                count=count,
                status="running" if count else "stopped",
            )
            continue
        if source == "docker":
            runtime = docker_rows.get(plugin_id)
            if runtime is None:
                row["status"] = "stopped"
                continue
            memory_bytes = int(runtime.get("memory_bytes") or 0)
            memory_limit_bytes = int(runtime.get("memory_limit_bytes") or 0)
            memory_percent = (
                (memory_bytes / memory_limit_bytes) * 100
                if memory_limit_bytes > 0
                else 0.0
            )
            row.update(
                cpu=round(float(runtime.get("cpu") or 0.0), 1),
                mem=round(memory_percent, 1),
                memory_mb=round(memory_bytes / (1024 ** 2), 1),
                memory_limit_mb=(
                    round(memory_limit_bytes / (1024 ** 2), 1)
                    if memory_limit_bytes > 0
                    else None
                ),
                count=int(runtime.get("count") or 0),
                status=runtime.get("status", "stopped"),
            )
    return rows
