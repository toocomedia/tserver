"""Hosted Python application pages and small control endpoints."""
from urllib.parse import urlencode
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models.domain import Domain
from models.app_deployment import AppDeployment
from models.app_environment import AppEnvironmentVariable
from models.hosted_app import HostedApp
from routers.apps_support import get_app as _app, get_domain as _domain
from services import app_cleanup_service, app_deployment_service
from services import app_environment_service, app_hosting_logs_service
from services import app_dependency_service, app_hosting_health_service
from services import app_lifecycle_service
from services import hosted_app_control_service
from services import app_hosting_service as apps
from services import app_update_service
from templating import templates

router = APIRouter(prefix="/apps", tags=["apps"])


@router.get("/", response_class=HTMLResponse)
async def index(request: Request, db: AsyncSession = Depends(get_db)):
    hosted = (await db.scalars(select(HostedApp).order_by(HostedApp.id.desc()))).all()
    return templates.TemplateResponse("pages/apps/index.html", {"request": request, "active_page": "apps", "apps": hosted})


@router.get("/create", response_class=HTMLResponse)
async def create_page(request: Request, domain_id: int, ssl: int = 0, db: AsyncSession = Depends(get_db)):
    domain = await _domain(db, domain_id)
    return templates.TemplateResponse("pages/apps/create.html", {"request": request, "active_page": "apps", "domain": domain, "ssl": bool(ssl), "notice": request.query_params.get("notice")})


@router.post("/inspect")
async def inspect_project(source_type: str = Form("git"), repository_url: str = Form(""), branch: str = Form("main")):
    if source_type == "zip":
        raise HTTPException(409, "ZIP source is coming soon.")
    return JSONResponse(apps.inspect_repository(repository_url.strip(), branch.strip()))


@router.post("/quick-deploy")
async def quick_deploy(request: Request, domain_id: int = Form(...), source_type: str = Form(...), repository_url: str = Form(""), branch: str = Form("main"), ssl: bool = Form(False), db: AsyncSession = Depends(get_db)):
    if source_type != "git":
        raise HTTPException(409, "ZIP source is coming soon.")
    await _domain(db, domain_id)
    detection = apps.inspect_repository(repository_url.strip(), branch.strip())
    if not detection["can_quick_deploy"]:
        reason = "; ".join(detection["warnings"]) or "Project configuration needs review."
        if "application/json" in request.headers.get("accept", ""):
            raise HTTPException(400, reason)
        query = urlencode({"domain_id": domain_id, "ssl": int(ssl), "notice": reason})
        return RedirectResponse(f"/apps/create?{query}", status_code=303)
    app = await apps.create_app(db, domain_id, source_type, detection.get("repository_url") or repository_url or None, str(detection.get("branch") or branch), str(detection["build_command"]), str(detection["start_command"]), ssl, "none", None)
    deployment = await app_deployment_service.start(db, app)
    if "application/json" in request.headers.get("accept", ""):
        return JSONResponse({"app_id": app.id, "deployment_id": deployment.id, "redirect": f"/apps/{app.id}?deployment={deployment.id}"})
    return RedirectResponse(f"/apps/{app.id}?deployment={deployment.id}", status_code=303)


@router.post("/create")
async def create(request: Request, domain_id: int = Form(...), source_type: str = Form(...), repository_url: str = Form(""), branch: str = Form("main"), build_command: str = Form(...), start_command: str = Form(...), ssl: bool = Form(False), postgres_mode: str = Form("none"), database_url: str = Form(""), db: AsyncSession = Depends(get_db)):
    await _domain(db, domain_id)
    if source_type != "git":
        raise HTTPException(409, "ZIP source is coming soon.")
    if source_type == "git":
        detected = apps.inspect_repository(repository_url.strip(), branch.strip())
        repository_url, branch = str(detected["repository_url"]), str(detected["branch"])
    app = await apps.create_app(db, domain_id, source_type, repository_url or None, branch, build_command, start_command, ssl, postgres_mode, database_url or None)
    deployment = await app_deployment_service.start(db, app)
    if "application/json" in request.headers.get("accept", ""):
        return JSONResponse({"app_id": app.id, "deployment_id": deployment.id if deployment else None, "redirect": f"/apps/{app.id}"})
    return RedirectResponse(f"/apps/{app.id}", status_code=303)


