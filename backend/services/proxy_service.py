"""
services/proxy_service.py — Reverse proxy business logic.
Validates inputs, enforces uniqueness, delegates cascade to cascade_service.
"""
import logging
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from fastapi import HTTPException

from models.domain import Domain
from models.proxy import ReverseProxy
from services import cascade_service, nginx_service
from utils.validators import (
    sanitize_subdomain_label,
    sanitize_domain,
    is_valid_ip,
    is_valid_port,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------
# QUERIES
# ---------------------------------------------------------------
async def get_all(db: AsyncSession) -> list[ReverseProxy]:
    result = await db.execute(
        select(ReverseProxy).order_by(ReverseProxy.created_at.desc())
    )
    return list(result.scalars().all())


async def get_by_id(db: AsyncSession, proxy_id: int) -> ReverseProxy:
    proxy = await db.scalar(
        select(ReverseProxy).where(ReverseProxy.id == proxy_id)
    )
    if not proxy:
        raise HTTPException(status_code=404, detail="Reverse proxy not found")
    return proxy


async def get_by_full_domain(
    db: AsyncSession, full_domain: str
) -> ReverseProxy | None:
    return await db.scalar(
        select(ReverseProxy).where(ReverseProxy.full_domain == full_domain)
    )


# ---------------------------------------------------------------
# CREATE — managed (panel DNS zone)
# ---------------------------------------------------------------
async def create_proxy(
    db: AsyncSession,
    domain_id: int,
    subdomain: str,
    target_ip: str,
    target_port: int,
    protocol: str = "http",
    enable_ssl: bool = False,
    cache_enabled: bool = False,
    cache_ttl_minutes: int = 10,
    cache_auto_clear_hours: int = 0,
) -> ReverseProxy:
    """
    Create reverse proxy with full cascade (DNS + nginx + optional SSL).
    Subdomain DNS points to THIS server; nginx forwards to target_ip:port.
    """
    subdomain = sanitize_subdomain_label(subdomain)
    target_ip = target_ip.strip()
    protocol = protocol.strip().lower()

    if not is_valid_ip(target_ip):
        raise HTTPException(status_code=400, detail=f"Invalid target IP: {target_ip}")
    if not is_valid_port(target_port):
        raise HTTPException(status_code=400, detail="Port must be between 1 and 65535")
    if protocol not in ("http", "https"):
        raise HTTPException(status_code=400, detail="Protocol must be http or https")

    # Parent domain must be panel-managed
    domain = await db.scalar(select(Domain).where(Domain.id == domain_id))
    if not domain:
        raise HTTPException(status_code=404, detail="Parent domain not found")
    if not domain.dns_zone_created:
        raise HTTPException(
            status_code=400,
            detail=f"DNS zone not active for {domain.name}. Fix the domain first.",
        )

    full_domain = f"{subdomain}.{domain.name}"

    # Uniqueness: DB
    existing = await get_by_full_domain(db, full_domain)
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"Proxy already exists for: {full_domain}",
        )

    # Uniqueness: must not collide with a managed root domain
    if await db.scalar(select(Domain).where(Domain.name == full_domain)):
        raise HTTPException(
            status_code=409,
            detail=f"'{full_domain}' is already a managed domain",
        )

    # Uniqueness: nginx server_name
    if nginx_service.server_name_in_use(full_domain):
        raise HTTPException(
            status_code=409,
            detail=f"Nginx already has a config using server_name '{full_domain}'",
        )

    return await cascade_service.create_reverse_proxy_full(
        db,
        domain_name=domain.name,
        domain_id=domain.id,
        subdomain=subdomain,
        full_domain=full_domain,
        target_ip=target_ip,
        target_port=target_port,
        protocol=protocol,
        enable_ssl=enable_ssl,
        dns_managed=True,
        cache_enabled=cache_enabled,
        cache_ttl_minutes=cache_ttl_minutes,
        cache_auto_clear_hours=cache_auto_clear_hours,
    )


# ---------------------------------------------------------------
# CREATE — external (DNS already points here)
# ---------------------------------------------------------------
async def create_external_proxy(
    db: AsyncSession,
    hostname: str,
    target_ip: str,
    target_port: int,
    protocol: str = "http",
    enable_ssl: bool = False,
    cache_enabled: bool = False,
    cache_ttl_minutes: int = 10,
    cache_auto_clear_hours: int = 0,
) -> ReverseProxy:
    """
    Create reverse proxy for an outside domain/subdomain whose DNS
    already points at this server. No PowerDNS writes.
    """
    try:
        full_domain = sanitize_domain(hostname)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    target_ip = target_ip.strip()
    protocol = protocol.strip().lower()

    if not is_valid_ip(target_ip):
        raise HTTPException(status_code=400, detail=f"Invalid target IP: {target_ip}")
    if not is_valid_port(target_port):
        raise HTTPException(status_code=400, detail="Port must be between 1 and 65535")
    if protocol not in ("http", "https"):
        raise HTTPException(status_code=400, detail="Protocol must be http or https")

    existing = await get_by_full_domain(db, full_domain)
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"Proxy already exists for: {full_domain}",
        )

    # Collision with managed static domain would dual-bind nginx server_name
    if await db.scalar(select(Domain).where(Domain.name == full_domain)):
        raise HTTPException(
            status_code=409,
            detail=(
                f"'{full_domain}' is already a managed domain. "
                "Use that domain's static site or remove it first."
            ),
        )

    if nginx_service.server_name_in_use(full_domain):
        raise HTTPException(
            status_code=409,
            detail=f"Nginx already has a config using server_name '{full_domain}'",
        )

    # Subdomain label for display only (first label or host)
    parts = full_domain.split(".")
    subdomain = parts[0] if len(parts) > 2 else "@"

    return await cascade_service.create_reverse_proxy_full(
        db,
        domain_name="",
        domain_id=None,
        subdomain=subdomain,
        full_domain=full_domain,
        target_ip=target_ip,
        target_port=target_port,
        protocol=protocol,
        enable_ssl=enable_ssl,
        dns_managed=False,
        cache_enabled=cache_enabled,
        cache_ttl_minutes=cache_ttl_minutes,
        cache_auto_clear_hours=cache_auto_clear_hours,
    )


