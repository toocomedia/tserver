"""
routers/dns.py — DNS Manager routes.
Records are always fetched live from PowerDNS, not from local DB alone.
Routes call dns_service only — no direct PowerDNS calls here.
"""
import logging
from urllib.parse import quote
from typing import List, Optional
from fastapi import APIRouter, Depends, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from database import get_db
from models.domain import Domain
from services import dns_service, domain_service, dns_diagnostic_service, external_dns_bridge
from services.task_manager_service import task_manager_service
from templating import templates
from utils import powerdns
import config

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/dns", tags=["dns"])

RECORD_TYPES = ["A", "AAAA", "CNAME", "MX", "TXT", "NS", "SRV", "CAA"]

# Content label shown per record type in the UI
CONTENT_LABELS = {
    "A":     "IPv4 Address",
    "AAAA":  "IPv6 Address",
    "CNAME": "Target Hostname",
    "MX":    "Priority + Mail Server  (e.g. 10 mail.example.com.)",
    "TXT":   "Text Value",
    "NS":    "Nameserver Hostname",
    "SRV":   "Priority Weight Port Target",
    "CAA":   "Flag Tag Value",
}


# ---------------------------------------------------------------
# RESPONSE HELPERS (shared by PowerDNS + external provider paths)
# ---------------------------------------------------------------
def _wants_json(request: Request) -> bool:
    return (
        "application/json" in request.headers.get("accept", "")
        or request.headers.get("x-requested-with") == "XMLHttpRequest"
    )


def _ok_response(request: Request, domain_name: str, message: str, payload: dict | None = None):
    if _wants_json(request):
        return {"status": "ok", "message": message, **(payload or {})}
    return RedirectResponse(f"/dns/{domain_name}/records?success={quote(message)}", status_code=303)


def _err_response(request: Request, domain_name: str, error: str):
    if _wants_json(request):
        return JSONResponse({"error": error}, status_code=400)
    return RedirectResponse(f"/dns/{domain_name}/records?error={quote(error[:300])}", status_code=303)


# ---------------------------------------------------------------
# ZONES LIST
# ---------------------------------------------------------------
@router.get("/", response_class=HTMLResponse)
async def dns_index(request: Request, db: AsyncSession = Depends(get_db)):
    """Show all managed DNS zones with record counts. Excludes subdomains that are records in parent zones."""
    domains = (await db.execute(
        select(Domain).order_by(Domain.name)
    )).scalars().all()

    external_map = await external_dns_bridge.bindings_map(db)

    zones = []
    for domain in domains:
        # If this domain is a subdomain managed inside a parent zone, skip listing it as a separate standalone zone
        if domain.parent_domain and not domain.dns_zone_created:
            continue

        parent, prefix = await domain_service.find_parent_domain(db, domain.name)
        records = await dns_service.list_records(domain.name)
        zones.append({
            "domain": domain,
            "record_count": len(records),
            "zone_exists": domain.dns_zone_created,
            "parent_domain_match": parent.name if parent else None,
            "subdomain_prefix": prefix if parent else None,
            "external_provider": external_map.get(domain.name),
        })

    return templates.TemplateResponse("pages/dns/index.html", {
        "request": request,
        "active_page": "dns",
        "zones": zones,
    })


# ---------------------------------------------------------------
# RECORDS FOR A ZONE
# ---------------------------------------------------------------
@router.get("/{domain_name}/records", response_class=HTMLResponse)
async def dns_records(
    request: Request,
    domain_name: str,
    db: AsyncSession = Depends(get_db),
):
    """Show all DNS records for a zone (live from PowerDNS, or from an external provider)."""
    domain = (await db.execute(
        select(Domain).where(Domain.name == domain_name)
    )).scalar_one_or_none()

    if not domain:
        return RedirectResponse("/dns/", status_code=303)

    # If this domain is a subdomain routed to a parent zone, redirect to parent zone records
    if domain.parent_domain and not domain.dns_zone_created:
        return RedirectResponse(f"/dns/{domain.parent_domain}/records", status_code=303)

    parent, prefix = await domain_service.find_parent_domain(db, domain.name)
    external = await external_dns_bridge.view_context(db, domain_name)

    if external.get("bound"):
        # Records come from the external provider (Wix, Hetzner, ...), not PowerDNS.
        rows = external.get("rows", [])
        record_types = external.get("supported_types") or RECORD_TYPES
    else:
        records = await dns_service.list_records(domain_name)
        # Flatten rrsets into individual record rows for the table
        rows = []
        for rrset in records:
            for rec in rrset.get("records", []):
                rows.append({
                    "name":    rrset["name"].rstrip("."),
                    "type":    rrset["type"],
                    "content": rec["content"],
                    "ttl":     rrset["ttl"],
                    "managed": True,
                })
        rows.sort(key=lambda r: (r["name"], r["type"]))
        record_types = RECORD_TYPES

    return templates.TemplateResponse("pages/dns/records.html", {
        "request": request,
        "active_page": "dns",
        "domain": domain,
        "parent_domain_match": parent.name if parent else None,
        "subdomain_prefix": prefix if parent else None,
        "rows": rows,
        "record_types": record_types,
        "content_labels": CONTENT_LABELS,
        "templates": config.DNS_TEMPLATES,
        "server_ip": config.SERVER_IP,
        "external": external,
    })


