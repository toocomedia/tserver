"""Resource usage rows for installed panel plugins."""
from __future__ import annotations

import asyncio
from typing import Any

try:
    import psutil
except ImportError:
    psutil = None  # type: ignore[assignment]

from plugins import plugin_manager
from plugins.roundcube_webmail.service import roundcube_webmail_service

_PROCESS_CACHE: dict[int, Any] = {}


def _empty_row(label: str, status: str) -> dict[str, Any]:
    return {
        "label": label,
        "cpu": 0.0,
        "mem": 0.0,
        "count": 0,
        "status": status,
    }


def _container_processes(root_pid: int) -> list[Any]:
    if psutil is None or root_pid <= 0:
        return []
    try:
        root = psutil.Process(root_pid)
        pids = {root_pid}
        pids.update(child.pid for child in root.children(recursive=True))
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return []
    for pid in set(_PROCESS_CACHE) - pids:
        _PROCESS_CACHE.pop(pid, None)
    for pid in pids:
        try:
            if pid not in _PROCESS_CACHE:
                _PROCESS_CACHE[pid] = psutil.Process(pid)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return list(_PROCESS_CACHE.values())


def _process_totals(processes: list[Any]) -> tuple[float, float, int]:
    cpu = 0.0
    memory = 0.0
    count = 0
    for process in processes:
        try:
            cpu += process.cpu_percent(interval=None)
            memory += process.memory_percent()
            count += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return round(cpu, 1), round(memory, 1), count


async def get_plugin_usage() -> dict[str, dict[str, Any]]:
    """Return lightweight live metrics for installed plugins."""
    plugin = await asyncio.to_thread(
        plugin_manager.get_plugin, "roundcube_webmail"
    )
    if not plugin or not plugin.get("installed"):
        return {}

    label = plugin.get("name", "Webmail (Roundcube)")
    effective_status = plugin.get("effective_status", "disabled")
    row = _empty_row(label, effective_status)
    if effective_status != "active":
        return {"roundcube_webmail": row}

    runtime = await asyncio.to_thread(roundcube_webmail_service.get_status)
    if not runtime.get("running"):
        row["status"] = "stopped"
        return {"roundcube_webmail": row}
    if not runtime.get("healthy"):
        row["status"] = "unhealthy"
        return {"roundcube_webmail": row}

    cpu, memory, count = _process_totals(
        _container_processes(int(runtime.get("pid") or 0))
    )
    row.update(cpu=cpu, mem=memory, count=count, status="running")
    return {"roundcube_webmail": row}
