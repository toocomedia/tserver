"""Safe discovery and removal of failed Apps Engine database containers."""
from __future__ import annotations

import asyncio
import re

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.container_app import ContainerApp
from services import container_app_service

_DATABASE_NAME = re.compile(r"^srv-container-db-(?P<app_id>\d+)-(?P<kind>mariadb|postgresql|redis|mongodb)$")


async def list_orphans(db: AsyncSession) -> list[dict[str, object]]:
    app_ids = set((await db.scalars(select(ContainerApp.id))).all())
    return [item for item in await asyncio.to_thread(_docker_databases) if item["app_id"] not in app_ids]


async def remove_orphan(db: AsyncSession, app_id: int) -> dict[str, object]:
    if await db.get(ContainerApp, app_id):
        raise HTTPException(409, "This Apps Engine app still exists and cannot be recovered as an orphan.")
    items = [item for item in await asyncio.to_thread(_docker_databases) if item["app_id"] == app_id]
    if not items:
        raise HTTPException(404, "No failed-deployment database resources were found.")
    for item in items:
        _require(container_app_service._run(["docker", "rm", "-f", item["name"]], timeout=45), "Could not remove failed database container.")
    volumes = {volume for item in items for volume in item["volumes"]}
    for volume in volumes:
        _require(container_app_service._run(["docker", "volume", "rm", volume], timeout=45), "Could not remove failed database data.")
    return {"removed_containers": len(items), "removed_volumes": len(volumes)}


def _docker_databases() -> list[dict[str, object]]:
    result = container_app_service._run([
        "docker", "ps", "-a", "--filter", "label=srv-panel.plugin=railpack_apps",
        "--format", "{{.Names}}\t{{.Label \"srv-panel.app-id\"}}\t{{.State}}",
    ], timeout=20)
    _require(result, "Could not inspect Apps Engine containers.")
    items = []
    for line in result.stdout.splitlines():
        name, label_app_id, state = (line.split("\t") + ["", "", ""])[:3]
        match = _DATABASE_NAME.fullmatch(name)
        if match is None or label_app_id != match["app_id"]:
            continue
        mounts = container_app_service._run(
            ["docker", "inspect", "--format", "{{range .Mounts}}{{.Name}} {{end}}", name], timeout=20,
        )
        _require(mounts, "Could not inspect failed database storage.")
        items.append({
            "app_id": int(match["app_id"]), "name": name, "kind": match["kind"],
            "state": state, "volumes": [value for value in mounts.stdout.split() if value],
        })
    return items


def _require(result, message: str) -> None:
    if result.returncode:
        raise HTTPException(502, (result.stderr or result.stdout or message)[-1000:])
