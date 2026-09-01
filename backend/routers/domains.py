"""
routers/domains.py — Domain CRUD routes.
Routes call services only — no direct DB or nginx calls here.
"""
import logging
import os
from fastapi import APIRouter, Depends, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from services import domain_service, nginx_service
from services.task_manager_service import task_manager_service
from middleware.auth import wants_json
from models.ssl_cert import SslCert
from models.proxy import ReverseProxy
from sqlalchemy import select
from templating import templates
import config

from utils.search_and_bulk import execute_bulk_action, BulkActionRequest
from models.domain import Domain
from models.hosted_app import HostedApp
from models.php_website import PhpWebsite

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/domains", tags=["domains"])


@router.post("/api/bulk")
async def domains_bulk_action(payload: BulkActionRequest, db: AsyncSession = Depends(get_db)):
    """Generic endpoint for bulk operations on domains (e.g. bulk delete)."""
    if payload.action.lower().strip() == "delete":
        managed = (await db.execute(
            select(PhpWebsite.id, PhpWebsite.domain_id).where(
                PhpWebsite.domain_id.in_(payload.item_ids)
            )
        )).all()
        if managed:
            raise HTTPException(
                409,
                "Remove managed PHP websites before bulk-deleting their domains.",
            )
    result = await execute_bulk_action(db, Domain, payload.action, payload.item_ids)
    return result



