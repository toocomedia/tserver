"""Template and Compose YAML export endpoints for Railpack applications."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models.container_app import ContainerApp
from models.container_app_snapshot import ContainerAppSnapshot
from services.apps_engine import template_export
from services.apps_engine.app_spec import AppSpec, ServiceSpec

router = APIRouter()


async def _app(db: AsyncSession, app_id: int) -> ContainerApp:
    app = await db.get(ContainerApp, app_id)
    if not app:
        raise HTTPException(404, "Application not found.")
    return app


async def _resolve_compose_yaml(db: AsyncSession, app: ContainerApp) -> tuple[str, str]:
    """Return (yaml_content, filename) for an app."""
    snapshot = None
    if app.active_snapshot_id:
        snapshot = await db.get(ContainerAppSnapshot, app.active_snapshot_id)
    elif app.pending_snapshot_id:
        snapshot = await db.get(ContainerAppSnapshot, app.pending_snapshot_id)

    app_name = app.container_name or f"app-{app.id}"
    filename = f"{app_name}-compose.yml"

    if snapshot:
        from services.apps_engine import app_spec_snapshots
        try:
            spec = app_spec_snapshots.app_spec_for(snapshot)
            yaml_str = template_export.app_spec_to_compose_yaml(spec)
            return yaml_str, filename
        except Exception:
            pass

    # Fallback to single service Compose definition from app metadata
    image = app.image_reference or "app:latest"
    ports = [app.internal_port] if app.internal_port else [80]
    service = ServiceSpec(
        name=app_name,
        image_reference=image,
        internal_ports=tuple(ports),
    )
    fallback_spec = AppSpec(
        name=app_name,
        display_name=app_name.title(),
        web_service_name=app_name,
        web_port=ports[0],
        services={app_name: service},
    )
    yaml_str = template_export.app_spec_to_compose_yaml(fallback_spec)
    return yaml_str, filename


@router.get("/{app_id}/compose-template")
async def download_compose_template(app_id: int, db: AsyncSession = Depends(get_db)):
    """Direct file download of standard docker-compose.yml."""
    app = await _app(db, app_id)
    yaml_str, filename = await _resolve_compose_yaml(db, app)
    return Response(
        content=yaml_str,
        media_type="application/x-yaml",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{app_id}/compose-template/raw")
async def raw_compose_template(app_id: int, db: AsyncSession = Depends(get_db)):
    """JSON response containing the raw YAML string for in-browser copying."""
    app = await _app(db, app_id)
    yaml_str, filename = await _resolve_compose_yaml(db, app)
    return JSONResponse({
        "status": "ok",
        "filename": filename,
        "yaml": yaml_str,
    })
