"""
routers/ssl.py — SSL Manager routes.
Routes call ssl_service only. No direct certbot or nginx calls here.
"""
import logging
from fastapi import APIRouter, Depends, Request, Form, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from database import get_db
from models.domain import Domain
from models.ssl_cert import SslCert
from models.proxy import ReverseProxy
from services import ssl_service, nginx_service
from templating import templates

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/ssl", tags=["ssl"])


async def _build_eligible(db: AsyncSession) -> list[dict]:
    """Domains and proxies with nginx active and no existing cert."""
    issued_domains = {
        r.full_domain
        for r in (await db.execute(select(SslCert))).scalars().all()
    }

    eligible: list[dict] = []

    all_domains = (await db.execute(
        select(Domain).order_by(Domain.name)
    )).scalars().all()

    for d in all_domains:
        if nginx_service.config_exists(d.name) and d.name not in issued_domains:
            eligible.append({
                "id": d.id,
                "label": d.name,
                "full_domain": d.name,
                "type": "domain",
            })

    all_proxies = (await db.execute(
        select(ReverseProxy).order_by(ReverseProxy.full_domain)
    )).scalars().all()

    for p in all_proxies:
        if nginx_service.config_exists(p.full_domain) and p.full_domain not in issued_domains:
            eligible.append({
                "id": p.domain_id,  # may be None for external
                "label": f"{p.full_domain} (proxy → {p.target_ip}:{p.target_port})",
                "full_domain": p.full_domain,
                "type": "proxy",
            })

    return eligible


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
    full_domain: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    """
    Issue SSL form.
    Dropdown shows domains AND proxy subdomains that:
      - have an active nginx config
      - do NOT already have a cert
    Preselect prefers full_domain (exact host); falls back to domain_id.
    """
    eligible = await _build_eligible(db)
    preselect_full = (full_domain or "").strip().lower() or None

    # Legacy: domain_id alone → preselect apex domain name if present
    if not preselect_full and domain_id is not None:
        for item in eligible:
            if item.get("id") == domain_id and item.get("type") == "domain":
                preselect_full = item["full_domain"]
                break
        if not preselect_full:
            for item in eligible:
                if item.get("id") == domain_id:
                    preselect_full = item["full_domain"]
                    break

    return templates.TemplateResponse("pages/ssl/issue.html", {
        "request": request,
        "active_page": "ssl",
        "eligible": eligible,
        "preselect_full_domain": preselect_full,
        "error": None,
    })


# ---------------------------------------------------------------
# ISSUE — submit
# ---------------------------------------------------------------
@router.post("/issue", response_class=HTMLResponse)
async def ssl_issue_submit(
    request: Request,
    full_domain: str = Form(...),
    domain_id: str = Form(""),
    include_www: bool = Form(default=False),
    db: AsyncSession = Depends(get_db),
):
    """Run certbot for the selected domain/subdomain."""
    full_domain = full_domain.strip().lower()
    resolved_domain_id: int | None = None
    if domain_id and str(domain_id).strip().isdigit():
        resolved_domain_id = int(domain_id)

    # Resolve domain_id from host if missing (external proxy or form omit)
    if resolved_domain_id is None:
        domain = await db.scalar(select(Domain).where(Domain.name == full_domain))
        if domain:
            resolved_domain_id = domain.id
        else:
            proxy = await db.scalar(
                select(ReverseProxy).where(ReverseProxy.full_domain == full_domain)
            )
            if proxy:
                resolved_domain_id = proxy.domain_id

    try:
        cert = await ssl_service.issue_cert(
            db, resolved_domain_id, full_domain, include_www
        )
        return RedirectResponse(f"/ssl/?issued={cert.full_domain}", status_code=303)
    except Exception as exc:
        error_msg = str(exc.detail) if hasattr(exc, "detail") else str(exc)
        eligible = await _build_eligible(db)
        return templates.TemplateResponse("pages/ssl/issue.html", {
            "request": request,
            "active_page": "ssl",
            "eligible": eligible,
            "preselect_full_domain": full_domain,
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

from pydantic import BaseModel
class AutoRenewPayload(BaseModel):
    auto_renew: bool

@router.post("/api/{cert_id}/auto-renew")
async def ssl_auto_renew_toggle(cert_id: int, payload: AutoRenewPayload, db: AsyncSession = Depends(get_db)):
    """Toggle auto_renew for a certificate."""
    cert = await db.scalar(select(SslCert).where(SslCert.id == cert_id))
    if cert:
        cert.auto_renew = payload.auto_renew
        await db.commit()
    return {"status": "ok", "auto_renew": payload.auto_renew}
