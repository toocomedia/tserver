"""Railpack Apps plugin pages and deployment endpoints."""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models.container_app import ContainerApp
from models.container_app_deployment import ContainerAppDeployment
from models.domain import Domain
from models.hosted_app import HostedApp
from services import container_app_cleanup_service, container_app_control_service
from services import container_app_deployment_service, container_app_inspection_service, container_app_service
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
    db: AsyncSession = Depends(get_db),
):
    domain = await db.get(Domain, domain_id)
    if domain is None:
        raise HTTPException(404, "Domain not found.")
    app = await container_app_service.create_app(
        db, domain=domain, source_type=source_type, build_mode=build_mode,
        repository_url=repository_url.strip() or None, branch=branch.strip() or "main",
        image_reference=image_reference.strip() or None, internal_port=internal_port,
        ssl_requested=ssl, environment_values=_environment_values(environment_values),
        database_mode=database_mode, database_url=database_url.strip() or None,
    )
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
    deployment = await db.scalar(select(ContainerAppDeployment).where(ContainerAppDeployment.app_id == app.id).order_by(ContainerAppDeployment.id.desc()))
    return templates.TemplateResponse("railpack_apps_detail.html", {
        "request": request, "active_page": "railpack_apps", "app": app, "domain": domain, "deployment": deployment,
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
    deployment = await container_app_deployment_service.queue_deployment(
        db, app, action="deploy" if app.status in {"pending", "failed"} else "redeploy",
    )
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
    await db.execute(delete(ContainerAppDeployment).where(ContainerAppDeployment.app_id == app.id))
    await db.delete(app)
    return RedirectResponse("/plugins/railpack_apps/", status_code=303)


@router.post("/{app_id}/{action}")
async def control(app_id: int, action: str, db: AsyncSession = Depends(get_db)):
    app = await _app(db, app_id)
    domain = await db.get(Domain, app.domain_id)
    if domain is None:
        raise HTTPException(409, "App domain is missing.")
    if app.status in {"deleting", "delete_failed"}:
        raise HTTPException(409, "Finish deletion before controlling this app.")
    await container_app_control_service.control(db, app, domain, action)
    return RedirectResponse(f"/plugins/railpack_apps/{app.id}", status_code=303)


async def _app(db: AsyncSession, app_id: int) -> ContainerApp:
    app = await db.get(ContainerApp, app_id)
    if app is None:
        raise HTTPException(404, "Container app not found.")
    return app
