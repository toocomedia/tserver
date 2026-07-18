"""
services/cascade_service.py — Multi-step orchestration with rollback.

Used by proxy (and future cross-module flows) so partial failures
never leave orphaned DNS / nginx / SSL state.
"""
import logging
from typing import Awaitable, Callable

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.proxy import ReverseProxy
from models.ssl_cert import SslCert
from services import dns_service, nginx_service, ssl_service, error_service
import config

logger = logging.getLogger(__name__)

RollbackFn = Callable[[], Awaitable[None]]


async def _safe_rollback(name: str, fn: RollbackFn) -> None:
    try:
        await fn()
    except Exception as e:
        logger.error("Rollback step '%s' failed: %s", name, e)


async def create_reverse_proxy_full(
    db: AsyncSession,
    *,
    domain_name: str,
    domain_id: int | None,
    subdomain: str,
    full_domain: str,
    target_ip: str,
    target_port: int,
    protocol: str,
    enable_ssl: bool,
    dns_managed: bool = True,
) -> ReverseProxy:
    """
    Atomic reverse-proxy create:
      [optional DNS A] → nginx config → reload → DB row → optional SSL
    On failure, undoes completed steps in reverse order.
    """
    steps_done: list[str] = []

    try:
        # 1. DNS: only when panel manages the parent zone
        if dns_managed:
            if not domain_name or not subdomain:
                raise HTTPException(
                    status_code=400,
                    detail="Managed proxy requires parent domain and subdomain",
                )
            await dns_service.add_record(
                domain_name, subdomain, "A", config.SERVER_IP
            )
            steps_done.append("dns")

        # 2. Nginx reverse-proxy config (+ nginx -t inside)
        nginx_service.ensure_acme_root()
        config_path = await nginx_service.create_proxy(
            full_domain, target_ip, target_port, protocol
        )
        steps_done.append("nginx")

        # 3. Reload
        await nginx_service.reload()

        # 4. Persist proxy row (needed before SSL so issue_cert can find it)
        proxy = ReverseProxy(
            domain_id=domain_id,
            subdomain=subdomain or "",
            full_domain=full_domain,
            target_ip=target_ip,
            target_port=target_port,
            protocol=protocol,
            ssl_enabled=False,
            ssl_cert_id=None,
            nginx_config_path=config_path,
            dns_managed=dns_managed,
        )
        db.add(proxy)
        await db.flush()
        steps_done.append("db")

        # 5. Optional SSL (updates nginx + links cert to proxy)
        if enable_ssl:
            await ssl_service.issue_cert(db, domain_id, full_domain, include_www=False)
            steps_done.append("ssl")
            await db.refresh(proxy)

        logger.info(
            "Reverse proxy created: %s → %s://%s:%s (ssl=%s dns_managed=%s)",
            full_domain, protocol, target_ip, target_port, enable_ssl, dns_managed,
        )
        return proxy

    except Exception as exc:
        logger.error(
            "Proxy create failed for %s: %s — rolling back %s",
            full_domain, exc, steps_done,
        )
        detail = str(getattr(exc, "detail", exc))
        await error_service.record(
            db=db,
            level="error",
            source="proxy",
            operation="create_proxy",
            message=detail[:500],
            detail=detail,
            context={
                "full_domain": full_domain,
                "domain_name": domain_name,
                "subdomain": subdomain,
                "target_ip": target_ip,
                "target_port": target_port,
                "protocol": protocol,
                "enable_ssl": enable_ssl,
                "dns_managed": dns_managed,
                "steps_done": steps_done,
            },
        )
        await _rollback_create(
            db,
            domain_name=domain_name,
            subdomain=subdomain,
            full_domain=full_domain,
            dns_managed=dns_managed,
            steps_done=steps_done,
        )
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(status_code=500, detail=str(exc)) from exc


async def _rollback_create(
    db: AsyncSession,
    *,
    domain_name: str,
    subdomain: str,
    full_domain: str,
    dns_managed: bool,
    steps_done: list[str],
) -> None:
    """Undo create steps in reverse order."""
    for step in reversed(steps_done):
        if step == "ssl":
            cert = await db.scalar(
                select(SslCert).where(SslCert.full_domain == full_domain)
            )
            if cert:
                await _safe_rollback(
                    "ssl",
                    lambda c=cert: ssl_service.revoke_cert(db, c.id),
                )
        elif step == "db":
            proxy = await db.scalar(
                select(ReverseProxy).where(ReverseProxy.full_domain == full_domain)
            )
            if proxy:
                await db.delete(proxy)
                await db.flush()
        elif step == "nginx":
            await _safe_rollback(
                "nginx",
                lambda: nginx_service.remove_site(full_domain),
            )
            try:
                await nginx_service.reload()
            except Exception as e:
                logger.error("Nginx reload after rollback failed: %s", e)
        elif step == "dns" and dns_managed:
            await _safe_rollback(
                "dns",
                lambda: dns_service.delete_record(domain_name, subdomain, "A"),
            )


async def delete_reverse_proxy_full(
    db: AsyncSession,
    proxy: ReverseProxy,
    domain_name: str,
) -> None:
    """
    Full proxy teardown:
      revoke SSL (if any) → remove nginx → remove DNS (if managed) → delete DB row
    Best-effort cleanup; continues even if individual steps warn.
    """
    full_domain = proxy.full_domain
    subdomain = proxy.subdomain
    dns_managed = getattr(proxy, "dns_managed", True)

    # 1. Revoke SSL if linked (or orphaned cert for this host)
    cert_id = proxy.ssl_cert_id
    if not cert_id:
        cert = await db.scalar(
            select(SslCert).where(SslCert.full_domain == full_domain)
        )
        cert_id = cert.id if cert else None

    if cert_id:
        try:
            await ssl_service.revoke_cert(db, cert_id)
        except Exception as e:
            logger.warning("SSL revoke during proxy delete failed: %s", e)

    # 2. Nginx config
    try:
        await nginx_service.remove_site(full_domain)
        await nginx_service.reload()
    except Exception as e:
        logger.warning("Nginx cleanup for %s failed: %s", full_domain, e)

    # 3. DNS A record for subdomain (managed only)
    if dns_managed and domain_name and subdomain:
        try:
            await dns_service.delete_record(domain_name, subdomain, "A")
        except Exception as e:
            logger.warning("DNS cleanup for %s failed: %s", full_domain, e)

    # 4. DB
    await db.delete(proxy)
    logger.info("Reverse proxy deleted: %s", full_domain)
