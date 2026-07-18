"""
services/dns_service.py — DNS management via PowerDNS API.
Wraps utils/powerdns.py with business-level logic and template support.
"""
import logging
from fastapi import HTTPException
from utils import powerdns
import config

logger = logging.getLogger(__name__)


async def create_zone(domain: str) -> None:
    """Create DNS zone in PowerDNS. Raises if zone already exists."""
    existing = await powerdns.get_zone(domain)
    if existing:
        raise HTTPException(status_code=409, detail=f"DNS zone already exists: {domain}")
    await powerdns.create_zone(domain)
    logger.info("DNS zone created: %s", domain)


async def delete_zone(domain: str) -> None:
    """Delete DNS zone. Silently skips if zone not found."""
    try:
        await powerdns.delete_zone(domain)
    except HTTPException as e:
        if e.status_code == 404:
            logger.warning("DNS zone not found (skip delete): %s", domain)
            return
        raise


async def add_a_record(domain: str, name: str, ip: str, ttl: int = 3600) -> None:
    """Add an A record. name='@' for root, 'www' for subdomain prefix."""
    await powerdns.add_record(domain, name, "A", ip, ttl)


async def add_record(
    domain: str, name: str, rtype: str, content: str, ttl: int = 3600
) -> None:
    """Generic add record (single value). TXT is auto-quoted in powerdns."""
    await powerdns.add_record(domain, name, rtype, content, ttl)


async def add_records(
    domain: str, name: str, rtype: str, contents: list[str], ttl: int = 3600
) -> None:
    """Add/replace multi-value RRset (e.g. two NS records)."""
    await powerdns.add_records(domain, name, rtype, contents, ttl)


async def delete_record(domain: str, name: str, rtype: str) -> None:
    """Delete a specific record (entire name+type RRset)."""
    await powerdns.delete_record(domain, name, rtype)


async def list_records(domain: str) -> list[dict]:
    """Return all rrsets for a zone."""
    return await powerdns.list_records(domain)


async def zone_exists(domain: str) -> bool:
    """Return True if the zone exists in PowerDNS."""
    return await powerdns.get_zone(domain) is not None


async def apply_template(domain: str, template_name: str) -> list[str]:
    """
    Apply a named DNS template from config.DNS_TEMPLATES.
    Returns list of record descriptions added.
    content may be str or list[str] for multi-value RRsets.
    """
    template = config.DNS_TEMPLATES.get(template_name)
    if not template:
        raise HTTPException(status_code=400, detail=f"Unknown DNS template: {template_name}")

    fmt = {"domain": domain, "server_ip": config.SERVER_IP}
    added: list[str] = []

    for rec in template["records"]:
        raw = rec["content"]
        if isinstance(raw, list):
            contents = [c.format(**fmt) for c in raw]
            await powerdns.add_records(
                domain, rec["name"], rec["type"], contents, rec["ttl"]
            )
            for c in contents:
                added.append(f"{rec['name']} {rec['type']} {c}")
        else:
            content = raw.format(**fmt)
            await powerdns.add_record(
                domain, rec["name"], rec["type"], content, rec["ttl"]
            )
            added.append(f"{rec['name']} {rec['type']} {content}")

    logger.info(
        "DNS template '%s' applied to %s: %d records",
        template_name, domain, len(added),
    )
    return added
