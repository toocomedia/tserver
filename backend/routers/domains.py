"""
routers/domains.py — Domain CRUD routes.
Routes call services only — no direct DB or nginx calls here.
"""
import logging
import os
from fastapi import APIRouter, Depends, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from services import domain_service, nginx_service
from models.ssl_cert import SslCert
from models.proxy import ReverseProxy
from sqlalchemy import select
from templating import templates
import config

from utils.search_and_bulk import execute_bulk_action, BulkActionRequest
from models.domain import Domain

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/domains", tags=["domains"])


@router.post("/api/bulk")
async def domains_bulk_action(payload: BulkActionRequest, db: AsyncSession = Depends(get_db)):
    """Generic endpoint for bulk operations on domains (e.g. bulk delete)."""
    result = await execute_bulk_action(db, Domain, payload.action, payload.item_ids)
    return result



# ---------------------------------------------------------------
# LIST
# ---------------------------------------------------------------
@router.get("/", response_class=HTMLResponse)
async def domains_list(request: Request, db: AsyncSession = Depends(get_db)):
    domains = await domain_service.get_all(db)

    # Batch fetch all SSL certs in a single query
    all_certs = {c.full_domain: c for c in (await db.execute(select(SslCert))).scalars().all()}
    # Batch check enabled nginx site configs in memory
    enabled_sites = set(os.listdir(config.NGINX_SITES_ENABLED)) if os.path.exists(config.NGINX_SITES_ENABLED) else set()

    # Attach live status to each domain (apex SSL only — not proxy subdomains)
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
    })


# ---------------------------------------------------------------
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
    db: AsyncSession = Depends(get_db),
):
    try:
        domain = await domain_service.create(db, name, project_type=project_type)
        if project_type == "python":
            return RedirectResponse(f"/apps/create?domain_id={domain.id}&ssl={'1' if ssl_enabled == 'yes' else '0'}", status_code=303)
        if ssl_enabled == "yes" and project_type != "dns":
            return RedirectResponse(f"/ssl/issue?domain_id={domain.id}&full_domain={domain.name}", status_code=303)
        return RedirectResponse(f"/domains/{domain.id}", status_code=303)
    except Exception as exc:
        error_msg = str(exc.detail) if hasattr(exc, "detail") else str(exc)
        return templates.TemplateResponse("pages/domains/create.html", {
            "request": request,
            "active_page": "domains",
            "server_ip": config.SERVER_IP,
            "error": error_msg,
            "name": name,
            "project_type": project_type,
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

    nginx_active = nginx_service.config_exists(domain.name)
    current_html = nginx_service.read_index_html(domain.name)

    return templates.TemplateResponse("pages/domains/detail.html", {
        "request": request,
        "active_page": "domains",
        "domain": domain,
        "cert": cert,
        "proxies": proxies,
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
    db: AsyncSession = Depends(get_db),
):
    await domain_service.delete(db, domain_id)
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
