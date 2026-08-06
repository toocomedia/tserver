"""
routers/proxy.py — Reverse Proxy Manager routes.
Routes call proxy_service only — no direct nginx/DNS/SSL calls here.
"""
import logging
import asyncio
import os
from fastapi import APIRouter, Depends, Request, Form, Query
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from database import get_db
from models.domain import Domain
from services import proxy_service, dns_service, nginx_service, cache_service
from templating import templates
import config

from utils.search_and_bulk import execute_bulk_action, BulkActionRequest
from models.proxy import ReverseProxy

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/proxy", tags=["proxy"])


@router.post("/api/bulk")
async def proxy_bulk_action(payload: BulkActionRequest, db: AsyncSession = Depends(get_db)):
    """Generic endpoint for bulk operations on reverse proxies."""
    result = await execute_bulk_action(db, ReverseProxy, payload.action, payload.item_ids)
    return result



# ---------------------------------------------------------------
# LIST (DB LIMIT + OFFSET PAGINATED)
# ---------------------------------------------------------------
@router.get("/", response_class=HTMLResponse)
async def proxy_index(
    request: Request,
    offset: int = 0,
    limit: int = 6,
    db: AsyncSession = Depends(get_db)
):
    """Show reverse proxies with live nginx/DNS status using DB LIMIT and OFFSET."""
    proxies, total = await proxy_service.get_paginated(db, limit=limit, offset=offset)
    rows = []

    # Batch fetch all domains in a single query
    domain_map = {d.id: d for d in (await db.execute(select(Domain))).scalars().all()}
    # Batch check enabled nginx site configs in memory
    enabled_sites = set(os.listdir(config.NGINX_SITES_ENABLED)) if os.path.exists(config.NGINX_SITES_ENABLED) else set()
    # Request-scoped DNS record cache to avoid calling DNS service repeatedly for same domain
    dns_cache = {}

    for p in proxies:
        domain = domain_map.get(p.domain_id) if p.domain_id is not None else None

        dns_managed = getattr(p, "dns_managed", True)
        dns_ok = False
        dns_status = "external"

        if not dns_managed:
            dns_ok = True  # external DNS is user-managed
            dns_status = "external"
        elif domain:
            dns_status = "missing"
            try:
                if domain.name not in dns_cache:
                    dns_cache[domain.name] = await dns_service.list_records(domain.name)
                rrsets = dns_cache[domain.name]
                fqdn = f"{p.subdomain}.{domain.name}."
                for rr in rrsets:
                    if rr.get("type") == "A" and rr.get("name", "").rstrip(".") == fqdn.rstrip("."):
                        dns_ok = True
                        dns_status = "active"
                        break
            except Exception as e:
                logger.warning("DNS status check failed for %s: %s", p.full_domain, e)
                dns_cache[domain.name] = []
        else:
            dns_status = "missing"

        cache_enabled = getattr(p, "cache_enabled", False)
        cache_size_mb = 0.0
        if cache_enabled:
            cache_size_mb = await asyncio.to_thread(cache_service.get_cache_size_mb, p.full_domain)

        rows.append({
            "proxy": p,
            "domain_name": domain.name if domain else "External",
            "nginx_active": f"{p.full_domain}.conf" in enabled_sites,
            "dns_ok": dns_ok,
            "dns_status": dns_status,
            "dns_managed": dns_managed,
            "cache_enabled": cache_enabled,
            "cache_size_mb": cache_size_mb,
            "last_cache_cleared": getattr(p, "last_cache_cleared", None),
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
    cache_enabled: bool = Form(False),
    cache_ttl_minutes: int = Form(10),
    cache_auto_clear_hours: int = Form(0),
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
        "cache_enabled": cache_enabled,
        "cache_ttl_minutes": cache_ttl_minutes,
        "cache_auto_clear_hours": cache_auto_clear_hours,
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
                cache_enabled=cache_enabled,
                cache_ttl_minutes=cache_ttl_minutes,
                cache_auto_clear_hours=cache_auto_clear_hours,
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
                cache_enabled=cache_enabled,
                cache_ttl_minutes=cache_ttl_minutes,
                cache_auto_clear_hours=cache_auto_clear_hours,
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
async def proxy_delete(
    proxy_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Delete proxy with full cleanup cascade."""
    is_ajax = (
        request.headers.get("x-requested-with") == "XMLHttpRequest"
        or "application/json" in request.headers.get("accept", "")
    )
    try:
        await proxy_service.delete_proxy(db, proxy_id)
        if is_ajax:
            return JSONResponse({"success": True, "message": "Reverse proxy deleted."})
        return RedirectResponse("/proxy/?deleted=1", status_code=303)
    except Exception as exc:
        error = str(exc.detail) if hasattr(exc, "detail") else str(exc)
        if is_ajax:
            return JSONResponse({"success": False, "error": error}, status_code=400)
        return RedirectResponse(f"/proxy/?error={error}", status_code=303)


# ---------------------------------------------------------------
# CACHE — PURGE
# ---------------------------------------------------------------
@router.post("/{proxy_id}/cache/purge")
async def proxy_cache_purge(
    proxy_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Manually purge the Nginx cache for a specific proxy."""
    proxy = await proxy_service.get_by_id(db, proxy_id)
    purged = await cache_service.purge_proxy_cache(proxy.full_domain)
    size_mb = cache_service.get_cache_size_mb(proxy.full_domain)
    return JSONResponse({
        "ok": True,
        "purged": purged,
        "cache_size_mb": size_mb,
        "message": "Cache purged." if purged else "Cache was already empty.",
    })


# ---------------------------------------------------------------
# CACHE — SETTINGS
# ---------------------------------------------------------------
@router.post("/{proxy_id}/cache/settings")
async def proxy_cache_settings(
    proxy_id: int,
    cache_enabled: bool = Form(False),
    cache_ttl_minutes: int = Form(10),
    cache_auto_clear_hours: int = Form(0),
    db: AsyncSession = Depends(get_db),
):
    """Save cache settings for a specific proxy and regenerate nginx config."""
    proxy = await proxy_service.update_cache_settings(
        db,
        proxy_id=proxy_id,
        cache_enabled=cache_enabled,
        cache_ttl_minutes=max(1, cache_ttl_minutes),
        cache_auto_clear_hours=max(0, cache_auto_clear_hours),
    )
    size_mb = cache_service.get_cache_size_mb(proxy.full_domain)
    return JSONResponse({
        "ok": True,
        "cache_enabled": proxy.cache_enabled,
        "cache_ttl_minutes": proxy.cache_ttl_minutes,
        "cache_auto_clear_hours": proxy.cache_auto_clear_hours,
        "cache_size_mb": size_mb,
        "message": "Cache settings saved.",
    })