# ---------------------------------------------------------------
# CONVERT SUBDOMAIN ZONE TO PARENT RECORD
# ---------------------------------------------------------------
@router.post("/{domain_name}/convert-to-record")
async def dns_convert_to_record(
    domain_name: str,
    db: AsyncSession = Depends(get_db),
):
    """Convert an existing standalone subdomain DNS zone to an A record in its parent zone."""
    try:
        domain = await domain_service.convert_zone_to_parent_record(db, domain_name)
        return RedirectResponse(
            f"/dns/{domain.parent_domain}/records?success=Converted+{domain.name}+to+record+in+{domain.parent_domain}",
            status_code=303
        )
    except Exception as exc:
        error = str(exc.detail) if hasattr(exc, "detail") else str(exc)
        return RedirectResponse(
            f"/dns/{domain_name}/records?error={error}",
            status_code=303
        )


@router.post("/api/{domain_name}/convert-to-record")
async def dns_api_convert_to_record(
    domain_name: str,
    db: AsyncSession = Depends(get_db),
):
    """API endpoint to convert standalone subdomain DNS zone to record in parent zone."""
    domain = await domain_service.convert_zone_to_parent_record(db, domain_name)
    return {
        "status": "ok",
        "domain": domain.name,
        "parent_domain": domain.parent_domain,
        "redirect_url": f"/dns/{domain.parent_domain}/records",
    }


# ---------------------------------------------------------------
# DIAGNOSE ZONE
# ---------------------------------------------------------------
@router.get("/api/{domain_name}/diagnose")
async def dns_api_diagnose(
    domain_name: str,
    db: AsyncSession = Depends(get_db),
):
    """Run full DNS diagnostic suite on a domain and return JSON."""
    result = await dns_diagnostic_service.diagnose_domain(domain_name)
    return result


# ---------------------------------------------------------------
# ADD RECORD
# ---------------------------------------------------------------
@router.post("/{domain_name}/records/add")
async def dns_add_record(
    request: Request,
    domain_name: str,
    name: str = Form(...),
    type: str = Form(...),
    content: str = Form(...),
    ttl: int = Form(3600),
    db: AsyncSession = Depends(get_db),
):
    """Add a DNS record to a zone (with smart auto-normalization & RRset merging)."""
    # Validate domain is managed
    domain = (await db.execute(
        select(Domain).where(Domain.name == domain_name)
    )).scalar_one_or_none()
    if not domain:
        return JSONResponse({"error": "Domain not found"}, status_code=404)

    rtype = type.strip().upper()

    # External provider path (Wix, Hetzner, ...) — bypass PowerDNS entirely.
    if await external_dns_bridge.is_bound(db, domain_name):
        try:
            row = await external_dns_bridge.add_record(db, domain_name, name, rtype, content, ttl)
            await task_manager_service.record_completed_task(
                category="dns", action="create",
                target_id=f"{domain_name}:{row.get('name', name)}",
                label=f"Add DNS: {row.get('name', name)} ({rtype})", success=True,
                message=f"DNS record {row.get('name', name)} ({rtype}) added to {domain_name} (external).",
            )
            return _ok_response(request, domain_name, "Record added successfully", {"record": row})
        except HTTPException as exc:
            return _err_response(request, domain_name, str(exc.detail))
        except Exception as exc:
            return _err_response(request, domain_name, str(exc))

    # Validate type
    if rtype not in RECORD_TYPES:
        return RedirectResponse(
            f"/dns/{domain_name}/records?error=Invalid+record+type",
            status_code=303
        )

    try:
        clean_name, clean_type, clean_content = dns_service.normalize_record(
            name, rtype, content, domain_name
        )
        await dns_service.add_record(domain_name, clean_name, clean_type, clean_content, ttl)
        await task_manager_service.record_completed_task(
            category="dns",
            action="create",
            target_id=f"{domain_name}:{clean_name}",
            label=f"Add DNS: {clean_name} ({clean_type})",
            success=True,
            message=f"DNS record {clean_name} ({clean_type}) added to {domain_name}.",
        )
        
        # If AJAX request, return JSON
        if "application/json" in request.headers.get("accept", "") or request.headers.get("x-requested-with") == "XMLHttpRequest":
            return {"status": "ok", "message": "Record added successfully", "name": clean_name, "type": clean_type, "content": clean_content}

        return RedirectResponse(
            f"/dns/{domain_name}/records?success=Record+added+successfully",
            status_code=303
        )
    except Exception as exc:
        error = str(exc.detail) if hasattr(exc, "detail") else str(exc)
        if "application/json" in request.headers.get("accept", ""):
            return JSONResponse({"error": error}, status_code=400)
        return RedirectResponse(
            f"/dns/{domain_name}/records?error={quote(error[:300])}",
            status_code=303
        )


