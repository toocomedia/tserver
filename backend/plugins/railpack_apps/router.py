"""Railpack Apps plugin pages and deployment endpoints."""
from __future__ import annotations

from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models.container_app import ContainerApp
from models.container_app_deployment import ContainerAppDeployment
from models.container_app_database import ContainerAppDatabase
from models.container_app_backup import ContainerAppBackup
from models.domain import Domain
from models.ssl_cert import SslCert
from services import container_app_cleanup_service, container_app_database_service
from services import container_app_database_lifecycle_service
from services import container_app_deployment_service
from services import container_app_removal_service, ssl_service
from plugins.railpack_apps.router_create import router as create_router
from plugins.railpack_apps.router_recovery import router as recovery_router
from plugins.railpack_apps.router_resources import router as resource_router
from templating import templates

router = APIRouter(prefix="/plugins/railpack_apps", tags=["railpack-apps"])


@router.get("/", response_class=HTMLResponse)
async def index(request: Request, db: AsyncSession = Depends(get_db)):
    apps = (await db.scalars(select(ContainerApp).order_by(ContainerApp.id.desc()))).all()
    domain_ids = [app.domain_id for app in apps]
    domains = (await db.scalars(select(Domain).where(Domain.id.in_(domain_ids)))).all() if domain_ids else []
    return templates.TemplateResponse("railpack_apps.html", {
        "request": request, "active_page": "railpack_apps", "apps": apps,
        "domains_by_id": {domain.id: domain for domain in domains},
    })


class BulkActionRequest(BaseModel):
    action: str
    ids: list[int]


@router.post("/bulk")
async def bulk_action(req: BulkActionRequest, db: AsyncSession = Depends(get_db)):
    if req.action not in ["start", "stop", "restart", "delete"]:
        raise HTTPException(400, "Invalid bulk action.")
    apps = (await db.scalars(select(ContainerApp).where(ContainerApp.id.in_(req.ids)))).all()
    if not apps:
        return JSONResponse({"status": "ok"})
    
    from services import container_app_control_service, container_app_cleanup_service, container_app_removal_service
    from services.resource_guard_service import resource_guard_service

    for app in apps:
        domain = await db.get(Domain, app.domain_id)
        if not domain:
            continue
        if req.action == "delete":
            app.status = "deleting"
            await db.commit()
            try:
                await container_app_cleanup_service.uninstall(db, app, domain, remove_network=False)
                attachments = await container_app_database_service.attachments_for(db, app.id)
                managed_ids = [item.id for item in attachments if item.provider in {"docker", "panel_postgres", "panel_mariadb"}]
                await container_app_removal_service.remove_selected_data(
                    db, app, attachments, database_ids=managed_ids,
                    delete_app_volume=bool(app.data_volume), delete_wordpress_files=bool(app.wordpress_content_volume),
                    delete_backups=True
                )
                await db.execute(delete(ContainerAppDeployment).where(ContainerAppDeployment.app_id == app.id))
                await db.execute(delete(ContainerAppBackup).where(ContainerAppBackup.app_id == app.id))
                await db.delete(app)
            except Exception:
                app.status = "delete_failed"
                await db.commit()
        else:
            if app.status in {"deleting", "delete_failed", "data_preserved"}:
                continue
            if req.action != "stop":
                try:
                    await resource_guard_service.allow_start(db)
                except RuntimeError:
                    continue
            try:
                await container_app_control_service.control(db, app, domain, req.action)
            except Exception:
                if req.action != "stop":
                    app.status = "failed"
                    await db.commit()
    
    await db.commit()
    return JSONResponse({"status": "ok"})


router.include_router(create_router)
router.include_router(recovery_router)


