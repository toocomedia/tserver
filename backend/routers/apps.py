"""Python application hosting routes."""
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from database import get_db
from models.domain import Domain
from models.hosted_app import HostedApp
from services import app_hosting_service as apps
from services import app_hosting_logs_service as app_logs
from templating import templates

router = APIRouter(prefix="/apps", tags=["apps"])

@router.get("/", response_class=HTMLResponse)
async def index(request: Request, db: AsyncSession = Depends(get_db)):
    return templates.TemplateResponse("pages/apps/index.html", {"request":request,"active_page":"apps","apps":(await db.scalars(select(HostedApp))).all()})

@router.get("/create", response_class=HTMLResponse)
async def create_page(request: Request, domain_id: int, ssl: int = 0, db: AsyncSession = Depends(get_db)):
    domain = await db.get(Domain, domain_id)
    return templates.TemplateResponse("pages/apps/create.html", {"request":request,"active_page":"apps","domain":domain,"ssl":bool(ssl)})

@router.post("/create")
async def create(domain_id: int = Form(...), source_type: str = Form(...), repository_url: str = Form(""), branch: str = Form("main"), build_command: str = Form("pip install -r requirements.txt"), start_command: str = Form("uvicorn main:app --host $HOST --port $PORT"), ssl: bool = Form(False), postgres_mode: str = Form("none"), database_url: str = Form(""), archive: UploadFile | None = File(None), db: AsyncSession = Depends(get_db)):
    app = await apps.create_app(db, domain_id, source_type, repository_url or None, branch, build_command, start_command, ssl, postgres_mode, database_url or None)
    if source_type == "zip":
        if archive is None:
            raise HTTPException(400, "Choose a ZIP application archive.")
        await apps.extract_zip(archive, app)
    return RedirectResponse(f"/apps/{app.id}", status_code=303)

@router.post("/inspect")
async def inspect_project(repository_url: str = Form(...), branch: str = Form("main")):
    return JSONResponse(apps.inspect_repository(repository_url.strip(), branch.strip()))

@router.get("/{app_id}/logs", response_class=HTMLResponse)
async def logs(app_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    app = await db.get(HostedApp, app_id)
    if app is None: raise HTTPException(404, "Python app not found.")
    return templates.TemplateResponse("pages/apps/logs.html", {"request":request,"active_page":"apps","app":app,"logs":await app_logs.get_logs(app)})

@router.post("/{app_id}/settings")
async def settings(app_id: int, build_command: str = Form(...), start_command: str = Form(...), db: AsyncSession = Depends(get_db)):
    app = await db.get(HostedApp, app_id)
    if app is None: raise HTTPException(404, "Python app not found.")
    app_logs.update_commands(app, build_command, start_command)
    return RedirectResponse(f"/apps/{app_id}", status_code=303)

@router.get("/{app_id}", response_class=HTMLResponse)
async def detail(app_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    return templates.TemplateResponse("pages/apps/detail.html", {"request":request,"active_page":"apps","app":await db.get(HostedApp, app_id)})

@router.post("/{app_id}/upload")
async def upload(app_id: int, archive: UploadFile = File(...), db: AsyncSession = Depends(get_db)):
    await apps.extract_zip(archive, await db.get(HostedApp, app_id)); return RedirectResponse(f"/apps/{app_id}", status_code=303)

@router.post("/{app_id}/deploy")
async def deploy(app_id: int, db: AsyncSession = Depends(get_db)):
    app = await db.get(HostedApp, app_id); domain = await db.get(Domain, app.domain_id)
    await apps.deploy(app, domain.name); app.status = "running"
    if app.ssl_requested:
        from services import ssl_service, nginx_service
        cert = await ssl_service.issue_cert(db, domain.id, domain.name)
        domain.nginx_config_path = await nginx_service.update_proxy_ssl(domain.name, "127.0.0.1", app.port, "http", cert.cert_path, f"/etc/letsencrypt/live/{domain.name}/privkey.pem")
        await nginx_service.reload()
    return RedirectResponse(f"/apps/{app_id}", status_code=303)

@router.post("/{app_id}/uninstall")
async def uninstall(app_id: int, db: AsyncSession = Depends(get_db)):
    app = await db.get(HostedApp, app_id)
    if app is None:
        raise HTTPException(404, "Python app not found.")
    domain = await db.get(Domain, app.domain_id)
    await apps.uninstall(app, domain.name if domain else None); await db.delete(app)
    return RedirectResponse("/apps/", status_code=303)

@router.post("/{app_id}/{action}")
async def control(app_id: int, action: str, db: AsyncSession = Depends(get_db)):
    app = await db.get(HostedApp, app_id); await apps.control(app, action)
    return RedirectResponse(f"/apps/{app_id}", status_code=303)