# ---------------------------------------------------------------
# EDIT RECORD (external providers only)
# ---------------------------------------------------------------
@router.post("/{domain_name}/records/edit")
async def dns_edit_record(
    request: Request,
    domain_name: str,
    record_id: str = Form(...),
    name: str = Form(...),
    type: str = Form(...),
    content: str = Form(...),
    ttl: int = Form(3600),
    db: AsyncSession = Depends(get_db),
):
    """Edit an existing record on an external provider (Hetzner PUT / Wix rewrite)."""
    domain = (await db.execute(
        select(Domain).where(Domain.name == domain_name)
    )).scalar_one_or_none()
    if not domain:
        return JSONResponse({"error": "Domain not found"}, status_code=404)

    if not await external_dns_bridge.is_bound(db, domain_name):
        return _err_response(request, domain_name, "Editing is only supported for external DNS providers.")

    rtype = type.strip().upper()
    try:
        row = await external_dns_bridge.update_record(db, domain_name, record_id, name, rtype, content, ttl)
        await task_manager_service.record_completed_task(
            category="dns", action="update", target_id=f"{domain_name}:{row.get('name', name)}",
            label=f"Edit DNS: {row.get('name', name)} ({rtype})", success=True,
            message=f"DNS record {row.get('name', name)} ({rtype}) updated on {domain_name} (external).",
        )
        return _ok_response(request, domain_name, "Record updated successfully", {"record": row})
    except HTTPException as exc:
        return _err_response(request, domain_name, str(exc.detail))
    except Exception as exc:
        return _err_response(request, domain_name, str(exc))


# ---------------------------------------------------------------
# DELETE RECORD
# ---------------------------------------------------------------
@router.post("/{domain_name}/records/delete")
async def dns_delete_record(
    request: Request,
    domain_name: str,
    name: str = Form(...),
    type: str = Form(...),
    content: str | None = Form(None),
    record_id: str | None = Form(None),
    db: AsyncSession = Depends(get_db),
):
    """Delete a specific DNS record (or single value) from a zone."""
    domain = (await db.execute(
        select(Domain).where(Domain.name == domain_name)
    )).scalar_one_or_none()
    if not domain:
        if "application/json" in request.headers.get("accept", ""):
            return JSONResponse({"error": "Domain not found"}, status_code=404)
        return RedirectResponse("/dns/", status_code=303)

    rtype = type.strip().upper()

    # External provider path — delete by opaque record id (name/type/content as hints).
    if await external_dns_bridge.is_bound(db, domain_name):
        if rtype == "SOA":
            return _err_response(request, domain_name, "SOA records cannot be deleted")
        try:
            await external_dns_bridge.delete_record(db, domain_name, record_id or "", name, rtype, content or "")
            await task_manager_service.record_completed_task(
                category="dns", action="delete", target_id=f"{domain_name}:{name}",
                label=f"Delete DNS: {name} ({rtype})", success=True,
                message=f"DNS record {name} ({rtype}) deleted from {domain_name} (external).",
            )
            return _ok_response(request, domain_name, "Record deleted")
        except HTTPException as exc:
            return _err_response(request, domain_name, str(exc.detail))
        except Exception as exc:
            return _err_response(request, domain_name, str(exc))

    if rtype == "SOA":
        if "application/json" in request.headers.get("accept", ""):
            return JSONResponse({"error": "SOA records cannot be deleted"}, status_code=400)
        return RedirectResponse(
            f"/dns/{domain_name}/records?error=SOA+records+cannot+be+deleted",
            status_code=303,
        )

    # UI may pass FQDN (example.com or www.example.com); normalize to short name
    clean_name = powerdns.normalize_record_name(name, domain_name)

    try:
        await dns_service.delete_record(domain_name, clean_name, rtype, content=content)
        await task_manager_service.record_completed_task(
            category="dns",
            action="delete",
            target_id=f"{domain_name}:{clean_name}",
            label=f"Delete DNS: {clean_name} ({rtype})",
            success=True,
            message=f"DNS record {clean_name} ({rtype}) deleted from {domain_name}.",
        )
        if "application/json" in request.headers.get("accept", "") or request.headers.get("x-requested-with") == "XMLHttpRequest":
            return {"status": "ok", "message": "Record deleted successfully"}
        return RedirectResponse(
            f"/dns/{domain_name}/records?success=Record+deleted",
            status_code=303,
        )
    except Exception as exc:
        logger.warning("Delete record failed: %s", exc)
        error = str(exc.detail) if hasattr(exc, "detail") else str(exc)
        if "application/json" in request.headers.get("accept", ""):
            return JSONResponse({"error": error}, status_code=400)
        return RedirectResponse(
            f"/dns/{domain_name}/records?error={quote(error[:300])}",
            status_code=303,
        )


