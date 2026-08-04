"""Resource Guard status and Settings APIs."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from dependencies import dependency_manager
from models.container_app import ContainerApp
from models.domain import Domain
from models.hosted_app import HostedApp
from plugins import plugin_manager
from services.resource_guard_service import PRIORITIES, resource_guard_service

router = APIRouter(tags=["resource-guard"])


class ResourceGuardSettingsIn(BaseModel):
    mode: str
    memory_limit_percent: int = Field(ge=75, le=95)


class PriorityOverrideIn(BaseModel):
    component_type: str
    component_id: str
    priority: str


@router.get("/api/resource-guard/status")
async def status(db: AsyncSession = Depends(get_db)):
    return await resource_guard_service.status(db)


@router.get("/api/settings/resource-guard")
async def get_settings(db: AsyncSession = Depends(get_db)):
    return {"status": await resource_guard_service.status(db), "resources": await _resources(db)}


@router.post("/api/settings/resource-guard")
async def save_settings(payload: ResourceGuardSettingsIn, db: AsyncSession = Depends(get_db)):
    try:
        return await resource_guard_service.save_settings(db, payload.mode, payload.memory_limit_percent)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/api/settings/resource-guard/priorities")
async def save_priority(payload: PriorityOverrideIn, db: AsyncSession = Depends(get_db)):
    try:
        await resource_guard_service.save_priority(db, payload.component_type, payload.component_id, payload.priority)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"ok": True}


async def _resources(db: AsyncSession) -> list[dict]:
    rows: list[dict] = []
    for plugin in plugin_manager.list_plugins(check_dependencies=False):
        rows.append(await _row(db, "plugin", plugin["id"], plugin["name"]))
    for dependency in dependency_manager.get_all_statuses(cached=True):
        rows.append(await _row(db, "dependency", dependency["id"], dependency["name"]))
    hosted = (await db.execute(select(HostedApp, Domain.name).join(Domain))).all()
    for app, name in hosted:
        rows.append(await _row(db, "hosted_app", str(app.id), name))
    containers = (await db.execute(select(ContainerApp, Domain.name).join(Domain))).all()
    for app, name in containers:
        rows.append(await _row(db, "container_app", str(app.id), name))
    return rows


async def _row(db: AsyncSession, kind: str, item_id: str, label: str) -> dict:
    return {"type": kind, "id": str(item_id), "label": label, "priority": await resource_guard_service.priority(db, kind, str(item_id)), "priorities": PRIORITIES}
