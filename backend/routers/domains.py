"""
routers/domains.py — Domain CRUD routes.
Routes call services only — no direct DB or nginx calls here.
"""
import logging
from fastapi import APIRouter, Depends, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from services import domain_service, nginx_service
from models.ssl_cert import SslCert
from models.proxy import ReverseProxy
from sqlalchemy import select
import config

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/domains", tags=["domains"])
templates = Jinja2Templates(directory="templates")


# ---------------------------------------------------------------
# LIST
# ---------------------------------------------------------------
@router.get("/", response_class=HTMLResponse)
async def domains_list(request: Request, db: AsyncSession = Depends(get_db)):
    domains = await domain_service.get_all(db)

    # Attach live status to each domain
    domain_statuses = []
    for d in domains:
        cert = await db.scalar(select(SslCert).where(SslCert.domain_id == d.id))
        domain_statuses.append({
            "domain": d,
            "nginx_active": nginx_service.config_exists(d.name),
            "ssl_active": cert is not None,
            "cert": cert,
        })

    return templates.TemplateResponse("pages/domains/index.html", {
        "request": request,
        "active_page": "domains",
        "domain_statuses": domain_statuses,
    })


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
    })


# ---------------------------------------------------------------
# CREATE — submit
# ---------------------------------------------------------------
@router.post("/create", response_class=HTMLResponse)
async def domains_create(
    request: Request,
    name: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    try:
        domain = await domain_service.create(db, name)
        return RedirectResponse(f"/domains/{domain.id}", status_code=303)
    except Exception as exc:
        error_msg = str(exc.detail) if hasattr(exc, "detail") else str(exc)
        return templates.TemplateResponse("pages/domains/create.html", {
            "request": request,
            "active_page": "domains",
            "server_ip": config.SERVER_IP,
            "error": error_msg,
            "name": name,
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
    cert = await db.scalar(select(SslCert).where(SslCert.domain_id == domain_id))
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
        "current_html": current_html,
        "can_issue_ssl": nginx_active and cert is None,
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