class DnsRecordItem(BaseModel):
    name: str
    type: str
    content: Optional[str] = None
    id: Optional[str] = None


class BulkDeleteRecordsRequest(BaseModel):
    records: List[DnsRecordItem]


@router.post("/{domain_name}/records/bulk-delete")
async def dns_bulk_delete_records(
    domain_name: str,
    payload: BulkDeleteRecordsRequest,
    db: AsyncSession = Depends(get_db),
):
    """Delete multiple DNS records from a zone."""
    domain = (await db.execute(
        select(Domain).where(Domain.name == domain_name)
    )).scalar_one_or_none()
    if not domain:
        raise HTTPException(404, "Domain not found.")

    external_bound = await external_dns_bridge.is_bound(db, domain_name)
    processed = 0
    errors = []

    for item in payload.records:
        rtype = item.type.strip().upper()
        if rtype == "SOA":
            continue
        try:
            if external_bound:
                await external_dns_bridge.delete_record(
                    db, domain_name, item.id or "", item.name, rtype, item.content or ""
                )
            else:
                clean_name = powerdns.normalize_record_name(item.name, domain_name)
                await dns_service.delete_record(domain_name, clean_name, rtype, content=item.content)
            processed += 1
        except Exception as exc:
            logger.warning("Bulk delete record failed for %s (%s): %s", item.name, rtype, exc)
            errors.append(f"{item.name} ({rtype}): {str(exc)}")

    await task_manager_service.record_completed_task(
        category="dns",
        action="bulk_delete",
        target_id=f"{domain_name}",
        label=f"Bulk Delete DNS ({processed} records)",
        success=True,
        message=f"Bulk deleted {processed} record(s) from {domain_name}.",
    )

    return {
        "success": len(errors) == 0 or processed > 0,
        "processed": processed,
        "failed": len(errors),
        "message": f"Successfully deleted {processed} record(s)." + (f" ({len(errors)} failed)" if errors else ""),
        "errors": errors
    }


# ---------------------------------------------------------------
# APPLY TEMPLATE
# ---------------------------------------------------------------
@router.post("/{domain_name}/records/template")
async def dns_apply_template(
    domain_name: str,
    template_name: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    """Apply a DNS template (adds multiple records at once)."""
    domain = (await db.execute(
        select(Domain).where(Domain.name == domain_name)
    )).scalar_one_or_none()
    if not domain:
        return RedirectResponse("/dns/", status_code=303)

    try:
        added = await dns_service.apply_template(domain_name, template_name)
        logger.info("Template '%s' applied to %s: %d records", template_name, domain_name, len(added))
        return RedirectResponse(
            f"/dns/{domain_name}/records?success=Template+applied+({len(added)}+records)",
            status_code=303
        )
    except Exception as exc:
        error = str(exc.detail) if hasattr(exc, "detail") else str(exc)
        return RedirectResponse(
            f"/dns/{domain_name}/records?error={error}",
            status_code=303
        )
