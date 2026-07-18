"""
services/domain_service.py — Domain business logic.
Orchestrates: DNS zone, webroot, nginx config, DB record.
Rollback on any failure — no orphaned state left behind.
"""
import logging
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from fastapi import HTTPException

from models.domain import Domain
from services import dns_service, nginx_service, error_service
from utils.validators import sanitize_domain
import config

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------
# QUERIES
# ---------------------------------------------------------------
async def get_all(db: AsyncSession) -> list[Domain]:
    result = await db.execute(select(Domain).order_by(Domain.created_at.desc()))
    return result.scalars().all()


async def get_by_id(db: AsyncSession, domain_id: int) -> Domain:
    result = await db.execute(select(Domain).where(Domain.id == domain_id))
    domain = result.scalar_one_or_none()
    if not domain:
        raise HTTPException(status_code=404, detail="Domain not found")
    return domain


async def get_by_name(db: AsyncSession, name: str) -> Domain | None:
    result = await db.execute(select(Domain).where(Domain.name == name))
    return result.scalar_one_or_none()


# ---------------------------------------------------------------
# CREATE
# ---------------------------------------------------------------
async def create(db: AsyncSession, name: str) -> Domain:
    """
    Full domain creation:
    1. Validate name
    2. Check DB + nginx for duplicates
    3. Create DNS zone + A record
    4. Create webroot + default index.html
    5. Create nginx config (HTTP static site)
    6. nginx -t → rollback all if fails
    7. nginx reload
    8. Save to DB
    """
    name = sanitize_domain(name)

    # Guard: already in DB
    existing = await get_by_name(db, name)
    if existing:
        raise HTTPException(status_code=409, detail=f"Domain already exists: {name}")

    # Guard: nginx server_name conflict
    if nginx_service.server_name_in_use(name):
        raise HTTPException(
            status_code=409,
            detail=f"Nginx already has a config using server_name '{name}'"
        )

    steps_done: list[str] = []

    try:
        # 1. Ensure shared acme-challenge dir exists
        nginx_service.ensure_acme_root()

        # 2. DNS zone
        await dns_service.create_zone(name)
        steps_done.append("dns_zone")

        # 3. DNS A record → server IP
        await dns_service.add_a_record(name, "@", config.SERVER_IP)
        steps_done.append("dns_record")

        # 4. Webroot + default page
        webroot = nginx_service.create_webroot(name)
        steps_done.append("webroot")

        # 5. Nginx config (writes + nginx -t inside; raises if fails)
        nginx_config_path = await nginx_service.create_static_site(name)
        steps_done.append("nginx_config")

        # 6. Reload nginx
        await nginx_service.reload()

        # 7. Save to DB
        domain = Domain(
            name=name,
            server_ip=config.SERVER_IP,
            nginx_config_path=nginx_config_path,
            webroot_path=webroot,
            dns_zone_created=True,
            nginx_active=True,
        )
        db.add(domain)
        await db.flush()
        logger.info("Domain created: %s", name)
        return domain

    except Exception as exc:
        logger.error("Domain creation failed for %s: %s — rolling back %s", name, exc, steps_done)
        detail = str(getattr(exc, "detail", exc))
        await error_service.record(
            db=db,
            level="error",
            source="domain",
            operation="create_domain",
            message=detail[:500],
            detail=detail,
            context={"domain": name, "steps_done": steps_done},
        )
        await _rollback(name, steps_done)
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(status_code=500, detail=str(exc)) from exc


async def _rollback(name: str, steps_done: list[str]) -> None:
    """Undo completed steps in reverse order."""
    for step in reversed(steps_done):
        try:
            if step == "nginx_config":
                await nginx_service.remove_site(name)
            elif step == "webroot":
                nginx_service.remove_webroot(name)
            elif step == "dns_zone":
                await dns_service.delete_zone(name)
            elif step == "dns_record":
                pass  # deleted with zone
        except Exception as e:
            logger.error("Rollback step '%s' failed: %s", step, e)


# ---------------------------------------------------------------
# DELETE
# ---------------------------------------------------------------
async def delete(db: AsyncSession, domain_id: int, force: bool = False) -> None:
    """
    Delete domain:
    1. Check no active reverse proxies
    2. Remove nginx config
    3. Remove webroot
    4. Remove DNS zone
    5. Remove from DB
    """
    from models.proxy import ReverseProxy
    from models.ssl_cert import SslCert

    domain = await get_by_id(db, domain_id)

    # Guard: active reverse proxies
    proxy_count = await db.scalar(
        select(ReverseProxy).where(ReverseProxy.domain_id == domain_id)
    )
    if proxy_count and not force:
        raise HTTPException(
            status_code=409,
            detail="Domain has active reverse proxies. Remove them first."
        )

    # Guard: active SSL certs (warn, but allow with force)
    cert = await db.scalar(
        select(SslCert).where(SslCert.domain_id == domain_id)
    )
    if cert and not force:
        raise HTTPException(
            status_code=409,
            detail="Domain has an active SSL cert. Revoke it first or use force delete."
        )

    # Remove nginx config
    try:
        await nginx_service.remove_site(domain.name)
        await nginx_service.reload()
    except Exception as e:
        logger.warning("Nginx cleanup failed for %s: %s", domain.name, e)

    # Remove webroot
    try:
        nginx_service.remove_webroot(domain.name)
    except Exception as e:
        logger.warning("Webroot cleanup failed for %s: %s", domain.name, e)

    # Remove DNS zone
    try:
        await dns_service.delete_zone(domain.name)
    except Exception as e:
        logger.warning("DNS cleanup failed for %s: %s", domain.name, e)

    await db.delete(domain)
    logger.info("Domain deleted: %s", domain.name)


# ---------------------------------------------------------------
# PAGE EDIT
# ---------------------------------------------------------------
async def update_index_html(db: AsyncSession, domain_id: int, content: str) -> None:
    """Update the domain's default HTML page."""
    domain = await get_by_id(db, domain_id)
    nginx_service.write_index_html(domain.name, content)
    logger.info("index.html updated for: %s", domain.name)
