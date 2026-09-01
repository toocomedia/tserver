"""Core panel JSON API for native PHP and WordPress websites."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models.domain import Domain
from models.php_website_operation import PhpWebsiteOperation
from dependencies import dependency_manager
from schemas import php_sites as schemas
from services import php_site_laravel_service as laravel
from services import php_site_runtime as runtime
from services import php_site_service as service
from services.task_manager_service import task_manager_service
from templating import templates


def require_php_active() -> None:
    status = dependency_manager.get_status("php", cached=True) or {}
    if not status.get("healthy"):
        raise HTTPException(
            503,
            "PHP Websites is available only after a panel-managed PHP version is installed and healthy in Dependencies.",
        )


router = APIRouter(
    prefix="/api/php-sites",
    tags=["php-sites"],
    dependencies=[Depends(require_php_active)],
)

page_router = APIRouter(prefix="/php-sites", tags=["php-site-pages"])


def _php_page_redirect() -> RedirectResponse | None:
    if not dependency_manager.is_healthy("php"):
        return RedirectResponse("/dependencies", status_code=303)
    return None


@page_router.get("/", response_class=HTMLResponse, include_in_schema=False)
async def page_index(request: Request, db: AsyncSession = Depends(get_db)):
    redirect = _php_page_redirect()
    if redirect:
        return redirect
    return templates.TemplateResponse("pages/php_sites/index.html", {
        "request": request,
        "active_page": "php_sites",
        "sites": await service.list_sites(db),
    })


@page_router.get("/create", response_class=HTMLResponse, include_in_schema=False)
async def page_create(request: Request):
    redirect = _php_page_redirect()
    if redirect:
        return redirect
    return templates.TemplateResponse("pages/php_sites/create.html", {
        "request": request,
        "active_page": "php_sites",
    })


@page_router.get("/{site_id}", response_class=HTMLResponse, include_in_schema=False)
async def page_detail(request: Request, site_id: int):
    redirect = _php_page_redirect()
    if redirect:
        return redirect
    return templates.TemplateResponse("pages/php_sites/detail.html", {
        "request": request,
        "active_page": "php_sites",
        "site_id": site_id,
    })


@router.get("/")
async def api_root():
    return {
        "name": "PHP Websites backend",
        "api": "/api/php-sites",
        "documentation": "todo/PHP_WEBSITES_BACKEND.md",
    }


@router.get("/options")
async def api_options(db: AsyncSession = Depends(get_db)):
    return await service.options(db)


@router.get("/sites")
async def api_sites(db: AsyncSession = Depends(get_db)):
    return {"sites": await service.list_sites(db)}


@router.post("/sites", status_code=202)
async def api_create_site(body: schemas.SiteCreate, db: AsyncSession = Depends(get_db)):
    site, operation = await service.create_site(db, body)
    
    # Track in unified Task Manager
    task_manager_service.create_task(
        category="php_site",
        action="create",
        target_id=str(site.id),
        label=f"Create PHP Site: domain {body.domain_id}",
    )
    
    return {
        "site_id": site.id,
        "operation_id": operation.id,
        "status_url": f"/api/php-sites/operations/{operation.id}",
    }


@router.get("/sites/{site_id}")
async def api_site(site_id: int, db: AsyncSession = Depends(get_db)):
    return await service.serialize_site(db, await service.get_site(db, site_id))


@router.get("/sites/{site_id}/health")
async def api_health(site_id: int, db: AsyncSession = Depends(get_db)):
    site = await service.get_site(db, site_id)
    return await service.health(db, site)


@router.get("/sites/{site_id}/operations")
async def api_site_operations(
    site_id: int,
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    await service.get_site(db, site_id)
    operations = (await db.scalars(
        select(PhpWebsiteOperation)
        .where(PhpWebsiteOperation.site_id == site_id)
        .order_by(PhpWebsiteOperation.id.desc())
        .limit(limit)
    )).all()
    return {"operations": [service.operation_payload(item) for item in operations]}


@router.get("/operations/{operation_id}")
async def api_operation(operation_id: int, db: AsyncSession = Depends(get_db)):
    operation = await db.get(PhpWebsiteOperation, operation_id)
    if operation is None:
        raise HTTPException(404, "PHP website operation not found.")
    return service.operation_payload(operation)


def accepted(operation: PhpWebsiteOperation) -> JSONResponse:
    return JSONResponse({
        "site_id": operation.site_id,
        "operation_id": operation.id,
        "status_url": f"/api/php-sites/operations/{operation.id}",
    }, status_code=202)


@router.post("/sites/{site_id}/runtime")
async def api_change_runtime(
    site_id: int, body: schemas.RuntimeChange, db: AsyncSession = Depends(get_db),
):
    site = await service.get_site(db, site_id)
    await asyncio.to_thread(service.require_version, body.php_version)
    if laravel.is_laravel_preset(site.preset) and tuple(map(int, body.php_version.split("."))) < (8, 3):
        raise HTTPException(409, "Laravel and Filament websites require PHP 8.3 or newer.")
    return accepted(await service.queue_action(db, site, "runtime", body.model_dump()))


@router.patch("/sites/{site_id}/document-root")
async def api_change_document_root(
    site_id: int, body: schemas.DocumentRootChange, db: AsyncSession = Depends(get_db),
):
    site = await service.get_site(db, site_id)
    if laravel.is_laravel_preset(site.preset):
        raise HTTPException(409, "Laravel document root is always public.")
    return accepted(await service.queue_action(db, site, "document_root", body.model_dump()))


@router.post("/sites/{site_id}/control")
async def api_control(
    site_id: int, body: schemas.ControlRequest, db: AsyncSession = Depends(get_db),
):
    site = await service.get_site(db, site_id)
    return accepted(await service.queue_action(db, site, body.action))


@router.post("/sites/{site_id}/repair")
async def api_repair(site_id: int, db: AsyncSession = Depends(get_db)):
    site = await service.get_site(db, site_id)
    return accepted(await service.queue_action(db, site, "repair"))


@router.post("/sites/{site_id}/archive")
async def api_archive(
    site_id: int, body: schemas.Confirmation, db: AsyncSession = Depends(get_db),
):
    site = await service.get_site(db, site_id)
    domain = await db.get(Domain, site.domain_id)
    if domain is None or body.confirmation != f"ARCHIVE {domain.name}":
        expected = f"ARCHIVE {domain.name}" if domain else "ARCHIVE {domain}"
        raise HTTPException(409, f"Type {expected} to archive this website.")
    return accepted(await service.queue_action(db, site, "archive"))


@router.post("/sites/{site_id}/restore")
async def api_restore(site_id: int, db: AsyncSession = Depends(get_db)):
    site = await service.get_site(db, site_id)
    if site.status != "archived":
        raise HTTPException(409, "Only an archived PHP website can be restored.")
    return accepted(await service.queue_action(db, site, "restore"))


@router.get("/sites/{site_id}/logs")
async def api_logs(
    site_id: int,
    stream: str = Query("access", pattern="^(access|nginx_error|php)$"),
    lines: int = Query(200, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
):
    site = await service.get_site(db, site_id)
    try:
        return await asyncio.to_thread(runtime.read_logs, site, stream, lines)
    except RuntimeError as exc:
        raise HTTPException(502, str(exc)) from exc


@router.post("/sites/{site_id}/database", status_code=201)
async def api_create_database(
    site_id: int,
    body: schemas.DatabaseCreate | None = None,
    db: AsyncSession = Depends(get_db),
):
    site = await service.get_site(db, site_id)
    install_extension = bool(body and body.install_missing_extension)
    guard_token = None
    if install_extension:
        from services.resource_guard_service import resource_guard_service
        preflight = await resource_guard_service.preflight(db, "native_light")
        if not preflight["ok"]:
            raise HTTPException(409, f"Resource Guard blocked PHP extension installation: {preflight['reason']}")
        guard_token = resource_guard_service.register(
            "php_site", str(site.id), "normal", f"PHP database extension: site {site.id}",
            profile="native_light",
        )
    try:
        await service.ensure_database_extension(site.php_version, install=install_extension)
    finally:
        if guard_token is not None:
            resource_guard_service.unregister(guard_token)
    item = await service.create_database(db, site)
    credentials = service.read_credentials(item)
    await db.commit()
    domain = await db.get(Domain, site.domain_id)
    if domain:
        try:
            await asyncio.to_thread(runtime.provision, site, domain.name, database=credentials)
        except RuntimeError as exc:
            item.status, item.last_error = "error", str(exc)[:1000]
            await db.commit()
            raise HTTPException(
                502,
                "Database was created, but the PHP-FPM environment update failed. Use Repair before relying on it.",
            ) from exc
    response = JSONResponse({
        "database": item.database_name, "username": item.username,
        "password": credentials["password"], "host": "127.0.0.1", "port": 3306,
    }, status_code=201)
    response.headers["Cache-Control"] = "no-store"
    return response


@router.post("/sites/{site_id}/database/reveal")
async def api_reveal_database(site_id: int, db: AsyncSession = Depends(get_db)):
    site = await service.get_site(db, site_id)
    item = await service.database_for(db, site.id)
    credentials = service.read_credentials(item)
    response = JSONResponse({**credentials, "host": "127.0.0.1", "port": 3306})
    response.headers["Cache-Control"] = "no-store"
    return response


@router.post("/sites/{site_id}/database/rotate")
async def api_rotate_database(site_id: int, db: AsyncSession = Depends(get_db)):
    site = await service.get_site(db, site_id)
    credentials = await service.rotate_database(db, site)
    response = JSONResponse({**credentials, "host": "127.0.0.1", "port": 3306})
    response.headers["Cache-Control"] = "no-store"
    return response


@router.delete("/sites/{site_id}/database")
async def api_delete_database(
    site_id: int, body: schemas.DatabaseDelete, db: AsyncSession = Depends(get_db),
):
    site = await service.get_site(db, site_id)
    warning = await service.remove_database(db, site, body.confirmation)
    return {"success": True, "warning": warning}


@router.post("/sites/{site_id}/ssl/issue")
async def api_issue_ssl(
    site_id: int, body: schemas.SslRequest, db: AsyncSession = Depends(get_db),
):
    site = await service.get_site(db, site_id)
    return accepted(await service.queue_action(db, site, "ssl_issue", body.model_dump()))


@router.post("/sites/{site_id}/ssl/renew")
async def api_renew_ssl(site_id: int, db: AsyncSession = Depends(get_db)):
    site = await service.get_site(db, site_id)
    return accepted(await service.queue_action(db, site, "ssl_renew"))


@router.delete("/sites/{site_id}/ssl")
async def api_revoke_ssl(
    site_id: int, body: schemas.Confirmation, db: AsyncSession = Depends(get_db),
):
    site = await service.get_site(db, site_id)
    domain = await db.get(Domain, site.domain_id)
    if domain is None or body.confirmation != f"REVOKE {domain.name}":
        raise HTTPException(409, f"Type REVOKE {domain.name if domain else 'DOMAIN'} to revoke SSL.")
    return accepted(await service.queue_action(db, site, "ssl_revoke"))


@router.post("/sites/{site_id}/wordpress/retry")
async def api_wordpress_retry(
    site_id: int, body: schemas.WordPressRetry, db: AsyncSession = Depends(get_db),
):
    site = await service.get_site(db, site_id)
    return accepted(await service.queue_action(db, site, "wordpress_retry", body.model_dump()))


@router.post("/sites/{site_id}/wordpress/cache/clear")
async def api_clear_wordpress_cache(site_id: int, db: AsyncSession = Depends(get_db)):
    site = await service.get_site(db, site_id)
    if site.preset != "wordpress":
        raise HTTPException(409, "This is not a WordPress website.")
    if await service.active_operation(db, site.id):
        raise HTTPException(409, "Another PHP website operation is already running.")
    domain = await db.get(Domain, site.domain_id)
    if domain is None:
        raise HTTPException(409, "PHP website domain is missing.")
    try:
        return await asyncio.to_thread(runtime.clear_wordpress_cache, site, domain.name)
    except RuntimeError as exc:
        raise HTTPException(502, str(exc)) from exc


@router.post("/sites/{site_id}/laravel/retry")
async def api_laravel_retry(
    site_id: int, body: schemas.LaravelRetry, db: AsyncSession = Depends(get_db),
):
    site = await service.get_site(db, site_id)
    return accepted(await service.queue_action(db, site, "laravel_retry", body.model_dump()))


@router.post("/sites/{site_id}/filament/retry")
async def api_filament_retry(
    site_id: int, body: schemas.FilamentRetry, db: AsyncSession = Depends(get_db),
):
    site = await service.get_site(db, site_id)
    return accepted(await service.queue_action(db, site, "filament_retry", {
        "filament": body.model_dump(exclude={"install_missing_extensions"}),
        "install_missing_extensions": body.install_missing_extensions,
    }))


@router.delete("/sites/{site_id}")
async def api_delete_site(
    site_id: int, body: schemas.DeleteSite, db: AsyncSession = Depends(get_db),
):
    site = await service.get_site(db, site_id)
    domain_name = str(site.domain_id)
    
    task_manager_service.create_task(
        category="php_site",
        action="delete",
        target_id=str(site_id),
        label=f"Delete PHP Site: {site_id}",
    )
    
    domain = await service.delete_site(
        db, site, body.confirmation, delete_database_data=body.delete_database,
    )
    return {"success": True, "domain_id": domain.id, "domain_state": "static"}
