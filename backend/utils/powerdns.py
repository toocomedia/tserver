"""
utils/powerdns.py — PowerDNS REST API client
All calls to PowerDNS go through this module.
"""
import logging
import re
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


def normalize_record_name(name: str, domain: str) -> str:
    """
    Clean and normalize user-provided record name.
    - Strips http://, https://, trailing slashes, ports, paths
    - If user enters the full domain 'example.com' or '@' → '@'
    - If user enters 'sub.example.com' while in zone 'example.com' → 'sub'
    - Defaults empty or whitespace to '@'
    """
    if not name:
        return "@"
    
    cleaned = str(name).strip()
    # Strip URL scheme and path if pasted by accident
    cleaned = re.sub(r"^https?://", "", cleaned, flags=re.IGNORECASE)
    cleaned = cleaned.split("/")[0].split(":")[0].strip().rstrip(".")
    
    if not cleaned or cleaned == "@":
        return "@"
    
    zone = domain.rstrip(".").lower()
    cleaned_lower = cleaned.lower()
    
    if cleaned_lower == zone:
        return "@"
    
    suffix = "." + zone
    if cleaned_lower.endswith(suffix):
        prefix = cleaned[: -len(suffix)].rstrip(".")
        return prefix if prefix else "@"
        
    return cleaned


def _record_name(name: str, domain: str) -> str:
    """
    Convert short name to fully-qualified.
    '@' → 'domain.' | 'www' → 'www.domain.' | already fqdn → unchanged
    """
    normalized = normalize_record_name(name, domain)
    if normalized == "@":
        return _zone_name(domain)
    # Already FQDN under this zone or absolute
    if normalized.endswith("."):
        return normalized
    return f"{normalized}.{domain.rstrip('.') }."


def _clean_url_to_host(val: str) -> str:
    """Extract host/IP from URL strings if pasted."""
    s = val.strip()
    # Remove protocol
    s = re.sub(r"^https?://", "", s, flags=re.IGNORECASE)
    # Remove path / query
    s = s.split("/")[0].strip()
    return s


def format_record_content(rtype: str, content: str, domain: str = "") -> str:
    """
    Smart normalization and auto-correction for DNS record content.
    - A: Extracts IPv4 from URLs (http://1.2.3.4:80/path), strips CIDR (/32), ports, spaces.
    - AAAA: Extracts IPv6 from URLs/brackets, strips CIDR (/64), ports.
    - NS / CNAME: Strips URLs, ensures trailing dot for hostnames (ns1.example.com → ns1.example.com.).
    - MX: Auto-defaults missing priority (mail.example.com → 10 mail.example.com.) and adds trailing dot.
    - TXT: Safely escapes inner quotes, wraps in quotes without double-quoting.
    - CAA: Auto-formats missing flag (issue letsencrypt.org → 0 issue "letsencrypt.org").
    - SRV: Ensures target hostname ends with a dot.
    """
    if content is None:
        return ""
    
    content = str(content).strip()
    rtype = rtype.upper()

    if rtype == "A":
        # Strip URL, paths, and ports
        host = _clean_url_to_host(content)
        # Strip port if present e.g. 1.2.3.4:8080
        if ":" in host and not host.startswith("["):
            host = host.split(":")[0]
        # Strip CIDR notation if present e.g. 1.2.3.4/32
        if "/" in host:
            host = host.split("/")[0]
        return host.strip()

    elif rtype == "AAAA":
        if "[" in content and "]" in content:
            match = re.search(r"\[([a-fA-F0-9:]+)\]", content)
            if match:
                return match.group(1).strip()
        host = _clean_url_to_host(content)
        host = host.lstrip("[").rstrip("]")
        if "/" in host:
            host = host.split("/")[0]
        return host.strip()

    elif rtype in ("CNAME", "NS"):
        host = _clean_url_to_host(content)
        # Strip port if present
        if ":" in host and not host.startswith("["):
            host = host.split(":")[0]
        # If user typed '@' and domain is known
        if host == "@" and domain:
            return _zone_name(domain)
        # Lowercase
        host = host.lower()
        # Auto-append trailing dot if it's a hostname with at least one dot
        if "." in host and not host.endswith("."):
            host = host + "."
        return host

    elif rtype == "MX":
        # User might input "10 mail.example.com" or just "mail.example.com"
        parts = content.split()
        priority = 10
        target = ""
        if len(parts) == 1:
            # Missing priority, assume 10
            target = parts[0]
        elif len(parts) >= 2 and parts[0].isdigit():
            priority = int(parts[0])
            target = parts[1]
        else:
            # Fallback
            target = parts[-1]

        target = _clean_url_to_host(target)
        if ":" in target:
            target = target.split(":")[0]
        target = target.lower()
        if "." in target and not target.endswith("."):
            target = target + "."
        return f"{priority} {target}"

    elif rtype == "TXT":
        # Strip trailing semicolon from copied BIND records
        cleaned = content.rstrip(";").strip()
        # If already fully enclosed in quotes
        if cleaned.startswith('"') and cleaned.endswith('"') and len(cleaned) >= 2:
            inner = cleaned[1:-1]
            escaped = inner.replace('\\"', '"').replace('"', '\\"')
            return f'"{escaped}"'
        escaped = cleaned.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'

    elif rtype == "CAA":
        # Format: <flag> <tag> "<value>"
        # e.g. '0 issue "letsencrypt.org"' or user typed 'issue letsencrypt.org'
        cleaned = content.rstrip(";").strip()
        parts = cleaned.split(None, 2)
        if len(parts) == 2 and not parts[0].isdigit():
            # e.g. 'issue letsencrypt.org' -> add flag 0
            tag, val = parts[0], parts[1].strip('"')
            return f'0 {tag} "{val}"'
        elif len(parts) == 3 and parts[0].isdigit():
            flag, tag, val = parts[0], parts[1], parts[2].strip('"')
            return f'{flag} {tag} "{val}"'
        return cleaned

    elif rtype == "SRV":
        # Format: <prio> <weight> <port> <target.>
        parts = content.split()
        if len(parts) == 4:
            target = parts[3].lower()
            if "." in target and not target.endswith("."):
                target = target + "."
            return f"{parts[0]} {parts[1]} {parts[2]} {target}"
        return content

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
    """
    Add a single DNS record.
    If an RRset with the same (name, rtype) already exists, MERGE this new content
    into the existing RRset rather than overwriting existing records.
    """
    zone = _zone_name(domain)
    fqdn = _record_name(name, domain)
    rtype_u = rtype.upper()
    formatted = format_record_content(rtype_u, content, domain)
    
    if not formatted:
        raise HTTPException(status_code=400, detail="Record content cannot be empty")

    # Fetch existing zone to check for existing RRset members
    existing_records = []
    zone_data = await get_zone(domain)
    if zone_data:
        for rrset in zone_data.get("rrsets", []):
            if rrset.get("name") == fqdn and rrset.get("type") == rtype_u:
                for rec in rrset.get("records", []):
                    c_val = rec.get("content", "")
                    if c_val and c_val not in existing_records:
                        existing_records.append(c_val)
                # Retain existing TTL if not explicitly changed
                if not ttl or ttl == 3600:
                    ttl = rrset.get("ttl", 3600)
                break

    if formatted in existing_records:
        # Idempotent: already exists
        logger.info("PDNS record already present: %s %s %s", fqdn, rtype_u, formatted)
        return

    merged_contents = existing_records + [formatted]
    await _apply_rrset(zone, fqdn, rtype_u, merged_contents, ttl)