# ---------------------------------------------------------------
# LIST (DB LIMIT + OFFSET PAGINATED)
# ---------------------------------------------------------------
@router.get("/", response_class=HTMLResponse)
async def domains_list(
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    domains = await domain_service.get_all(db)

    # Batch fetch all SSL certs in a single query
    all_certs = {c.full_domain: c for c in (await db.execute(select(SslCert))).scalars().all()}
    # Batch check enabled nginx site configs in memory
    enabled_sites = set(os.listdir(config.NGINX_SITES_ENABLED)) if os.path.exists(config.NGINX_SITES_ENABLED) else set()

    # Attach live status to each domain
    domain_statuses = []
    for d in domains:
        cert = all_certs.get(d.name)
        domain_statuses.append({
            "domain": d,
            "nginx_active": f"{d.name}.conf" in enabled_sites,
            "ssl_active": cert is not None,
            "cert": cert,
        })

    return templates.TemplateResponse("pages/domains/index.html", {
        "request": request,
        "active_page": "domains",
        "domain_statuses": domain_statuses,
        "total_count": len(domains),
    })


@router.get("/api/items")
async def domains_api_items(
    offset: int = 0,
    limit: int = 6,
    db: AsyncSession = Depends(get_db)
):
    """DB-backed paginated items endpoint for Load More button."""
    domains, total = await domain_service.get_paginated(db, limit=limit, offset=offset)
    all_certs = {c.full_domain: c for c in (await db.execute(select(SslCert))).scalars().all()}
    enabled_sites = set(os.listdir(config.NGINX_SITES_ENABLED)) if os.path.exists(config.NGINX_SITES_ENABLED) else set()

    items = []
    for d in domains:
        cert = all_certs.get(d.name)
        items.append({
            "id": d.id,
            "name": d.name,
            "server_ip": d.server_ip,
            "project_type": d.project_type,
            "dns_zone_created": d.dns_zone_created,
            "parent_domain": d.parent_domain,
            "nginx_active": f"{d.name}.conf" in enabled_sites,
            "ssl_active": cert is not None,
        })

    return {
        "items": items,
        "total": total,
        "offset": offset,
        "limit": limit,
        "has_more": (offset + len(domains)) < total
    }


# ---------------------------------------------------------------
# CHECK HOSTNAME (SMART SUBDOMAIN DETECTION)
# ---------------------------------------------------------------
@router.get("/api/check-hostname")
async def check_hostname(
    name: str = "",
    db: AsyncSession = Depends(get_db),
):
    """
    Check if a hostname is valid, if it already exists, and if a parent domain exists.
    """
    name = (name or "").strip().lower()
    if not name:
        return {"valid": False, "exists": False, "is_subdomain": False, "parent_domain": None, "subdomain_prefix": None}

    try:
        from utils.validators import sanitize_domain
        clean_name = sanitize_domain(name)
    except Exception:
        return {"valid": False, "exists": False, "is_subdomain": False, "parent_domain": None, "subdomain_prefix": None}

    existing = await domain_service.get_by_name(db, clean_name)
    if existing:
        return {
            "valid": True,
            "exists": True,
            "is_subdomain": False,
            "parent_domain": None,
            "subdomain_prefix": None,
            "message": f"Domain '{clean_name}' already exists in your panel."
        }

    parent, prefix = await domain_service.find_parent_domain(db, clean_name)
    if parent:
        return {
            "valid": True,
            "exists": False,
            "is_subdomain": True,
            "parent_domain": parent.name,
            "subdomain_prefix": prefix,
            "parent_has_zone": parent.dns_zone_created,
        }

    return {
        "valid": True,
        "exists": False,
        "is_subdomain": False,
        "parent_domain": None,
        "subdomain_prefix": None,
    }


# ---------------------------------------------------------------
# CREATE — form page
# ---------------------------------------------------------------
@router.get("/create", response_class=HTMLResponse)
async def domains_create_page(request: Request):
    return templates.TemplateResponse("pages/domains/create.html", {
        "request": request,
        "active_page": "domains",
        "server_ip": config.SERVER_IP,
        "error": None,
        "name": "",
        "project_type": "static",
        "dns_mode": "new_zone",
        "parent_domain": "",
    })


# ---------------------------------------------------------------
# CREATE — submit
# ---------------------------------------------------------------
@router.post("/create", response_class=HTMLResponse)
async def domains_create(
    request: Request,
    name: str = Form(...),
    project_type: str = Form("static"),
    ssl_enabled: str = Form("no"),
    dns_mode: str = Form("new_zone"),
    parent_domain: str | None = Form(None),
    db: AsyncSession = Depends(get_db),
):
    clean_parent = parent_domain.strip() if (parent_domain and parent_domain.strip()) else None

    if wants_json(request):
        async def _run_create(task_rec):
            task_rec.add_log(f"Creating domain records for {name}...")
            from database import AsyncSessionLocal
            async with AsyncSessionLocal() as bg_db:
                dom = await domain_service.create(
                    bg_db,
                    name,
                    project_type=project_type,
                    dns_mode=dns_mode,
                    parent_domain=clean_parent,
                )
                await bg_db.commit()
            task_rec.add_log(f"Domain {name} records created successfully.")
            return True, f"Domain {name} created."

        task = await task_manager_service.spawn(
            category="domain",
            action="create",
            target_id=name,
            label=f"Create Domain: {name}",
            runner=_run_create,
        )
        return JSONResponse({
            "success": True,
            "task_id": task.id,
            "status": "running",
            "message": f"Creating domain {name}...",
        })

    try:
        domain = await domain_service.create(
            db,
            name,
            project_type=project_type,
            dns_mode=dns_mode,
            parent_domain=clean_parent,
        )
        await task_manager_service.record_completed_task(
            category="domain",
            action="create",
            target_id=name,
            label=f"Create Domain: {name}",
            success=True,
            message=f"Domain {name} created successfully.",
        )
        if ssl_enabled == "yes" and project_type != "dns":
            return RedirectResponse(f"/ssl/issue?domain_id={domain.id}&full_domain={domain.name}", status_code=303)
        return RedirectResponse(f"/domains/{domain.id}", status_code=303)
    except Exception as exc:
        error_msg = str(exc.detail) if hasattr(exc, "detail") else str(exc)
        await task_manager_service.record_completed_task(
            category="domain",
            action="create",
            target_id=name,
            label=f"Create Domain: {name}",
            success=False,
            message=f"Failed to create domain {name}: {error_msg}",
        )
        return templates.TemplateResponse("pages/domains/create.html", {
            "request": request,
            "active_page": "domains",
            "server_ip": config.SERVER_IP,
            "error": error_msg,
            "name": name,
            "project_type": project_type,
            "dns_mode": dns_mode,
            "parent_domain": parent_domain or "",
        }, status_code=400)


# ---------------------------------------------------------------
# DETAIL
# ---------------------------------------------------------------
@router.get("/{domain_id}", response_class=HTMLResponse)
async def domains_detail(
    request: Request,
    domain_id: int,
    db: AsyncSession = Depends(get_db),
):
    domain = await domain_service.get_by_id(db, domain_id)
    # Apex cert only — proxy certs share parent domain_id but different full_domain
    cert = await db.scalar(
        select(SslCert).where(SslCert.full_domain == domain.name)
    )
    proxies = (await db.execute(
        select(ReverseProxy).where(ReverseProxy.domain_id == domain_id)
    )).scalars().all()
    app = await db.scalar(select(HostedApp).where(HostedApp.domain_id == domain_id))

    nginx_active = nginx_service.config_exists(domain.name)
    current_html = nginx_service.read_index_html(domain.name)

    return templates.TemplateResponse("pages/domains/detail.html", {
        "request": request,
        "active_page": "domains",
        "domain": domain,
        "cert": cert,
        "proxies": proxies,
        "app": app,
        "nginx_active": nginx_active,
        "current_html": current_html or "",
        # Always allow showing Issue SSL when apex has no cert (button in page body)
        "can_issue_ssl": cert is None,
    })


# ---------------------------------------------------------------
# EDIT DEFAULT PAGE
# ---------------------------------------------------------------
@router.post("/{domain_id}/edit-page", response_class=HTMLResponse)
async def domains_edit_page(
    request: Request,
    domain_id: int,
    content: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    try:
        await domain_service.update_index_html(db, domain_id, content)
        return RedirectResponse(f"/domains/{domain_id}?saved=1", status_code=303)
    except Exception as exc:
        error_msg = str(exc.detail) if hasattr(exc, "detail") else str(exc)
        domain = await domain_service.get_by_id(db, domain_id)
        return templates.TemplateResponse("pages/domains/detail.html", {
            "request": request,
            "active_page": "domains",
            "domain": domain,
            "error": error_msg,
        }, status_code=400)


# ---------------------------------------------------------------
# DELETE
# ---------------------------------------------------------------
@router.post("/{domain_id}/delete")
async def domains_delete(
    domain_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    domain = await domain_service.get_by_id(db, domain_id)
    domain_name = domain.name if domain else f"ID {domain_id}"

    if wants_json(request):
        async def _run_delete(task_rec):
            task_rec.add_log(f"Removing Nginx vhosts, DNS records, and files for {domain_name}...")
            from database import AsyncSessionLocal
            async with AsyncSessionLocal() as bg_db:
                await domain_service.delete(bg_db, domain_id)
                await bg_db.commit()
            task_rec.add_log(f"Domain {domain_name} deleted successfully.")
            return True, f"Domain {domain_name} deleted."

        task = await task_manager_service.spawn(
            category="domain",
            action="delete",
            target_id=str(domain_id),
            label=f"Delete Domain: {domain_name}",
            runner=_run_delete,
            lock_type="exclusive",
        )
        return JSONResponse({
            "success": True,
            "task_id": task.id,
            "status": "running",
            "message": f"Deleting domain {domain_name}...",
        })

    await domain_service.delete(db, domain_id)
    await task_manager_service.record_completed_task(
        category="domain",
        action="delete",
        target_id=str(domain_id),
        label=f"Delete Domain: {domain_name}",
        success=True,
        message=f"Domain {domain_name} deleted successfully.",
    )
    return RedirectResponse("/domains/", status_code=303)


# ---------------------------------------------------------------
# ENABLE STATIC SITE
# ---------------------------------------------------------------
@router.post("/{domain_id}/enable-static")
async def domains_enable_static(
    domain_id: int,
    db: AsyncSession = Depends(get_db),
):
    await domain_service.enable_static_site(db, domain_id)
    return RedirectResponse(f"/domains/{domain_id}?enabled_static=1", status_code=303)
