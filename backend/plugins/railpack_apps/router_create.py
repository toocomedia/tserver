"""Apps Engine create-page and deployment-start endpoints."""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models.container_app import ContainerApp
from models.domain import Domain
from models.hosted_app import HostedApp
from services import container_app_database_service, container_app_deployment_service
from services import container_app_inspection_service, container_app_service, container_app_wordpress_service
from templating import templates

router = APIRouter()


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
    request: Request, domain_id: int = Form(...), source_type: str = Form(...), build_mode: str = Form("railpack"),
    repository_url: str = Form(""), branch: str = Form("main"), image_reference: str = Form(""),
    internal_port: int = Form(3000), ssl: bool = Form(False), environment_values: str = Form("{}"),
    database_mode: str = Form("none"), database_url: str = Form(""), database_attachments: str = Form(""),
    preset: str = Form(""), wordpress_site_title: str = Form(""), wordpress_admin_user: str = Form(""),
    wordpress_admin_email: str = Form(""), wordpress_admin_password: str = Form(""), db: AsyncSession = Depends(get_db),
):
    domain = await db.get(Domain, domain_id)
    if domain is None:
        raise HTTPException(404, "Domain not found.")
    attachments = _attachments(database_attachments)
    if preset == "wordpress":
        attachments = _prepare_wordpress(attachments, wordpress_site_title, wordpress_admin_user, wordpress_admin_email, wordpress_admin_password)
        source_type, build_mode, image_reference, internal_port = "image", "image", container_app_wordpress_service.WP_IMAGE, 80
    app = await container_app_service.create_app(
        db, domain=domain, source_type=source_type, build_mode=build_mode, repository_url=repository_url.strip() or None,
        branch=branch.strip() or "main", image_reference=image_reference.strip() or None, internal_port=internal_port,
        ssl_requested=ssl, environment_values=_environment_values(environment_values), database_mode=database_mode,
        database_url=database_url.strip() or None, database_attachments=attachments,
    )
    if preset == "wordpress":
        await _configure_wordpress(app, wordpress_site_title, wordpress_admin_user, wordpress_admin_email, wordpress_admin_password, db)
    domain.project_type = "container"
    deployment = await container_app_deployment_service.queue_deployment(db, app)
    await db.commit()
    return _create_response(request, app.id, deployment.id)


def _attachments(raw: str) -> list[dict[str, str]] | None:
    try:
        return json.loads(raw) if raw else None
    except json.JSONDecodeError as exc:
        raise HTTPException(400, "Database attachments are invalid.") from exc


def _prepare_wordpress(attachments, title: str, user: str, email: str, password: str) -> list[dict[str, str]]:
    container_app_wordpress_service.validate_setup(title, user, email, password)
    attachments = attachments if attachments is not None else []
    if not any(item.get("kind") == "mariadb" for item in attachments if isinstance(item, dict)):
        attachments.append({"kind": "mariadb", "provider": "docker", "environment_key": "MYSQL_URL"})
    return attachments


async def _configure_wordpress(app, title: str, user: str, email: str, password: str, db: AsyncSession) -> None:
    container_app_wordpress_service.prepare(app, title, user, email, password)
    items = await container_app_database_service.attachments_for(db, app.id)
    container_app_database_service.rebuild_environment(app, items, container_app_database_service.read_app_environment(app))


def _environment_values(raw: str) -> dict[str, str]:
    try:
        value = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(400, "Environment values are invalid.") from exc
    if not isinstance(value, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in value.items()):
        raise HTTPException(400, "Environment values must be a key/value object.")
    return value


def _create_response(request: Request, app_id: int, deployment_id: int):
    location = f"/plugins/railpack_apps/{app_id}?deployment={deployment_id}"
    if "application/json" in request.headers.get("accept", ""):
        return JSONResponse({"app_id": app_id, "deployment_id": deployment_id, "redirect": location})
    return RedirectResponse(location, status_code=303)
