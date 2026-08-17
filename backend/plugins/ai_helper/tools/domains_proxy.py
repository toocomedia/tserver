"""
tools/domains_proxy.py — Tool handlers for Domains, SSL Certificates, and Reverse Proxies.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.domain import Domain
from models.proxy import ReverseProxy
from models.ssl_cert import SslCert


async def get_domains_and_ssl(
    db: AsyncSession,
    domain_name: Optional[str] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Retrieves list of domains and their SSL status."""
    stmt = select(Domain)
    if domain_name:
        stmt = stmt.where(Domain.name.ilike(f"%{domain_name.strip()}%"))
    
    result = await db.execute(stmt)
    domains = result.scalars().all()

    # Query SSL certs mapping
    cert_stmt = select(SslCert)
    cert_result = await db.execute(cert_stmt)
    certs = {c.full_domain: c for c in cert_result.scalars().all()}

    output = []
    for d in domains:
        ssl_info = certs.get(d.name)
        output.append({
            "id": d.id,
            "domain": d.name,
            "server_ip": d.server_ip,
            "project_type": d.project_type,
            "nginx_active": d.nginx_active,
            "webroot": d.webroot_path,
            "ssl": {
                "active": ssl_info is not None,
                "expires_at": ssl_info.expiry_date.isoformat() if (ssl_info and ssl_info.expiry_date) else None,
                "auto_renew": ssl_info.auto_renew if ssl_info else False,
            }
        })

    return {
        "status": "ok",
        "count": len(output),
        "domains": output,
    }


async def get_reverse_proxy_routes(
    db: AsyncSession,
    domain: Optional[str] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Retrieves Nginx reverse proxy routes and upstreams."""
    stmt = select(ReverseProxy)
    if domain:
        stmt = stmt.where(ReverseProxy.full_domain.ilike(f"%{domain.strip()}%"))

    result = await db.execute(stmt)
    proxies = result.scalars().all()

    output = []
    for p in proxies:
        output.append({
            "id": p.id,
            "full_domain": p.full_domain,
            "subdomain": p.subdomain,
            "upstream_target": f"{p.protocol}://{p.target_ip}:{p.target_port}",
            "target_ip": p.target_ip,
            "target_port": p.target_port,
            "protocol": p.protocol,
            "ssl_enabled": p.ssl_enabled,
            "cache_enabled": p.cache_enabled,
            "dns_managed": p.dns_managed,
        })

    return {
        "status": "ok",
        "count": len(output),
        "reverse_proxies": output,
    }
