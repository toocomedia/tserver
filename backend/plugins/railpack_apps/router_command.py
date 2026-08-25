"""FastAPI router endpoints for executing commands inside App Engine application containers."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models.container_app import ContainerApp
from models.domain import Domain
from plugins.railpack_apps import command_service

router = APIRouter()


class CommandRunRequest(BaseModel):
    command: str = Field(..., min_length=1, max_length=2000, description="Shell command to run in container")
    container_name: Optional[str] = Field(None, max_length=128, description="Target container name if multi-container")
    timeout: int = Field(30, ge=5, le=60, description="Execution timeout in seconds")


@router.post("/{app_id}/command/run")
async def run_command_in_app(
    app_id: int,
    req: CommandRunRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Executes a command inside the target container for this application."""
    app = await db.get(ContainerApp, app_id)
    if app is None:
        raise HTTPException(404, "Container app not found.")

    result = await command_service.execute_app_command(
        app=app,
        command=req.command,
        container_name=req.container_name,
        timeout=req.timeout,
    )
    return JSONResponse(result)


@router.get("/{app_id}/command/quick-commands")
async def get_app_quick_commands(
    app_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Returns available containers and tailored quick commands for the application."""
    app = await db.get(ContainerApp, app_id)
    if app is None:
        raise HTTPException(404, "Container app not found.")

    domain = await db.get(Domain, app.domain_id) if app.domain_id else None
    containers = command_service.get_authorized_containers(app)
    quick_commands = command_service.get_quick_commands(app, domain)

    return JSONResponse({
        "app_id": app.id,
        "status": app.status,
        "containers": containers,
        "quick_commands": quick_commands,
    })
