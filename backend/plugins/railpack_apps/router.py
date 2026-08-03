"""Railpack Apps plugin pages and deployment endpoints."""
from __future__ import annotations

import json
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models.container_app import ContainerApp
from models.container_app_deployment import ContainerAppDeployment
from models.container_app_database import ContainerAppDatabase
from models.container_app_backup import ContainerAppBackup
from models.domain import Domain
from models.hosted_app import HostedApp
from services import container_app_cleanup_service, container_app_control_service
from services import container_app_backup_service, container_app_database_service
from services import container_app_database_lifecycle_service
from services import container_app_deployment_service, container_app_inspection_service, container_app_service, container_app_wordpress_service
from plugins.railpack_apps.router_resources import router as resource_router
from templating import templates

router = APIRouter(prefix="/plugins/railpack_apps", tags=["railpack-apps"])


def _environment_values(raw: str) -> dict[str, str]:
    try:
        value = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(400, "Environment values are invalid.") from exc
    if not isinstance(value, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in value.items()):
        raise HTTPException(400, "Environment values must be a key/value object.")
    return value


@router.get("/", response_class=HTMLResponse)
async def index(request: Request, db: AsyncSession = Depends(get_db)):
    apps = (await db.scalars(select(ContainerApp).order_by(ContainerApp.id.desc()))).all()
    domain_ids = [app.domain_id for app in apps]
    domains = (await db.scalars(select(Domain).where(Domain.id.in_(domain_ids)))).all() if domain_ids else []
    return templates.TemplateResponse("railpack_apps.html", {
        "request": request, "active_page": "railpack_apps", "apps": apps,
        "domains_by_id": {domain.id: domain for domain in domains},
    })


@router.get("/create", response_class=HTMLResponse)
async def create_page(request: Request, db: AsyncSession = Depends(get_db)):
    used = set((await db.scalars(select(ContainerApp.domain_id))).all())
    used.update((await db.scalars(select(HostedApp.domain_id))).all())
    domains = (await db.scalars(select(Domain).order_by(Domain.name))).all()
    return templates.TemplateResponse("railpack_apps_create.html", {
        "request": request, "active_page": "railpack_apps", "domains": domains, "used_domain_ids": used,
    })


@router.post("/inspect")
async def inspect(repository_url: str = Form(...), branch: str = Form("main")):
    return JSONResponse(container_app_inspection_service.inspect_repository(repository_url.strip(), branch.strip() or "main"))


@router.post("/create")
async def create(
    domain_id: int = Form(...), source_type: str = Form(...), build_mode: str = Form("railpack"),
    repository_url: str = Form(""), branch: str = Form("main"), image_reference: str = Form(""),
    internal_port: int = Form(3000), ssl: bool = Form(False), environment_values: str = Form("{}"),
    database_mode: str = Form("none"), database_url: str = Form(""),
    database_attachments: str = Form(""), preset: str = Form(""), wordpress_site_title: str = Form(""),
    wordpress_admin_user: str = Form(""), wordpress_admin_email: str = Form(""), wordpress_admin_password: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    domain = await db.get(Domain, domain_id)
    if domain is None:
        raise HTTPException(404, "Domain not found.")
    try:
        attachments = json.loads(database_attachments) if database_attachments else None
    except json.JSONDecodeError as exc:
        raise HTTPException(400, "Database attachments are invalid.") from exc
    if preset == "wordpress":
        container_app_wordpress_service.validate_setup(wordpress_site_title, wordpress_admin_user, wordpress_admin_email, wordpress_admin_password)
        attachments = attachments or []
        if not any(item.get("kind") == "mariadb" for item in attachments if isinstance(item, dict)):
            attachments.append({"kind": "mariadb", "provider": "docker", "environment_key": "MYSQL_URL"})
        source_type, build_mode, image_reference, internal_port = "image", "image", container_app_wordpress_service.WP_IMAGE, 80
    app = await container_app_service.create_app(
        db, domain=domain, source_type=source_type, build_mode=build_mode,
        repository_url=repository_url.strip() or None, branch=branch.strip() or "main",
        image_reference=image_reference.strip() or None, internal_port=internal_port,
        ssl_requested=ssl, environment_values=_environment_values(environment_values),
        database_mode=database_mode, database_url=database_url.strip() or None,
        database_attachments=attachments,
    )
    if preset == "wordpress":
        container_app_wordpress_service.prepare(app, wordpress_site_title, wordpress_admin_user, wordpress_admin_email, wordpress_admin_password)
        items = await container_app_database_service.attachments_for(db, app.id)
        container_app_database_service.rebuild_environment(app, items, container_app_database_service.read_app_environment(app))
    domain.project_type = "container"
    deployment = await container_app_deployment_service.queue_deployment(db, app)
    await db.commit()
    return RedirectResponse(f"/plugins/railpack_apps/{app.id}?deployment={deployment.id}", status_code=303)


@router.get("/{app_id}", response_class=HTMLResponse)
async def detail(app_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    app = await db.get(ContainerApp, app_id)
    if app is None:
        raise HTTPException(404, "Container app not found.")
    domain = await db.get(Domain, app.domain_id)
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
        "request": request, "active_page": "railpack_apps", "app": app, "domain": domain, "deployment": deployment, "deployments": deployments,
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
            db, app, action="deploy" if app.status in {"pending", "failed"} else "redeploy",
        )
    except HTTPException as exc:
        active = await container_app_deployment_service.active_deployment(db, app.id)
        if active:
            return RedirectResponse(f"/plugins/railpack_apps/{app.id}?deployment={active.id}", status_code=303)
        return RedirectResponse(f"/plugins/railpack_apps/{app.id}?{urlencode({'error': str(exc.detail)})}", status_code=303)
    return RedirectResponse(f"/plugins/railpack_apps/{app.id}?deployment={deployment.id}", status_code=303)


@router.post("/{app_id}/uninstall")
async def uninstall(app_id: int, confirmation: str = Form(""), db: AsyncSession = Depends(get_db)):
    if confirmation != "DELETE":
        raise HTTPException(400, "Type DELETE to remove this app.")
    app = await _app(db, app_id)
    domain = await db.get(Domain, app.domain_id)
    if domain is None:
        raise HTTPException(409, "App domain is missing.")
    app.status, app.last_error = "deleting", None
    try:
        await container_app_cleanup_service.uninstall(db, app, domain)
    except HTTPException as exc:
        app.status, app.last_error = "delete_failed", str(exc.detail)[:1000]
        await db.commit()
        return RedirectResponse(f"/plugins/railpack_apps/{app.id}?error=Delete+failed", status_code=303)
    attachments = await container_app_database_service.attachments_for(db, app.id)
    if attachments or app.wordpress_content_volume:
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
