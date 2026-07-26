"""Hosted Python application pages and small control endpoints."""
from pathlib import Path
from urllib.parse import urlencode
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models.domain import Domain
from models.app_deployment import AppDeployment
from models.app_environment import AppEnvironmentVariable
from models.hosted_app import HostedApp
from services import app_deployment_service, app_environment_service, app_hosting_logs_service
from services import app_hosting_service as apps
from services import app_source_inspection_service
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
async def inspect_project(source_type: str = Form("git"), repository_url: str = Form(""), branch: str = Form("main"), archive: UploadFile | None = File(None)):
    if source_type == "zip":
        if archive is None: raise HTTPException(400, "Choose a ZIP project first.")
        return JSONResponse(await app_source_inspection_service.inspect_zip(archive))
    return JSONResponse(apps.inspect_repository(repository_url.strip(), branch.strip()))


@router.post("/quick-deploy")
async def quick_deploy(domain_id: int = Form(...), source_type: str = Form(...), repository_url: str = Form(""), branch: str = Form("main"), ssl: bool = Form(False), archive: UploadFile | None = File(None), db: AsyncSession = Depends(get_db)):
    await _domain(db, domain_id)
    detection = await _inspect_source(source_type, repository_url, branch, archive)
    if not detection["can_quick_deploy"]:
        reason = "; ".join(detection["warnings"]) or "Project configuration needs review."
        query = urlencode({"domain_id": domain_id, "ssl": int(ssl), "notice": reason})
        return RedirectResponse(f"/apps/create?{query}", status_code=303)
    app = await apps.create_app(db, domain_id, source_type, detection.get("repository_url") or repository_url or None, branch, str(detection["build_command"]), str(detection["start_command"]), ssl, "none", None)
    if source_type == "zip" and archive is not None: await apps.extract_zip(archive, app)
    deployment = await app_deployment_service.start(db, app)
    return RedirectResponse(f"/apps/{app.id}?deployment={deployment.id}", status_code=303)


@router.post("/create")
async def create(domain_id: int = Form(...), source_type: str = Form(...), repository_url: str = Form(""), branch: str = Form("main"), build_command: str = Form(...), start_command: str = Form(...), ssl: bool = Form(False), postgres_mode: str = Form("none"), database_url: str = Form(""), archive: UploadFile | None = File(None), db: AsyncSession = Depends(get_db)):
    await _domain(db, domain_id)
    app = await apps.create_app(db, domain_id, source_type, repository_url or None, branch, build_command, start_command, ssl, postgres_mode, database_url or None)
    if source_type == "zip":
        if archive is None: raise HTTPException(400, "Choose a ZIP project first.")
        await apps.extract_zip(archive, app)
    return RedirectResponse(f"/apps/{app.id}", status_code=303)


@router.get("/{app_id}/logs", response_class=HTMLResponse)
async def logs(app_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    app = await _app(db, app_id)
    return templates.TemplateResponse("pages/apps/logs.html", {"request": request, "active_page": "apps", "app": app, "logs": await app_hosting_logs_service.get_logs(app)})


@router.get("/{app_id}/deployments/{deployment_id}")
async def deployment_status(app_id: int, deployment_id: int, db: AsyncSession = Depends(get_db)):
    deployment = await app_deployment_service.get(db, app_id, deployment_id)
    return {"id": deployment.id, "status": deployment.status, "stage": deployment.stage, "output": deployment.output, "error": deployment.error}


@router.post("/{app_id}/settings")
async def settings(app_id: int, build_command: str = Form(...), start_command: str = Form(...), db: AsyncSession = Depends(get_db)):
    app_hosting_logs_service.update_commands(await _app(db, app_id), build_command, start_command)
    return RedirectResponse(f"/apps/{app_id}", status_code=303)


@router.post("/{app_id}/detect")
async def detect_again(app_id: int, db: AsyncSession = Depends(get_db)):
    app = await _app(db, app_id)
    detected = apps.inspect_repository(app.repository_url or "", app.branch or "main") if app.source_type == "git" else apps.suggest_project(Path(app.work_dir) / "source")
    app.build_command, app.start_command = str(detected["build_command"]), str(detected["start_command"])
    if detected.get("repository_url"): app.repository_url = str(detected["repository_url"])
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
    return templates.TemplateResponse("pages/apps/detail.html", {"request": request, "active_page": "apps", "app": app, "domain": domain, "deployment": latest, "environment_keys": await app_environment_service.keys(db, app.id)})


@router.post("/{app_id}/upload")
async def upload(app_id: int, archive: UploadFile = File(...), db: AsyncSession = Depends(get_db)):
    await apps.extract_zip(archive, await _app(db, app_id))
    return RedirectResponse(f"/apps/{app_id}", status_code=303)


@router.post("/{app_id}/deploy")
async def deploy(app_id: int, db: AsyncSession = Depends(get_db)):
    app = await _app(db, app_id)
    deployment = await app_deployment_service.start(db, app)
    return RedirectResponse(f"/apps/{app_id}?deployment={deployment.id}", status_code=303)


@router.post("/{app_id}/uninstall")
async def uninstall(app_id: int, db: AsyncSession = Depends(get_db)):
    app = await _app(db, app_id)
    domain = await db.get(Domain, app.domain_id)
    await apps.uninstall(app, domain.name if domain else None)
    await db.execute(delete(AppDeployment).where(AppDeployment.app_id == app.id))
    await db.execute(delete(AppEnvironmentVariable).where(AppEnvironmentVariable.app_id == app.id))
    await db.delete(app)
    return RedirectResponse("/apps/", status_code=303)


@router.post("/{app_id}/{action}")
async def control(app_id: int, action: str, db: AsyncSession = Depends(get_db)):
    await apps.control(await _app(db, app_id), action)
    return RedirectResponse(f"/apps/{app_id}", status_code=303)


async def _app(db: AsyncSession, app_id: int) -> HostedApp:
    app = await db.get(HostedApp, app_id)
    if app is None: raise HTTPException(404, "Python app not found.")
    return app


async def _domain(db: AsyncSession, domain_id: int) -> Domain:
    domain = await db.get(Domain, domain_id)
    if domain is None: raise HTTPException(404, "Domain not found.")
    return domain


async def _inspect_source(source_type: str, repository_url: str, branch: str, archive: UploadFile | None) -> dict[str, object]:
    if source_type == "zip":
        if archive is None: raise HTTPException(400, "Choose a ZIP project first.")
        return await app_source_inspection_service.inspect_zip(archive)
    return apps.inspect_repository(repository_url.strip(), branch.strip())
