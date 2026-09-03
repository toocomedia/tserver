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
from services import domain_service, nginx_service, external_dns_bridge
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
    """Generic endpoint for bulk operations on domains (delete, issue_ssl, create_dns)."""
    target_ids = payload.target_ids
    if not target_ids:
        return JSONResponse({"success": False, "processed": 0, "failed": 0, "message": "No domain IDs provided."}, status_code=400)

    action = payload.action.lower().strip()
    processed = 0
    errors = []

    if action == "delete":
        # Guard: check if any domain is used by PHP websites or hosted apps
        managed_php = (await db.execute(
            select(PhpWebsite.domain_id).where(PhpWebsite.domain_id.in_(target_ids))
        )).scalars().all()
        if managed_php:
            raise HTTPException(
                409,
                "Remove managed PHP websites before deleting their domains."
            )

        managed_apps = (await db.execute(
            select(HostedApp.domain_id).where(HostedApp.domain_id.in_(target_ids))
        )).scalars().all()
        if managed_apps:
            raise HTTPException(
                409,
                "Remove hosted applications before deleting their domains."
            )

        for domain_id in target_ids:
            try:
                await domain_service.delete(db, domain_id)
                processed += 1
            except Exception as e:
                logger.error(f"Bulk delete error for domain {domain_id}: {e}")
                errors.append(f"Domain #{domain_id}: {str(e)}")

        await db.commit()

        await task_manager_service.record_completed_task(
            category="domain",
            action="bulk_delete",
            target_id="bulk",
            label=f"Bulk Delete Domains ({processed})",
            success=True,
            message=f"Bulk deleted {processed} domain(s)."
        )

        return {
            "success": len(errors) == 0 or processed > 0,
            "processed": processed,
            "failed": len(errors),
            "message": f"Successfully deleted {processed} domain(s)." + (f" ({len(errors)} failed)" if errors else ""),
            "errors": errors
        }

    elif action in ("issue_ssl", "ssl"):
        from services import ssl_service
        for domain_id in target_ids:
            domain = await db.get(Domain, domain_id)
            if not domain:
                continue
            try:
                await ssl_service.issue_cert(db, full_domain=domain.name)
                processed += 1
            except Exception as e:
                logger.error(f"Bulk SSL issuance failed for {domain.name}: {e}")
                errors.append(f"{domain.name}: {str(e)}")

        return {
            "success": len(errors) == 0 or processed > 0,
            "processed": processed,
            "failed": len(errors),
            "message": f"SSL issued for {processed} domain(s)." + (f" ({len(errors)} failed)" if errors else ""),
            "errors": errors
        }

    elif action in ("create_dns", "dns"):
        from services import dns_service
        for domain_id in target_ids:
            domain = await db.get(Domain, domain_id)
            if not domain or domain.dns_zone_created:
                continue
            try:
                await dns_service.create_zone(db, domain.name, domain.server_ip)
                processed += 1
            except Exception as e:
                logger.error(f"Bulk DNS creation failed for {domain.name}: {e}")
                errors.append(f"{domain.name}: {str(e)}")

        return {
            "success": len(errors) == 0 or processed > 0,
            "processed": processed,
            "failed": len(errors),
            "message": f"DNS zone created for {processed} domain(s)." + (f" ({len(errors)} failed)" if errors else ""),
            "errors": errors
        }

    return {"success": False, "processed": 0, "failed": len(target_ids), "message": f"Unsupported bulk action '{action}'."}




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
    external_active = external_dns_bridge.plugin_active()
    external_providers = external_dns_bridge.all_providers() if external_active else []
    return templates.TemplateResponse("pages/domains/create.html", {
        "request": request,
        "active_page": "domains",
        "server_ip": config.SERVER_IP,
        "error": None,
        "name": "",
        "project_type": "static",
        "dns_mode": "new_zone",
        "ns_mode": getattr(config, "DEFAULT_NS_MODE", "panel_default") or "panel_default",
        "default_ns1": getattr(config, "DEFAULT_NS1", "") or "",
        "default_ns2": getattr(config, "DEFAULT_NS2", "") or "",
        "default_ns3": getattr(config, "DEFAULT_NS3", "") or "",
        "external_active": external_active,
        "external_providers": external_providers,
        "parent_domain": "",
    })


