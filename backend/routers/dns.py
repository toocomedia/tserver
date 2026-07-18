"""
routers/dns.py — DNS Manager routes.
Records are always fetched live from PowerDNS, not from local DB alone.
Routes call dns_service only — no direct PowerDNS calls here.
"""
import logging
from urllib.parse import quote
from fastapi import APIRouter, Depends, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from database import get_db
from models.domain import Domain
from services import dns_service
import config

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/dns", tags=["dns"])
templates = Jinja2Templates(directory="templates")

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
# ZONES LIST
# ---------------------------------------------------------------
@router.get("/", response_class=HTMLResponse)
async def dns_index(request: Request, db: AsyncSession = Depends(get_db)):
    """Show all managed DNS zones with record counts."""
    domains = (await db.execute(
        select(Domain).order_by(Domain.name)
    )).scalars().all()

    zones = []
    for domain in domains:
        records = await dns_service.list_records(domain.name)
        zones.append({
            "domain": domain,
            "record_count": len(records),
            "zone_exists": domain.dns_zone_created,
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
    """Show all DNS records for a zone. Records fetched live from PowerDNS."""
    domain = (await db.execute(
        select(Domain).where(Domain.name == domain_name)
    )).scalar_one_or_none()

    if not domain:
        return RedirectResponse("/dns/", status_code=303)

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

    return templates.TemplateResponse("pages/dns/records.html", {
        "request": request,
        "active_page": "dns",
        "domain": domain,
        "rows": rows,
        "record_types": RECORD_TYPES,
        "content_labels": CONTENT_LABELS,
        "templates": config.DNS_TEMPLATES,
        "server_ip": config.SERVER_IP,
    })


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
    """Add a DNS record to a zone."""
    # Validate domain is managed
    domain = (await db.execute(
        select(Domain).where(Domain.name == domain_name)
    )).scalar_one_or_none()
    if not domain:
        return JSONResponse({"error": "Domain not found"}, status_code=404)

    # Validate type
    if type.upper() not in RECORD_TYPES:
        return RedirectResponse(
            f"/dns/{domain_name}/records?error=Invalid+record+type",
            status_code=303
        )

    try:
        await dns_service.add_record(domain_name, name.strip(), type.upper(), content.strip(), ttl)
        return RedirectResponse(
            f"/dns/{domain_name}/records?success=Record+added",
            status_code=303
        )
    except Exception as exc:
        error = str(exc.detail) if hasattr(exc, "detail") else str(exc)
        return RedirectResponse(
            f"/dns/{domain_name}/records?error={error}",
            status_code=303
        )


# ---------------------------------------------------------------
# DELETE RECORD
# ---------------------------------------------------------------
@router.post("/{domain_name}/records/delete")
async def dns_delete_record(
    domain_name: str,
    name: str = Form(...),
    type: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    """Delete a specific DNS record from a zone."""
    domain = (await db.execute(
        select(Domain).where(Domain.name == domain_name)
    )).scalar_one_or_none()
    if not domain:
        return RedirectResponse("/dns/", status_code=303)

    rtype = type.strip().upper()
    if rtype == "SOA":
        return RedirectResponse(
            f"/dns/{domain_name}/records?error=SOA+records+cannot+be+deleted",
            status_code=303,
        )

    # UI may pass FQDN (example.com or www.example.com); normalize to short name
    name = name.strip()
    zone = domain_name.rstrip(".").lower()
    lower = name.rstrip(".").lower()
    if lower == zone:
        short_name = "@"
    elif lower.endswith("." + zone):
        short_name = lower[: -(len(zone) + 1)]
    else:
        short_name = name

    try:
        await dns_service.delete_record(domain_name, short_name, rtype)
        return RedirectResponse(
            f"/dns/{domain_name}/records?success=Record+deleted",
            status_code=303,
        )
    except Exception as exc:
        logger.warning("Delete record failed: %s", exc)
        error = str(exc.detail) if hasattr(exc, "detail") else str(exc)
        # Keep query string short for browser URL limits
        return RedirectResponse(
            f"/dns/{domain_name}/records?error={quote(error[:300])}",
            status_code=303,
        )


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
