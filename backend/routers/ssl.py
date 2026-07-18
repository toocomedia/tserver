"""
routers/ssl.py — SSL Manager routes.
Routes call ssl_service only. No direct certbot or nginx calls here.
"""
import logging
from fastapi import APIRouter, Depends, Request, Form, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from database import get_db
from models.domain import Domain
from models.ssl_cert import SslCert
from models.proxy import ReverseProxy
from services import ssl_service, nginx_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/ssl", tags=["ssl"])
templates = Jinja2Templates(directory="templates")


# ---------------------------------------------------------------
# CERTS LIST
# ---------------------------------------------------------------
@router.get("/", response_class=HTMLResponse)
async def ssl_index(request: Request, db: AsyncSession = Depends(get_db)):
    """Show all issued SSL certs with live expiry status."""
    cert_list = await ssl_service.list_certs(db)
    return templates.TemplateResponse("pages/ssl/index.html", {
        "request": request,
        "active_page": "ssl",
        "cert_list": cert_list,
    })


# ---------------------------------------------------------------
# ISSUE — form
# ---------------------------------------------------------------
@router.get("/issue", response_class=HTMLResponse)
async def ssl_issue_page(
    request: Request,
    domain_id: int | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    """
    Issue SSL form.
    Dropdown shows domains AND proxy subdomains that:
      - have an active nginx config
      - do NOT already have a cert
    """
    # All domains with nginx active and no cert yet
    all_domains = (await db.execute(
        select(Domain).order_by(Domain.name)
    )).scalars().all()

    issued_domains = {
        r.full_domain
        for r in (await db.execute(select(SslCert))).scalars().all()
    }

    eligible = []
    for d in all_domains:
        if nginx_service.config_exists(d.name) and d.name not in issued_domains:
            eligible.append({"id": d.id, "label": d.name, "full_domain": d.name, "type": "domain"})

    # Also include proxy subdomains eligible for SSL
    all_proxies = (await db.execute(
        select(ReverseProxy).order_by(ReverseProxy.full_domain)
    )).scalars().all()

    for p in all_proxies:
        if nginx_service.config_exists(p.full_domain) and p.full_domain not in issued_domains:
            eligible.append({
                "id": p.domain_id,
                "label": f"{p.full_domain} (proxy → {p.target_ip}:{p.target_port})",
                "full_domain": p.full_domain,
                "type": "proxy",
            })

    return templates.TemplateResponse("pages/ssl/issue.html", {
        "request": request,
        "active_page": "ssl",
        "eligible": eligible,
        "preselect_id": domain_id,
        "error": None,
    })


# ---------------------------------------------------------------
# ISSUE — submit
# ---------------------------------------------------------------
@router.post("/issue", response_class=HTMLResponse)
async def ssl_issue_submit(
    request: Request,
    domain_id: int = Form(...),
    full_domain: str = Form(...),
    include_www: bool = Form(default=False),
    db: AsyncSession = Depends(get_db),
):
    """Run certbot for the selected domain/subdomain."""
    try:
        cert = await ssl_service.issue_cert(db, domain_id, full_domain, include_www)
        return RedirectResponse(f"/ssl/?issued={cert.full_domain}", status_code=303)
    except Exception as exc:
        error_msg = str(exc.detail) if hasattr(exc, "detail") else str(exc)

        # Re-render form with error
        all_domains = (await db.execute(select(Domain).order_by(Domain.name))).scalars().all()
        issued_domains = {r.full_domain for r in (await db.execute(select(SslCert))).scalars().all()}
        eligible = [
            {"id": d.id, "label": d.name, "full_domain": d.name, "type": "domain"}
            for d in all_domains
            if nginx_service.config_exists(d.name) and d.name not in issued_domains
        ]
        return templates.TemplateResponse("pages/ssl/issue.html", {
            "request": request,
            "active_page": "ssl",
            "eligible": eligible,
            "preselect_id": domain_id,
            "error": error_msg,
        }, status_code=400)


# ---------------------------------------------------------------
# RENEW
# ---------------------------------------------------------------
@router.post("/{cert_id}/renew")
async def ssl_renew(cert_id: int, db: AsyncSession = Depends(get_db)):
    """Renew a specific cert by ID."""
    try:
        await ssl_service.renew_cert(db, cert_id)
        return RedirectResponse(f"/ssl/?renewed=1", status_code=303)
    except Exception as exc:
        error = str(exc.detail) if hasattr(exc, "detail") else str(exc)
        return RedirectResponse(f"/ssl/?error={error}", status_code=303)


# ---------------------------------------------------------------
# REVOKE
# ---------------------------------------------------------------
@router.post("/{cert_id}/revoke")
async def ssl_revoke(cert_id: int, db: AsyncSession = Depends(get_db)):
    """Revoke cert, revert nginx to HTTP-only."""
    try:
        await ssl_service.revoke_cert(db, cert_id)
        return RedirectResponse("/ssl/?revoked=1", status_code=303)
    except Exception as exc:
        error = str(exc.detail) if hasattr(exc, "detail") else str(exc)
        return RedirectResponse(f"/ssl/?error={error}", status_code=303)
