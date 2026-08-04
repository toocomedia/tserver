"""Automatic Docker usage for Apps Engine resources owned by the panel."""
from __future__ import annotations

import asyncio
import re
import subprocess

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.container_app import ContainerApp
from models.container_app_deployment import ContainerAppDeployment
from models.domain import Domain
from services import container_app_service

_MEMORY_RE = re.compile(r"^([0-9.]+)\s*([kmgt]?i?b)$", re.I)


async def get_usage(db: AsyncSession, total_memory: int) -> dict:
    rows = (await db.execute(select(ContainerApp, Domain.name).join(Domain))).all()
    active = await db.scalar(select(ContainerAppDeployment).where(ContainerAppDeployment.status == "running").order_by(ContainerAppDeployment.id.desc()))
    names = [app.container_name for app, _ in rows if app.status == "running"]
    if active:
        names.append("srv-panel-buildkit")
    metrics = await asyncio.to_thread(_stats, names)
    apps = [_app_row(app, domain, metrics.get(app.container_name), total_memory) for app, domain in rows]
    build = _build_row(metrics.get("srv-panel-buildkit"), total_memory) if active else None
    total = _total(apps, total_memory)
    return {"apps": apps, "build": build, "total": total}


def _stats(names: list[str]) -> dict[str, dict]:
    if not names:
        return {}
    try:
        result = container_app_service._run(["docker", "stats", "--no-stream", "--format", "{{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.PIDs}}", *names], timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return {}
    if result.returncode:
        return {}
    rows = {}
    for line in result.stdout.splitlines():
        values = line.split("\t")
        if len(values) != 4:
            continue
        rows[values[0]] = {"cpu": _number(values[1]), "bytes": _memory(values[2].split(" / ", 1)[0]), "count": _integer(values[3])}
    return rows


def _app_row(app: ContainerApp, domain: str, metric: dict | None, total_memory: int) -> dict:
    metric = metric or {"cpu": 0.0, "bytes": 0, "count": 0}
    return _row(domain, app.container_name, app.status, metric, total_memory)


def _build_row(metric: dict | None, total_memory: int) -> dict:
    return _row("Active build", "srv-panel-buildkit", "building", metric or {"cpu": 0.0, "bytes": 0, "count": 0}, total_memory)


def _total(rows: list[dict], total_memory: int) -> dict:
    metric = {"cpu": sum(row["cpu"] for row in rows), "bytes": sum(row["memory_bytes"] for row in rows), "count": sum(row["count"] for row in rows)}
    return _row("Apps Engine total", "railpack_apps", "running" if metric["count"] else "stopped", metric, total_memory)


def _row(label: str, service: str, status: str, metric: dict, total_memory: int) -> dict:
    memory = int(metric["bytes"])
    percent = round(memory / total_memory * 100, 1) if total_memory else 0.0
    return {"label": label, "service": service, "status": status, "cpu": round(float(metric["cpu"]), 1), "count": int(metric["count"]), "mem": percent, "memory_bytes": memory, "memory": f"{memory / (1024 ** 2):.0f} MB ({percent:.1f}% of server)"}


def _number(value: str) -> float:
    try:
        return float(value.strip().rstrip("%"))
    except ValueError:
        return 0.0


def _integer(value: str) -> int:
    try:
        return int(value.strip())
    except ValueError:
        return 0


def _memory(value: str) -> int:
    match = _MEMORY_RE.match(value.strip())
    if not match:
        return 0
    amount, unit = float(match.group(1)), match.group(2).lower()
    units = {"b": 1, "kb": 1000, "mb": 1000 ** 2, "gb": 1000 ** 3, "tb": 1000 ** 4, "kib": 1024, "mib": 1024 ** 2, "gib": 1024 ** 3, "tib": 1024 ** 4}
    return int(amount * units[unit])
