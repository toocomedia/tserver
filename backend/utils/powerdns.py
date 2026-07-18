"""
utils/powerdns.py — PowerDNS REST API client
All calls to PowerDNS go through this module.
"""
import logging
import httpx
from fastapi import HTTPException
import config

logger = logging.getLogger(__name__)

BASE = f"{config.PDNS_URL}/api/v1/servers/{config.PDNS_SERVER_ID}"
HEADERS = {"X-API-Key": config.PDNS_API_KEY, "Content-Type": "application/json"}


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(headers=HEADERS, timeout=10.0)


def _zone_name(domain: str) -> str:
    """Ensure domain ends with a dot for PowerDNS canonical format."""
    return domain.rstrip(".") + "."


def _record_name(name: str, domain: str) -> str:
    """
    Convert short name to fully-qualified.
    '@' → 'domain.' | 'www' → 'www.domain.' | already fqdn → unchanged
    """
    if name == "@":
        return _zone_name(domain)
    # Already FQDN under this zone or absolute
    if name.endswith("."):
        return name
    # User pasted full hostname without trailing dot
    zone = domain.rstrip(".").lower()
    lower = name.lower()
    if lower == zone or lower.endswith("." + zone):
        return name.rstrip(".") + "."
    return f"{name}.{domain}."


def format_record_content(rtype: str, content: str) -> str:
    """
    Normalize content for PowerDNS.
    TXT must be quoted; escape inner quotes.
    """
    content = content.strip()
    rtype = rtype.upper()
    if rtype == "TXT":
        # Already quoted (single PowerDNS string or multi-string)
        if content.startswith('"'):
            return content
        escaped = content.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return content


# ---------------------------------------------------------------
# ZONES
# ---------------------------------------------------------------
async def create_zone(domain: str) -> dict:
    """Create a new authoritative zone in PowerDNS."""
    zone = _zone_name(domain)
    payload = {
        "name": zone,
        "kind": "Native",
        "nameservers": [],
        "rrsets": [],
    }
    async with _client() as c:
        r = await c.post(f"{BASE}/zones", json=payload)
    if r.status_code not in (200, 201):
        logger.error("PDNS create_zone failed: %s %s", r.status_code, r.text)
        raise HTTPException(status_code=502, detail=f"PowerDNS error: {r.text}")
    logger.info("PDNS zone created: %s", zone)
    return r.json()


async def delete_zone(domain: str) -> None:
    """Delete a zone and all its records from PowerDNS."""
    zone = _zone_name(domain)
    async with _client() as c:
        r = await c.delete(f"{BASE}/zones/{zone}")
    if r.status_code not in (200, 204):
        logger.error("PDNS delete_zone failed: %s %s", r.status_code, r.text)
        raise HTTPException(status_code=502, detail=f"PowerDNS error: {r.text}")
    logger.info("PDNS zone deleted: %s", zone)


async def get_zone(domain: str) -> dict | None:
    """Return zone info or None if not found."""
    zone = _zone_name(domain)
    async with _client() as c:
        r = await c.get(f"{BASE}/zones/{zone}")
    if r.status_code == 404:
        return None
    if r.status_code != 200:
        raise HTTPException(status_code=502, detail=f"PowerDNS error: {r.text}")
    return r.json()


async def list_zones() -> list[dict]:
    """Return all zones."""
    async with _client() as c:
        r = await c.get(f"{BASE}/zones")
    if r.status_code != 200:
        raise HTTPException(status_code=502, detail=f"PowerDNS error: {r.text}")
    return r.json()


# ---------------------------------------------------------------
# RECORDS
# ---------------------------------------------------------------
async def add_record(
    domain: str, name: str, rtype: str, content: str, ttl: int = 3600
) -> None:
    """Add or replace a single DNS record (PATCH rrsets)."""
    await add_records(domain, name, rtype, [content], ttl)


async def add_records(
    domain: str,
    name: str,
    rtype: str,
    contents: list[str],
    ttl: int = 3600,
) -> None:
    """
    Add or replace an RRset with one or more content values.
    Multi-value types (NS, TXT, MX) should pass all values in one call
    so REPLACE does not wipe earlier members.
    """
    if not contents:
        raise HTTPException(status_code=400, detail="Record content cannot be empty")

    zone = _zone_name(domain)
    fqdn = _record_name(name, domain)
    rtype_u = rtype.upper()
    records = [
        {"content": format_record_content(rtype_u, c), "disabled": False}
        for c in contents
        if c is not None and str(c).strip()
    ]
    if not records:
        raise HTTPException(status_code=400, detail="Record content cannot be empty")

    payload = {
        "rrsets": [{
            "name": fqdn,
            "type": rtype_u,
            "ttl": ttl,
            "changetype": "REPLACE",
            "records": records,
        }]
    }
    async with _client() as c:
        r = await c.patch(f"{BASE}/zones/{zone}", json=payload)
    if r.status_code not in (200, 204):
        logger.error("PDNS add_records failed: %s %s", r.status_code, r.text)
        raise HTTPException(status_code=502, detail=f"PowerDNS record error: {r.text}")
    logger.info(
        "PDNS records set: %s %s %s → %s",
        fqdn, rtype_u, domain, [rec["content"] for rec in records],
    )


async def delete_record(domain: str, name: str, rtype: str) -> None:
    """Delete a specific DNS record (entire name+type RRset)."""
    zone = _zone_name(domain)
    fqdn = _record_name(name, domain)
    payload = {
        "rrsets": [{
            "name": fqdn,
            "type": rtype.upper(),
            "changetype": "DELETE",
        }]
    }
    async with _client() as c:
        r = await c.patch(f"{BASE}/zones/{zone}", json=payload)
    if r.status_code not in (200, 204):
        logger.error("PDNS delete_record failed: %s %s", r.status_code, r.text)
        raise HTTPException(status_code=502, detail=f"PowerDNS record error: {r.text}")
    logger.info("PDNS record deleted: %s %s", fqdn, rtype)


async def list_records(domain: str) -> list[dict]:
    """Return all rrsets for a zone."""
    zone_data = await get_zone(domain)
    if zone_data is None:
        return []
    return zone_data.get("rrsets", [])
