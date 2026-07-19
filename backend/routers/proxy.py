"""
routers/proxy.py — Reverse Proxy Manager routes.
Routes call proxy_service only — no direct nginx/DNS/SSL calls here.
"""
import logging
from fastapi import APIRouter, Depends, Request, Form, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from database import get_db
from models.domain import Domain
from services import proxy_service, dns_service, nginx_service
from templating import templates
import config

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/proxy", tags=["proxy"])


# ---------------------------------------------------------------
# LIST
# ---------------------------------------------------------------
@router.get("/", response_class=HTMLResponse)
async def proxy_index(request: Request, db: AsyncSession = Depends(get_db)):
    """Show all reverse proxies with live nginx/DNS status."""
    proxies = await proxy_service.get_all(db)
    rows = []

    for p in proxies:
        domain = None
        if p.domain_id is not None:
            domain = await db.scalar(select(Domain).where(Domain.id == p.domain_id))

        dns_managed = getattr(p, "dns_managed", True)
        dns_ok = False
        dns_status = "external"

        if not dns_managed:
            dns_ok = True  # external DNS is user-managed
            dns_status = "external"
        elif domain:
            dns_status = "missing"
            try:
                rrsets = await dns_service.list_records(domain.name)
                fqdn = f"{p.subdomain}.{domain.name}."
                for rr in rrsets:
                    if rr.get("type") == "A" and rr.get("name", "").rstrip(".") == fqdn.rstrip("."):
                        dns_ok = True
                        dns_status = "active"
                        break
            except Exception as e:
                logger.warning("DNS status check failed for %s: %s", p.full_domain, e)
        else:
            dns_status = "missing"

        rows.append({
            "proxy": p,
            "domain_name": domain.name if domain else "External",
            "nginx_active": nginx_service.config_exists(p.full_domain),
            "dns_ok": dns_ok,
            "dns_status": dns_status,
            "dns_managed": dns_managed,
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
    """Create reverse proxy form — managed domain or external hostname."""
    domains = (await db.execute(
        select(Domain).order_by(Domain.name)
    )).scalars().all()

    form: dict = {"mode": "managed"}
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
    mode: str = Form("managed"),
    domain_id: str = Form(""),
    subdomain: str = Form(""),
    hostname: str = Form(""),
    target_ip: str = Form(...),
    target_port: int = Form(...),
    protocol: str = Form("http"),
    enable_ssl: bool = Form(False),
    db: AsyncSession = Depends(get_db),
):
    """Run proxy cascade for managed or external mode."""
    mode = (mode or "managed").strip().lower()
    resolved_domain_id: int | None = None
    if domain_id and str(domain_id).strip().isdigit():
        resolved_domain_id = int(domain_id)

    form_state = {
        "mode": mode,
        "domain_id": resolved_domain_id,
        "subdomain": subdomain,
        "hostname": hostname,
        "target_ip": target_ip,
        "target_port": target_port,
        "protocol": protocol,
        "enable_ssl": enable_ssl,
    }

    try:
        if mode == "external":
            proxy = await proxy_service.create_external_proxy(
                db,
                hostname=hostname,
                target_ip=target_ip,
                target_port=target_port,
                protocol=protocol,
                enable_ssl=enable_ssl,
            )
        else:
            if resolved_domain_id is None:
                raise ValueError("Parent domain is required for managed mode")
            proxy = await proxy_service.create_proxy(
                db,
                domain_id=resolved_domain_id,
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
            "form": form_state,
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
