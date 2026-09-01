"""
services/dns_diagnostic_service.py — DNS Health & Diagnostic Engine.
Runs multi-layer verification checks for domains managed by PowerDNS:
1. PowerDNS Service & REST API status
2. Local Zone & Essential Records presence (@ A, NS, www)
3. Local Port 53 Binding
4. Local DNS Resolution (127.0.0.1)
5. VPS Cloud Firewall & Inbound Port 53 Reachability
6. Registry TLD Delegation & Glue Records
7. Global Public Resolvers (Cloudflare & Google)
"""
import asyncio
import logging
import re
import socket
import struct
from typing import Any
import httpx

import config
from utils import powerdns

logger = logging.getLogger(__name__)

# DNS Record Type Numbers
TYPE_A = 1
TYPE_NS = 2
TYPE_SOA = 6


def _build_dns_query(domain: str, qtype: int = TYPE_A) -> bytes:
    """Build a minimal DNS query packet (RFC 1035)."""
    # Header: ID=0x1234, Flags=0x0100 (Standard query, Recursion Desired), QDCOUNT=1, ANCOUNT=0, NSCOUNT=0, ARCOUNT=0
    header = struct.pack("!HHHHHH", 0x1234, 0x0100, 1, 0, 0, 0)
    # Question section: QNAME (labels) + QTYPE + QCLASS (IN=1)
    qname = b""
    for part in domain.strip(".").split("."):
        encoded = part.encode("ascii", errors="replace")
        qname += struct.pack("!B", len(encoded)) + encoded
    qname += b"\x00"
    question = qname + struct.pack("!HH", qtype, 1)
    return header + question


def _parse_dns_response_ips(data: bytes) -> list[str]:
    """Parse IPv4 addresses from a minimal DNS response packet."""
    ips = []
    if len(data) < 12:
        return ips
    ancount = struct.unpack("!H", data[6:8])[0]
    if ancount == 0:
        return ips

    # Skip header
    idx = 12
    # Skip question section QNAME
    while idx < len(data) and data[idx] != 0:
        if (data[idx] & 0xC0) == 0xC0:
            idx += 2
            break
        idx += 1 + data[idx]
    else:
        idx += 1  # null terminator
    # Skip QTYPE and QCLASS
    idx += 4

    # Parse answers
    for _ in range(ancount):
        if idx >= len(data):
            break
        # Name (could be pointer)
        if (data[idx] & 0xC0) == 0xC0:
            idx += 2
        else:
            while idx < len(data) and data[idx] != 0:
                idx += 1 + data[idx]
            idx += 1
        if idx + 10 > len(data):
            break
        rtype, rclass, ttl, rdlength = struct.unpack("!HHIH", data[idx:idx+10])
        idx += 10
        if rtype == TYPE_A and rdlength == 4 and idx + 4 <= len(data):
            ip_bytes = data[idx:idx+4]
            ips.append(socket.inet_ntoa(ip_bytes))
        idx += rdlength
    return ips


async def _udp_query_async(host: str, port: int, domain: str, qtype: int = TYPE_A, timeout: float = 2.5) -> list[str]:
    """Send UDP DNS query asynchronously."""
    loop = asyncio.get_running_loop()
    query = _build_dns_query(domain, qtype)

    def _sync_query() -> list[str]:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(timeout)
        try:
            sock.sendto(query, (host, port))
            resp, _ = sock.recvfrom(2048)
            return _parse_dns_response_ips(resp)
        except Exception:
            return []
        finally:
            sock.close()

    try:
        return await loop.run_in_executor(None, _sync_query)
    except Exception:
        return []


async def _doh_query(domain: str, rtype: str = "A") -> dict[str, Any]:
    """Query Cloudflare and Google DoH endpoints for raw response data."""
    params = {"name": domain, "type": rtype, "do": "1"}
    headers = {"Accept": "application/dns-json"}
    async with httpx.AsyncClient(timeout=4.0) as client:
        # Try Cloudflare first
        try:
            r = await client.get("https://cloudflare-dns.com/dns-query", params=params, headers=headers)
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass

        # Fallback to Google DoH
        try:
            r = await client.get("https://dns.google/resolve", params=params)
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass

    return {}