async def add_records(
    domain: str,
    name: str,
    rtype: str,
    contents: list[str],
    ttl: int = 3600,
) -> None:
    """
    Set/Replace an entire RRset with one or more content values directly.
    """
    if not contents:
        raise HTTPException(status_code=400, detail="Record content cannot be empty")

    zone = _zone_name(domain)
    fqdn = _record_name(name, domain)
    rtype_u = rtype.upper()
    formatted_list = [
        format_record_content(rtype_u, c, domain)
        for c in contents
        if c is not None and str(c).strip()
    ]
    if not formatted_list:
        raise HTTPException(status_code=400, detail="Record content cannot be empty")

    await _apply_rrset(zone, fqdn, rtype_u, formatted_list, ttl)


async def _apply_rrset(
    zone: str,
    fqdn: str,
    rtype_u: str,
    contents: list[str],
    ttl: int = 3600,
) -> None:
    records = [{"content": c, "disabled": False} for c in contents if c]
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
        logger.error("PDNS _apply_rrset failed: %s %s", r.status_code, r.text)
        raise HTTPException(status_code=502, detail=f"PowerDNS record error: {r.text}")
    logger.info("PDNS records applied: %s %s → %s", fqdn, rtype_u, contents)


async def delete_record(
    domain: str, name: str, rtype: str, content: str | None = None
) -> None:
    """
    Delete a specific DNS record.
    - If content is provided and the RRset has multiple records, removes only that value.
    - If content is None or it was the last record in the RRset, sends DELETE for the entire RRset.
    """
    zone = _zone_name(domain)
    fqdn = _record_name(name, domain)
    rtype_u = rtype.upper()

    if content:
        norm_content = format_record_content(rtype_u, content, domain)
        zone_data = await get_zone(domain)
        if zone_data:
            for rrset in zone_data.get("rrsets", []):
                if rrset.get("name") == fqdn and rrset.get("type") == rtype_u:
                    all_records = [
                        r.get("content") for r in rrset.get("records", [])
                        if r.get("content")
                    ]
                    # Filter out the matching content
                    remaining = [c for c in all_records if c != norm_content and c != content.strip()]
                    if remaining:
                        # Update RRset with remaining values
                        await _apply_rrset(zone, fqdn, rtype_u, remaining, rrset.get("ttl", 3600))
                        logger.info("PDNS single record value deleted: %s %s %s (remaining: %s)", fqdn, rtype_u, norm_content, remaining)
                        return

    # Delete entire RRset
    payload = {
        "rrsets": [{
            "name": fqdn,
            "type": rtype_u,
            "changetype": "DELETE",
        }]
    }
    async with _client() as c:
        r = await c.patch(f"{BASE}/zones/{zone}", json=payload)
    if r.status_code not in (200, 204):
        logger.error("PDNS delete_record failed: %s %s", r.status_code, r.text)
        raise HTTPException(status_code=502, detail=f"PowerDNS record error: {r.text}")
    logger.info("PDNS RRset deleted: %s %s", fqdn, rtype_u)


async def list_records(domain: str) -> list[dict]:
    """Return all rrsets for a zone."""
    zone_data = await get_zone(domain)
    if zone_data is None:
        return []
    return zone_data.get("rrsets", [])