@router.get("/{app_id}", response_class=HTMLResponse)
async def detail(app_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    app = await db.get(ContainerApp, app_id)
    if app is None:
        raise HTTPException(404, "Container app not found.")
    domain = await db.get(Domain, app.domain_id)
    ssl_active = await ssl_service.is_domain_ssl_active(db, domain)
    deployments = (await db.scalars(select(ContainerAppDeployment).where(
        ContainerAppDeployment.app_id == app.id,
    ).order_by(ContainerAppDeployment.id.desc()).limit(8))).all()
    requested_deployment = _optional_deployment_id(request.query_params.get("deployment"))
    deployment = next(
        (item for item in deployments if item.id == requested_deployment),
        deployments[0] if deployments else None,
    )
    databases = await container_app_database_service.attachments_for(db, app.id)
    backups = list((await db.scalars(select(ContainerAppBackup).where(ContainerAppBackup.app_id == app.id).order_by(ContainerAppBackup.id.desc()).limit(12))).all())
    return templates.TemplateResponse("railpack_apps_detail.html", {
        "request": request, "active_page": "railpack_apps", "app": app, "domain": domain, "ssl_active": ssl_active, "deployment": deployment, "deployments": deployments,
        "databases": databases, "database_statuses": {item.id: container_app_database_lifecycle_service.status(item) for item in databases}, "backups": backups,
    })


@router.get("/{app_id}/deployments/{deployment_id}")
async def deployment_status(app_id: int, deployment_id: int, db: AsyncSession = Depends(get_db)):
    deployment = await db.get(ContainerAppDeployment, deployment_id)
    if deployment is None or deployment.app_id != app_id:
        raise HTTPException(404, "Deployment not found.")
    return JSONResponse({
        "id": deployment.id, "status": deployment.status, "stage": deployment.stage,
        "action": deployment.action, "output": deployment.output, "error": deployment.error,
    })


@router.post("/{app_id}/deploy")
async def deploy(app_id: int, db: AsyncSession = Depends(get_db)):
    app = await _app(db, app_id)
    try:
        deployment = await container_app_deployment_service.queue_deployment(
            db, app, action="deploy" if app.status == "pending" else "redeploy",
        )
    except HTTPException as exc:
        active = await container_app_deployment_service.active_deployment(db, app.id)
        if active:
            return RedirectResponse(f"/plugins/railpack_apps/{app.id}?deployment={active.id}", status_code=303)
        return RedirectResponse(f"/plugins/railpack_apps/{app.id}?{urlencode({'error': str(exc.detail)})}", status_code=303)
    return RedirectResponse(f"/plugins/railpack_apps/{app.id}?deployment={deployment.id}", status_code=303)


@router.post("/{app_id}/settings")
async def update_settings(
    app_id: int,
    request: Request,
    git_ref: str | None = Form(None),
    git_ref_type: str | None = Form(None),
    root_directory: str | None = Form(None),
    dockerfile_path: str | None = Form(None),
    build_args: str | None = Form(None),
    custom_start_command: str | None = Form(None),
    health_path: str | None = Form(None),
    startup_timeout_seconds: int | None = Form(None),
    storage_mounts: str | None = Form(None),
    db: AsyncSession = Depends(get_db),
):
    app = await _app(db, app_id)
    active = await container_app_deployment_service.active_deployment(db, app.id)
    if active:
        raise HTTPException(409, "Settings cannot be updated while a deployment is running or queued.")

    from dependencies.git import repository_service
    from services import container_app_service

    if git_ref is not None and app.source_type == "git":
        ref_type = git_ref_type or app.git_ref_type or "branch"
        repository_service.validate_source(app.repository_url or "", git_ref.strip(), ref_type)
        app.git_ref = git_ref.strip()
        app.branch = app.git_ref
        app.git_ref_type = ref_type
    if root_directory is not None:
        app.root_directory = container_app_service.validate_root_directory(root_directory)
    if dockerfile_path is not None:
        app.dockerfile_path = container_app_service.validate_dockerfile_path(dockerfile_path)
    if build_args is not None:
        app.build_args = container_app_service.parse_build_args(build_args)
    if custom_start_command is not None:
        app.custom_start_command = container_app_service.validate_custom_start_command(custom_start_command)
    if health_path is not None:
        app.health_path = container_app_service.validate_health_path(health_path)
    if startup_timeout_seconds is not None:
        app.startup_timeout_seconds = container_app_service.validate_startup_timeout(startup_timeout_seconds)
    if storage_mounts is not None:
        new_mounts_json = container_app_service.parse_storage_mounts(app.id, storage_mounts)
        app.storage_mounts = new_mounts_json

    await db.commit()

    if "application/json" in request.headers.get("accept", ""):
        return JSONResponse({"status": "ok", "app_id": app.id})
    return RedirectResponse(
        f"/plugins/railpack_apps/{app.id}?{urlencode({'notice': 'Settings updated. Redeploy to apply storage changes; removed storage data is preserved until DELETE ALL.'})}",
        status_code=303,
    )


@router.post("/{app_id}/uninstall")
async def uninstall(
    app_id: int, confirmation: str = Form(""),
    keep_database_ids: list[int] = Form([]), keep_app_volume: bool = Form(False),
    keep_wordpress_files: bool = Form(False), keep_saved_backups: bool = Form(False),
    db: AsyncSession = Depends(get_db),
):
    app = await _app(db, app_id)
    domain = await db.get(Domain, app.domain_id)
    if domain is None:
        raise HTTPException(409, "App domain is missing.")
    attachments = await container_app_database_service.attachments_for(db, app.id)
    managed_ids = {item.id for item in attachments if item.provider in {"docker", "panel_postgres", "panel_mariadb"}}
    if set(keep_database_ids) - managed_ids:
        raise HTTPException(400, "Only this app's local managed services can be kept here.")
    if confirmation != "DELETE ALL":
        raise HTTPException(400, "Type DELETE ALL to confirm this removal.")
    delete_database_ids = list(managed_ids - set(keep_database_ids))
    delete_app_volume = (bool(app.data_volume) or bool(app.storage_mounts)) and not keep_app_volume
    delete_wordpress_files = bool(app.wordpress_content_volume) and not keep_wordpress_files
    delete_saved_backups = not keep_saved_backups
    app.status, app.last_error = "deleting", None
    try:
        await container_app_cleanup_service.uninstall(db, app, domain, remove_network=False)
    except HTTPException as exc:
        app.status, app.last_error = "delete_failed", str(exc.detail)[:1000]
        await db.commit()
        return RedirectResponse(f"/plugins/railpack_apps/{app.id}?error=Delete+failed", status_code=303)
    try:
        data_preserved = await container_app_removal_service.remove_selected_data(
            db, app, attachments, database_ids=delete_database_ids,
            delete_app_volume=delete_app_volume, delete_wordpress_files=delete_wordpress_files,
            delete_backups=delete_saved_backups,
        )
    except (HTTPException, ValueError) as exc:
        app.status, app.last_error = "delete_failed", str(exc.detail if isinstance(exc, HTTPException) else exc)[:1000]
        await db.commit()
        return RedirectResponse(f"/plugins/railpack_apps/{app.id}?error=Data+removal+failed", status_code=303)
    if data_preserved:
        await db.execute(delete(ContainerAppDeployment).where(ContainerAppDeployment.app_id == app.id))
        app.status = "data_preserved"
        await db.commit()
        return RedirectResponse(f"/plugins/railpack_apps/{app.id}?notice=Application+removed;+managed+data+is+preserved", status_code=303)
    await db.execute(delete(ContainerAppDeployment).where(ContainerAppDeployment.app_id == app.id))
    await db.execute(delete(ContainerAppBackup).where(ContainerAppBackup.app_id == app.id))
    await db.delete(app)
    return RedirectResponse("/plugins/railpack_apps/", status_code=303)


async def _app(db: AsyncSession, app_id: int) -> ContainerApp:
    app = await db.get(ContainerApp, app_id)
    if app is None:
        raise HTTPException(404, "Container app not found.")
    return app


def _optional_deployment_id(value: str | None) -> int | None:
    try:
        return int(value) if value else None
    except ValueError:
        return None


router.include_router(resource_router)