async def diagnose_domain(domain: str) -> dict[str, Any]:
    """
    Run comprehensive 7-point DNS diagnostics for a domain.
    Returns structured results with overall status, step items, and recommendations.
    """
    clean_domain = domain.strip().lower().rstrip(".")
    steps: list[dict[str, Any]] = []
    recommendations: list[str] = []

    # -------------------------------------------------------------
    # 1. PowerDNS Daemon Status
    # -------------------------------------------------------------
    pdns_ok = False
    pdns_version = "Unknown"
    try:
        async with httpx.AsyncClient(headers={"X-API-Key": config.PDNS_API_KEY}, timeout=3.0) as client:
            r = await client.get(f"{config.PDNS_URL}/api/v1/servers/{config.PDNS_SERVER_ID}")
            if r.status_code == 200:
                pdns_ok = True
                pdns_version = r.json().get("version", "Active")
    except Exception as e:
        logger.warning("DNS diag: PowerDNS API connection failed: %s", e)

    if pdns_ok:
        steps.append({
            "id": "pdns_service",
            "title": "PowerDNS Service & API",
            "status": "pass",
            "summary": f"PowerDNS daemon is running ({pdns_version})",
            "details": f"REST API responsive on {config.PDNS_URL}",
        })
    else:
        steps.append({
            "id": "pdns_service",
            "title": "PowerDNS Service & API",
            "status": "fail",
            "summary": "PowerDNS service is not responding",
            "details": f"Could not connect to PowerDNS API at {config.PDNS_URL}",
        })
        recommendations.append("Run 'sudo systemctl restart pdns' on your server to start PowerDNS.")

    # -------------------------------------------------------------
    # 2. Local Zone & Records Inspection
    # -------------------------------------------------------------
    zone_data = None
    has_a_record = False
    has_ns_records = False
    has_www = False
    ns_list: list[str] = []
    a_records: list[str] = []

    if pdns_ok:
        zone_data = await powerdns.get_zone(clean_domain)

    if zone_data:
        for rrset in zone_data.get("rrsets", []):
            rtype = rrset.get("type", "")
            rname = rrset.get("name", "").rstrip(".").lower()
            
            if rtype == "A" and rname == clean_domain:
                has_a_record = True
                for rec in rrset.get("records", []):
                    if rec.get("content"):
                        a_records.append(rec.get("content"))
            elif rtype == "NS" and rname == clean_domain:
                has_ns_records = True
                for rec in rrset.get("records", []):
                    if rec.get("content"):
                        ns_list.append(rec.get("content").rstrip("."))
            elif rname == f"www.{clean_domain}":
                has_www = True

        if has_a_record and has_ns_records:
            steps.append({
                "id": "zone_records",
                "title": "Zone & Local Records",
                "status": "pass",
                "summary": f"Configured with {len(a_records)} root A record(s) and {len(ns_list)} NS record(s)",
                "details": f"Root IP: {', '.join(a_records)} | NS: {', '.join(ns_list)}",
            })
        else:
            missing = []
            if not has_a_record:
                missing.append("Root '@' A Record")
                recommendations.append(f"Add an 'A' record for '@' pointing to your server IP ({config.SERVER_IP}).")
            if not has_ns_records:
                missing.append("NS Records")
                recommendations.append("Apply the 'Child NS' template or add NS records (ns1 & ns2).")
            steps.append({
                "id": "zone_records",
                "title": "Zone & Local Records",
                "status": "warn",
                "summary": f"Missing essential records: {', '.join(missing)}",
                "details": f"Found A: {has_a_record}, NS: {has_ns_records}, www: {has_www}",
            })
    else:
        steps.append({
            "id": "zone_records",
            "title": "Zone & Local Records",
            "status": "fail",
            "summary": f"DNS zone for '{clean_domain}' not found in PowerDNS",
            "details": "The domain is not registered as an authoritative zone in PowerDNS.",
        })
        recommendations.append(f"Create the DNS zone for '{clean_domain}' in the panel.")

    # -------------------------------------------------------------
    # 3. Port 53 Binding
    # -------------------------------------------------------------
    port_53_bound = False
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.5)
        # Attempt to bind: if address already in use (EADDRINUSE), PowerDNS holds port 53
        try:
            s.bind(("0.0.0.0", 53))
            # If bind succeeded, PowerDNS is NOT holding port 53!
            s.close()
            port_53_bound = False
        except OSError:
            # Address already in use means port 53 is bound by daemon
            port_53_bound = True
    except Exception:
        port_53_bound = pdns_ok

    if port_53_bound or pdns_ok:
        steps.append({
            "id": "port_53",
            "title": "Port 53 Binding",
            "status": "pass",
            "summary": "Port 53 (UDP/TCP) is active and listening",
            "details": "Daemon listening on 0.0.0.0:53",
        })
    else:
        steps.append({
            "id": "port_53",
            "title": "Port 53 Binding",
            "status": "fail",
            "summary": "Port 53 is not bound by PowerDNS",
            "details": "PowerDNS is not listening on UDP/TCP port 53",
        })
        recommendations.append("Run 'sudo bash /opt/srv-panel/scripts/setup_powerdns.sh' to free port 53.")

    # -------------------------------------------------------------
    # 4. Local Resolution (127.0.0.1)
    # -------------------------------------------------------------
    local_resolved_ips = await _udp_query_async("127.0.0.1", 53, clean_domain)
    if local_resolved_ips:
        steps.append({
            "id": "local_resolution",
            "title": "Local Query Resolution",
            "status": "pass",
            "summary": f"Local query succeeded → {', '.join(local_resolved_ips)}",
            "details": "PowerDNS successfully resolved domain on 127.0.0.1:53",
        })
    elif has_a_record and pdns_ok:
        steps.append({
            "id": "local_resolution",
            "title": "Local Query Resolution",
            "status": "warn",
            "summary": "Local UDP query returned no answer",
            "details": "PowerDNS is running but did not return answers on 127.0.0.1:53",
        })
    else:
        steps.append({
            "id": "local_resolution",
            "title": "Local Query Resolution",
            "status": "warn",
            "summary": "Local query skipped (zone or daemon not ready)",
            "details": "Requires active zone and running PowerDNS",
        })

    # -------------------------------------------------------------
    # 5. Public / Inbound Port 53 Reachability & Cloud Firewall
    # -------------------------------------------------------------
    server_ip = config.SERVER_IP
    external_udp_ok = False
    if server_ip and server_ip not in ("127.0.0.1", "localhost"):
        ext_ips = await _udp_query_async(server_ip, 53, clean_domain, timeout=2.0)
        if ext_ips:
            external_udp_ok = True

    # -------------------------------------------------------------
    # 6. Global Public Resolvers & Delegation Check (DoH)
    # -------------------------------------------------------------
    doh_a = await _doh_query(clean_domain, "A")
    doh_ns = await _doh_query(clean_domain, "NS")

    doh_status = doh_a.get("Status", -1)  # 0=NOERROR, 2=SERVFAIL, 3=NXDOMAIN
    doh_answers = [ans.get("data", "") for ans in doh_a.get("Answer", []) if ans.get("data")]
    doh_ns_answers = [ans.get("data", "") for ans in doh_ns.get("Answer", []) if ans.get("data")]
    
    # Check Extended DNS Errors (EDE) or Authority
    authorities = doh_a.get("Authority", []) + doh_ns.get("Authority", [])
    has_glue_or_ns = len(doh_ns_answers) > 0 or any(a.get("type") == TYPE_NS for a in authorities)

    # Evaluate Delegation / Glue
    if has_glue_or_ns:
        detected_ns = doh_ns_answers if doh_ns_answers else [a.get("data", "") for a in authorities if a.get("type") == TYPE_NS]
        steps.append({
            "id": "delegation",
            "title": "Registry Nameservers & Glue",
            "status": "pass",
            "summary": f"Delegated to: {', '.join([n.rstrip('.') for n in detected_ns[:3]])}",
            "details": f"Registry delegated nameservers found: {len(detected_ns)}",
        })
    else:
        steps.append({
            "id": "delegation",
            "title": "Registry Nameservers & Glue",
            "status": "warn",
            "summary": "No public nameserver delegation found for this domain",
            "details": "The domain registrar has not yet propagated NS / Glue records to the parent TLD registry.",
        })
        recommendations.append("Set Custom Nameservers (e.g. ns1 & ns2) and Glue Records (IP) at your domain registrar.")

    # Evaluate Inbound Firewall & Public Resolver
    if external_udp_ok or (doh_status == 0 and doh_answers):
        steps.append({
            "id": "firewall",
            "title": "Inbound UDP Port 53 Reachability",
            "status": "pass",
            "summary": "Port 53 UDP is accessible from the internet",
            "details": f"DNS queries reached server authority successfully",
        })
    elif doh_status == 2:  # SERVFAIL
        steps.append({
            "id": "firewall",
            "title": "Inbound UDP Port 53 Reachability",
            "status": "fail",
            "summary": "Public DNS returned SERVFAIL (No Reachable Authority / Port 53 Blocked)",
            "details": "Public resolvers cannot connect to your server's port 53. Likely blocked by VPS Cloud Firewall.",
        })
        recommendations.append("Open Inbound UDP Port 53 and TCP Port 53 in your VPS Cloud Provider's Firewall / Security Group.")
    elif server_ip and server_ip not in ("127.0.0.1", "localhost"):
        steps.append({
            "id": "firewall",
            "title": "Inbound UDP Port 53 Reachability",
            "status": "warn",
            "summary": "Direct external UDP probe timed out or unverified",
            "details": f"Tested public IP: {server_ip}. If domain does not resolve, check VPS Cloud Firewall.",
        })

    # Public Resolvers Result
    if doh_status == 0 and doh_answers:
        steps.append({
            "id": "public_resolvers",
            "title": "Global Public DNS (1.1.1.1 & 8.8.8.8)",
            "status": "pass",
            "summary": f"Resolving globally to: {', '.join(doh_answers)}",
            "details": "Domain is active and resolving across public DNS networks.",
        })
    elif doh_status == 0 and not doh_answers:
        steps.append({
            "id": "public_resolvers",
            "title": "Global Public DNS (1.1.1.1 & 8.8.8.8)",
            "status": "warn",
            "summary": "Public DNS returned NOERROR but no IP answer",
            "details": "Domain name is known but root 'A' record is missing or still propagating.",
        })
    elif doh_status == 2:
        steps.append({
            "id": "public_resolvers",
            "title": "Global Public DNS (1.1.1.1 & 8.8.8.8)",
            "status": "fail",
            "summary": "Public DNS returned SERVFAIL",
            "details": "Resolvers cannot reach authority servers or encountered DNSSEC validation failure.",
        })
        if not any("Firewall" in r for r in recommendations):
            recommendations.append("Ensure DNSSEC is disabled at your registrar if not configured in PowerDNS.")
    elif doh_status == 3:
        steps.append({
            "id": "public_resolvers",
            "title": "Global Public DNS (1.1.1.1 & 8.8.8.8)",
            "status": "warn",
            "summary": "Public DNS returned NXDOMAIN",
            "details": "Domain is not yet registered or parent TLD registry has not published the zone.",
        })
    else:
        steps.append({
            "id": "public_resolvers",
            "title": "Global Public DNS (1.1.1.1 & 8.8.8.8)",
            "status": "warn",
            "summary": "DNS propagation in progress",
            "details": "Public resolvers are still updating their global cache.",
        })

    # -------------------------------------------------------------
    # Overall Status Calculation
    # -------------------------------------------------------------
    fail_count = sum(1 for s in steps if s["status"] == "fail")
    warn_count = sum(1 for s in steps if s["status"] == "warn")

    if fail_count > 0:
        overall_status = "error"
        summary_text = f"Action Required: {fail_count} critical issue(s) detected blocking DNS."
    elif warn_count > 0:
        overall_status = "warning"
        summary_text = f"Propagation or minor configuration warning(s) detected ({warn_count})."
    else:
        overall_status = "healthy"
        summary_text = "All DNS checks passed. Domain is healthy and resolving globally."

    return {
        "domain": clean_domain,
        "status": overall_status,
        "summary": summary_text,
        "server_ip": config.SERVER_IP,
        "steps": steps,
        "recommendations": list(dict.fromkeys(recommendations)),  # Deduplicate
    }
