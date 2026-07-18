"""
utils/powerdns.py — PowerDNS REST API client
All calls to PowerDNS go through this module.
Max 200 lines — split if exceeded.
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
    if not name.endswith("."):
        return f"{name}.{domain}."
    return name


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
    zone = _zone_name(domain)
    fqdn = _record_name(name, domain)
    payload = {
        "rrsets": [{
            "name": fqdn,
            "type": rtype.upper(),
            "ttl": ttl,
            "changetype": "REPLACE",
            "records": [{"content": content, "disabled": False}],
        }]
    }
    async with _client() as c:
        r = await c.patch(f"{BASE}/zones/{zone}", json=payload)
    if r.status_code not in (200, 204):
        logger.error("PDNS add_record failed: %s %s", r.status_code, r.text)
        raise HTTPException(status_code=502, detail=f"PowerDNS record error: {r.text}")
    logger.info("PDNS record added: %s %s %s → %s", fqdn, rtype, domain, content)


async def delete_record(domain: str, name: str, rtype: str) -> None:
    """Delete a specific DNS record."""
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
