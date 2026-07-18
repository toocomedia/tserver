"""
routers/proxy.py — Reverse Proxy Manager routes.
Routes call proxy_service only — no direct nginx/DNS/SSL calls here.
"""
import logging
from fastapi import APIRouter, Depends, Request, Form, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from database import get_db
from models.domain import Domain
from services import proxy_service, dns_service, nginx_service
import config

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/proxy", tags=["proxy"])
templates = Jinja2Templates(directory="templates")


# ---------------------------------------------------------------
# LIST
# ---------------------------------------------------------------
@router.get("/", response_class=HTMLResponse)
async def proxy_index(request: Request, db: AsyncSession = Depends(get_db)):
    """Show all reverse proxies with live nginx/DNS status."""
    proxies = await proxy_service.get_all(db)
    rows = []

    for p in proxies:
        domain = await db.scalar(select(Domain).where(Domain.id == p.domain_id))
        dns_ok = False
        if domain:
            try:
                rrsets = await dns_service.list_records(domain.name)
                fqdn = f"{p.subdomain}.{domain.name}."
                for rr in rrsets:
                    if rr.get("type") == "A" and rr.get("name", "").rstrip(".") == fqdn.rstrip("."):
                        dns_ok = True
                        break
            except Exception as e:
                logger.warning("DNS status check failed for %s: %s", p.full_domain, e)

        rows.append({
            "proxy": p,
            "domain_name": domain.name if domain else "—",
            "nginx_active": nginx_service.config_exists(p.full_domain),
            "dns_ok": dns_ok,
        })

    return templates.TemplateResponse("pages/proxy/index.html", {
        "request": request,
        "active_page": "proxy",
        "rows": rows,
    })


# ---------------------------------------------------------------
# CREATE — form
# ---------------------------------------------------------------
@router.get("/create", response_class=HTMLResponse)
async def proxy_create_page(
    request: Request,
    domain_id: int | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    """Create reverse proxy form. Domain dropdown = managed domains only."""
    domains = (await db.execute(
        select(Domain).order_by(Domain.name)
    )).scalars().all()

    form = {}
    if domain_id is not None:
        form["domain_id"] = domain_id

    return templates.TemplateResponse("pages/proxy/create.html", {
        "request": request,
        "active_page": "proxy",
        "domains": domains,
        "server_ip": config.SERVER_IP,
        "error": None,
        "form": form,
    })


# ---------------------------------------------------------------
# CREATE — submit
# ---------------------------------------------------------------
@router.post("/create", response_class=HTMLResponse)
async def proxy_create_submit(
    request: Request,
    domain_id: int = Form(...),
    subdomain: str = Form(...),
    target_ip: str = Form(...),
    target_port: int = Form(...),
    protocol: str = Form("http"),
    enable_ssl: bool = Form(False),
    db: AsyncSession = Depends(get_db),
):
    """Run full proxy cascade: DNS + nginx + optional SSL."""
    try:
        proxy = await proxy_service.create_proxy(
            db,
            domain_id=domain_id,
            subdomain=subdomain,
            target_ip=target_ip,
            target_port=target_port,
            protocol=protocol,
            enable_ssl=enable_ssl,
        )
        return RedirectResponse(
            f"/proxy/?created={proxy.full_domain}",
            status_code=303,
        )
    except Exception as exc:
        error_msg = str(exc.detail) if hasattr(exc, "detail") else str(exc)
        domains = (await db.execute(
            select(Domain).order_by(Domain.name)
        )).scalars().all()
        return templates.TemplateResponse("pages/proxy/create.html", {
            "request": request,
            "active_page": "proxy",
            "domains": domains,
            "server_ip": config.SERVER_IP,
            "error": error_msg,
            "form": {
                "domain_id": domain_id,
                "subdomain": subdomain,
                "target_ip": target_ip,
                "target_port": target_port,
                "protocol": protocol,
                "enable_ssl": enable_ssl,
            },
        }, status_code=400)


# ---------------------------------------------------------------
# DELETE
# ---------------------------------------------------------------
@router.post("/{proxy_id}/delete")
async def proxy_delete(proxy_id: int, db: AsyncSession = Depends(get_db)):
    """Delete proxy with full cleanup cascade."""
    try:
        await proxy_service.delete_proxy(db, proxy_id)
        return RedirectResponse("/proxy/?deleted=1", status_code=303)
    except Exception as exc:
        error = str(exc.detail) if hasattr(exc, "detail") else str(exc)
        return RedirectResponse(f"/proxy/?error={error}", status_code=303)