# ---------------------------------------------------------------
# CREATE — submit
# ---------------------------------------------------------------
@router.post("/create", response_class=HTMLResponse)
async def domains_create(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    content_type = request.headers.get("content-type", "").lower()
    is_json = "application/json" in content_type or wants_json(request)

    external_provider = None
    external_credentials = {}
    external_zone_ref = None

    if "application/json" in content_type:
        try:
            body = await request.json()
            name = str(body.get("name") or "").strip().lower()
            project_type = str(body.get("project_type") or "static").strip().lower()
            ssl_enabled = str(body.get("ssl_enabled") or "no").strip().lower()
            dns_mode = str(body.get("dns_mode") or "new_zone").strip().lower()
            ns_mode = str(body.get("ns_mode") or "panel_default").strip().lower()
            parent_domain = body.get("parent_domain")
            external_provider = str(body.get("external_provider") or "").strip().lower() or None
            external_credentials = body.get("external_credentials") or {}
            external_zone_ref = str(body.get("external_zone_ref") or "").strip() or None
        except Exception:
            raise HTTPException(400, "Invalid JSON payload")
    else:
        form_data = await request.form()
        name = str(form_data.get("name") or "").strip().lower()
        project_type = str(form_data.get("project_type") or "static").strip().lower()
        ssl_enabled = str(form_data.get("ssl_enabled") or "no").strip().lower()
        dns_mode = str(form_data.get("dns_mode") or "new_zone").strip().lower()
        ns_mode = str(form_data.get("ns_mode") or "panel_default").strip().lower()
        parent_domain = form_data.get("parent_domain")
        external_provider = str(form_data.get("external_provider") or "").strip().lower() or None
        external_zone_ref = str(form_data.get("external_zone_ref") or "").strip() or None
        if external_provider:
            for k, v in form_data.items():
                if k.startswith("ext_cred_"):
                    external_credentials[k[len("ext_cred_"):]] = v

    if not name:
        if is_json:
            raise HTTPException(400, "Domain name is required.")
        external_active = external_dns_bridge.plugin_active()
        external_providers = external_dns_bridge.all_providers() if external_active else []
        return templates.TemplateResponse("pages/domains/create.html", {
            "request": request,
            "active_page": "domains",
            "server_ip": config.SERVER_IP,
            "error": "Domain name is required.",
            "name": "",
            "project_type": project_type,
            "dns_mode": dns_mode,
            "ns_mode": ns_mode,
            "default_ns1": getattr(config, "DEFAULT_NS1", "") or "",
            "default_ns2": getattr(config, "DEFAULT_NS2", "") or "",
            "default_ns3": getattr(config, "DEFAULT_NS3", "") or "",
            "external_active": external_active,
            "external_providers": external_providers,
            "parent_domain": parent_domain or "",
        }, status_code=400)

    clean_parent = str(parent_domain).strip() if (parent_domain and str(parent_domain).strip()) else None

    if is_json:
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
                    ns_mode=ns_mode,
                    external_provider=external_provider,
                    external_credentials=external_credentials,
                    external_zone_ref=external_zone_ref,
                )
                await bg_db.commit()
                task_rec.add_log(f"Domain {name} records and Nginx configuration created.")

                if ssl_enabled == "yes" and project_type != "dns":
                    task_rec.add_log(f"Requesting Let's Encrypt SSL certificate for {name}...")
                    try:
                        await ssl_service.issue_cert(
                            bg_db, dom.id, name, include_www=False, auto_renew=True
                        )
                        await bg_db.commit()
                        task_rec.add_log(f"SSL certificate for {name} issued successfully.")
                    except Exception as ssl_err:
                        task_rec.add_log(f"Warning: SSL certificate issuance skipped: {ssl_err}")

            task_rec.add_log(f"Domain {name} setup completed successfully.")
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
            "message": f"Creating domain {name} in background...",
        })

    try:
        domain = await domain_service.create(
            db,
            name,
            project_type=project_type,
            dns_mode=dns_mode,
            parent_domain=clean_parent,
            ns_mode=ns_mode,
            external_provider=external_provider,
            external_credentials=external_credentials,
            external_zone_ref=external_zone_ref,
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