# ---------------------------------------------------------------
# REWRITE NGINX CONFIG FOR ONE / ALL PROXIES
# ---------------------------------------------------------------
def _key_path_from_cert(cert_path: str | None, full_domain: str) -> tuple[str, str] | None:
    """Return (fullchain, privkey) paths, or None if SSL files cannot be derived."""
    if cert_path:
        chain = cert_path
        key = cert_path.replace("fullchain.pem", "privkey.pem").replace("fullchain", "privkey")
        return chain, key
    # Fallback to Let's Encrypt live paths by hostname
    live = f"/etc/letsencrypt/live/{full_domain}"
    return f"{live}/fullchain.pem", f"{live}/privkey.pem"


async def rewrite_proxy_nginx(db: AsyncSession, proxy: ReverseProxy) -> None:
    """
    Rewrite the nginx site config for one reverse proxy from current DB + global flags.
    Used after cache settings changes and global performance (static asset cache) toggles.
    """
    if proxy.ssl_enabled:
        from models.ssl_cert import SslCert

        cert = None
        if proxy.ssl_cert_id:
            cert = await db.scalar(
                select(SslCert).where(SslCert.id == proxy.ssl_cert_id)
            )
        paths = _key_path_from_cert(
            cert.cert_path if cert else None,
            proxy.full_domain,
        )
        if paths:
            cert_path, key_path = paths
            await nginx_service.update_proxy_ssl(
                proxy.full_domain,
                proxy.target_ip,
                proxy.target_port,
                proxy.protocol,
                cert_path,
                key_path,
                cache_enabled=bool(proxy.cache_enabled),
                cache_ttl_minutes=int(proxy.cache_ttl_minutes or 10),
            )
            return

    await nginx_service.create_proxy(
        proxy.full_domain,
        proxy.target_ip,
        proxy.target_port,
        proxy.protocol,
        cache_enabled=bool(proxy.cache_enabled),
        cache_ttl_minutes=int(proxy.cache_ttl_minutes or 10),
    )


async def regenerate_all_nginx_configs(db: AsyncSession) -> tuple[int, list[str]]:
    """
    Rewrite every reverse-proxy site config.

    Required when global NGINX_PERF_STATIC_CACHE changes: save_performance_settings
    used to only write performance.conf, leaving broken/outdated location blocks
    (e.g. proxy_pass $upstream_addr) on disk.
    """
    proxies = await get_all(db)
    ok = 0
    errors: list[str] = []
    for proxy in proxies:
        try:
            await rewrite_proxy_nginx(db, proxy)
            ok += 1
        except Exception as exc:
            msg = f"{proxy.full_domain}: {exc}"
            errors.append(msg)
            logger.warning("Proxy nginx regenerate failed: %s", msg)
    return ok, errors


# ---------------------------------------------------------------
# UPDATE CACHE SETTINGS
# ---------------------------------------------------------------
async def update_cache_settings(
    db: AsyncSession,
    proxy_id: int,
    cache_enabled: bool,
    cache_ttl_minutes: int,
    cache_auto_clear_hours: int,
) -> ReverseProxy:
    """Update cache fields and regenerate the nginx config for this proxy."""
    proxy = await get_by_id(db, proxy_id)

    proxy.cache_enabled = cache_enabled
    proxy.cache_ttl_minutes = max(1, cache_ttl_minutes)
    proxy.cache_auto_clear_hours = max(0, cache_auto_clear_hours)

    try:
        await rewrite_proxy_nginx(db, proxy)
        await nginx_service.reload()
    except Exception as exc:
        logger.warning("Cache settings nginx update failed for %s: %s", proxy.full_domain, exc)

    await db.flush()
    return proxy



# ---------------------------------------------------------------
# DELETE
# ---------------------------------------------------------------
async def delete_proxy(db: AsyncSession, proxy_id: int) -> None:
    """Remove proxy: SSL → nginx → DNS (if managed) → DB."""
    proxy = await get_by_id(db, proxy_id)
    domain_name = ""
    if proxy.domain_id:
        domain = await db.scalar(select(Domain).where(Domain.id == proxy.domain_id))
        domain_name = domain.name if domain else ""
    if not domain_name and proxy.full_domain and "." in proxy.full_domain:
        domain_name = proxy.full_domain.split(".", 1)[-1]

    await cascade_service.delete_reverse_proxy_full(db, proxy, domain_name)