@router.get("/{app_id}/logs", response_class=HTMLResponse)
async def logs(app_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    app = await _app(db, app_id)
    return templates.TemplateResponse("pages/apps/logs.html", {"request": request, "active_page": "apps", "app": app, "logs": await app_hosting_logs_service.get_logs(app)})


@router.get("/{app_id}/deployments/{deployment_id}")
async def deployment_status(app_id: int, deployment_id: int, db: AsyncSession = Depends(get_db)):
    deployment = await app_deployment_service.get(db, app_id, deployment_id)
    return {
        "id": deployment.id,
        "status": deployment.status,
        "stage": deployment.stage,
        "action": deployment.action,
        "output": deployment.output,
        "error": deployment.error,
        "source_revision": deployment.source_revision,
        "previous_revision": deployment.previous_revision,
        "rollback_status": deployment.rollback_status,
    }


@router.post("/{app_id}/settings")
async def settings(app_id: int, build_command: str = Form(...), start_command: str = Form(...), db: AsyncSession = Depends(get_db)):
    app_hosting_logs_service.update_commands(await _app(db, app_id), build_command, start_command)
    return RedirectResponse(f"/apps/{app_id}", status_code=303)

@router.post("/{app_id}/detect")
async def detect_again(app_id: int, db: AsyncSession = Depends(get_db)):
    app = await _app(db, app_id)
    detected = apps.inspect_repository(app.repository_url or "", app.branch or "main") if app.source_type == "git" else apps.suggest_project(apps.current_source(app))
    app.build_command, app.start_command = str(detected["build_command"]), str(detected["start_command"])
    if detected.get("repository_url"): app.repository_url = str(detected["repository_url"])
    if detected.get("branch"): app.branch = str(detected["branch"])
    return RedirectResponse(f"/apps/{app_id}", status_code=303)


@router.post("/{app_id}/environment")
async def environment(app_id: int, key: str = Form(...), value: str = Form(...), db: AsyncSession = Depends(get_db)):
    await app_environment_service.set_value(db, await _app(db, app_id), key, value)
    return RedirectResponse(f"/apps/{app_id}#environment", status_code=303)


@router.post("/{app_id}/environment/{key}/delete")
async def delete_environment(app_id: int, key: str, db: AsyncSession = Depends(get_db)):
    await app_environment_service.remove(db, await _app(db, app_id), key)
    return RedirectResponse(f"/apps/{app_id}#environment", status_code=303)


@router.get("/{app_id}", response_class=HTMLResponse)
async def detail(app_id: int, request: Request, deployment: int | None = None, db: AsyncSession = Depends(get_db)):
    app = await _app(db, app_id)
    latest = await app_deployment_service.latest(db, app.id)
    domain = await db.get(Domain, app.domain_id)
    return templates.TemplateResponse("pages/apps/detail.html", {
        "request": request,
        "active_page": "apps",
        "app": app,
        "domain": domain,
        "deployment": latest,
        "environment_keys": await app_environment_service.keys(db, app.id),
        "update_ready": app_update_service.has_update(app),
        "notice": request.query_params.get("notice"),
        "error": request.query_params.get("error"),
        "missing_dependencies": app_dependency_service.missing_ids(app),
        "missing_dependency_url": app_dependency_service.requirement_url(
            app_dependency_service.missing_ids(app)[0]
        ) if app_dependency_service.missing_ids(app) else None,
    })


@router.post("/{app_id}/deploy")
async def deploy(app_id: int, db: AsyncSession = Depends(get_db)):
    app = await _app(db, app_id)
    action = (
        "deploy"
        if app.status in ("pending", "failed")
        and not app.active_release
        and not app.deployed_revision
        else "redeploy"
    )
    try:
        deployment = await app_deployment_service.start(db, app, action=action)
    except HTTPException as exc:
        return RedirectResponse(
            f"/apps/{app_id}?{urlencode({'error': str(exc.detail)})}", status_code=303
        )
    return RedirectResponse(f"/apps/{app_id}?deployment={deployment.id}", status_code=303)


@router.post("/{app_id}/uninstall")
async def uninstall(app_id: int, delete_scope: str = Form("app_only"), db: AsyncSession = Depends(get_db)):
    app = await _app(db, app_id)
    if delete_scope not in {"app_only", "app_and_database"}:
        raise HTTPException(400, "Invalid delete option.")
    if delete_scope == "app_and_database" and app.postgres_mode != "create":
        raise HTTPException(400, "Only a panel-managed database can be deleted here.")
    if app.status == "deleting":
        raise HTTPException(409, "Deletion is already running for this app.")
    domain = await db.get(Domain, app.domain_id)
    app.status, app.last_error = "deleting", None
    await app_deployment_service.cancel(db, app)
    await app_lifecycle_service.cancel_deployment(app.id)
    try:
        await app_lifecycle_service.run(
            app.id,
            lambda: app_cleanup_service.uninstall(
                app, domain.name if domain else None,
                delete_database=delete_scope == "app_and_database",
            ),
            wait=True,
        )
    except HTTPException as exc:
        app.status, app.last_error = "delete_failed", str(exc.detail)[:1000]
        await db.commit()
        return RedirectResponse(f"/apps/{app.id}?error=Delete+failed.+Retry+cleanup+from+this+page.", status_code=303)
    await db.execute(delete(AppDeployment).where(AppDeployment.app_id == app.id))
    await db.execute(delete(AppEnvironmentVariable).where(AppEnvironmentVariable.app_id == app.id))
    await db.delete(app)
    return RedirectResponse("/apps/", status_code=303)


@router.post("/{app_id}/{action}")
async def control(app_id: int, action: str, db: AsyncSession = Depends(get_db)):
    app = await _app(db, app_id)
    if action not in {"start", "stop", "restart"}:
        raise HTTPException(400, "Invalid app action.")
    if app.status in {"deleting", "delete_failed"}:
        raise HTTPException(409, "Finish or retry deletion before controlling this app.")
    if action == "stop":
        await app_deployment_service.cancel(db, app)
        await app_lifecycle_service.cancel_deployment(app.id)
    else:
        await app_deployment_service.ensure_idle(db, app.id)
    domain = await db.get(Domain, app.domain_id)
    if domain is None:
        raise HTTPException(409, "App domain is missing.")
    if action == "stop":
        operation = lambda: app_dependency_service.stop_app(db, app, domain)
    elif action == "start":
        operation = lambda: app_dependency_service.start_app(db, app, domain)
    else:
        operation = lambda: _restart_app(db, app, domain)
    try:
        await app_lifecycle_service.run(app.id, operation)
    except HTTPException as exc:
        return RedirectResponse(
            f"/apps/{app_id}?{urlencode({'error': str(exc.detail)})}", status_code=303
        )
    return RedirectResponse(f"/apps/{app_id}", status_code=303)


async def _restart_app(db: AsyncSession, app: HostedApp, domain: Domain) -> None:
    app_dependency_service.require_available(app)
    await hosted_app_control_service.control(app, "restart")
    await app_hosting_health_service.wait_for_listener(app.port)
    await app_dependency_service.publish_app(db, app, domain)
