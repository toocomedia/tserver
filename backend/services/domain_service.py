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
from services import dns_service, error_service, nginx_service, ssl_service
from utils.validators import sanitize_domain
import config

logger = logging.getLogger(__name__)


from sqlalchemy import select, func

# ---------------------------------------------------------------
# QUERIES
# ---------------------------------------------------------------
async def get_all(db: AsyncSession) -> list[Domain]:
    result = await db.execute(select(Domain).order_by(Domain.created_at.desc()))
    return result.scalars().all()


from utils.pagination import paginate_query

async def get_paginated(db: AsyncSession, limit: int = config.DEFAULT_PAGE_LIMIT, offset: int = 0) -> tuple[list[Domain], int]:
    """Retrieve a page of domains using central base pagination helper."""
    stmt = select(Domain).order_by(Domain.created_at.desc())
    domains, total = await paginate_query(db, stmt, offset=offset, limit=limit)
    return list(domains), total


async def get_by_id(db: AsyncSession, domain_id: int) -> Domain:
    result = await db.execute(select(Domain).where(Domain.id == domain_id))
    domain = result.scalar_one_or_none()
    if not domain:
        raise HTTPException(status_code=404, detail="Domain not found")
    return domain


async def get_by_name(db: AsyncSession, name: str) -> Domain | None:
    result = await db.execute(select(Domain).where(Domain.name == name))
    return result.scalar_one_or_none()


async def find_parent_domain(db: AsyncSession, hostname: str) -> tuple[Domain | None, str | None]:
    """
    Given a hostname like 'app.sub.example.com', finds the closest matching
    existing parent domain in the database (e.g. 'sub.example.com' or 'example.com')
    and returns (parent_domain, relative_subdomain_prefix).
    """
    try:
        clean_name = sanitize_domain(hostname).lower()
    except Exception:
        return None, None

    parts = clean_name.split(".")
    if len(parts) <= 2:
        return None, None

    for i in range(1, len(parts) - 1):
        candidate_parent = ".".join(parts[i:])
        prefix = ".".join(parts[:i])
        parent = await get_by_name(db, candidate_parent)
        if parent:
            return parent, prefix

    return None, None


# ---------------------------------------------------------------
# CREATE
# ---------------------------------------------------------------
async def create(
    db: AsyncSession,
    name: str,
    project_type: str = "static",
    dns_mode: str = "new_zone",
    parent_domain: str | None = None,
    ns_mode: str = "panel_default",
    external_provider: str | None = None,
    external_credentials: dict | None = None,
    external_zone_ref: str | None = None,
) -> Domain:
    """
    Full domain creation:
    1. Validate name
    2. Check DB + nginx for duplicates
    3. Setup DNS (panel_default, child_ns, manual, or external)
    4. If Website (static): Create webroot + default index.html, create nginx static site, test & reload
    5. Save to DB
    """
    name = sanitize_domain(name)
    if project_type in ("static", "website"):
        project_type = "static"
    elif project_type == "dns":
        project_type = "dns"
    else:
        project_type = "static"

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

        dns_zone_created = False
        saved_parent_domain = None

        if dns_mode == "parent_record" and parent_domain:
            parent_obj = await get_by_name(db, parent_domain)
            if not parent_obj:
                raise HTTPException(status_code=400, detail=f"Specified parent domain '{parent_domain}' does not exist.")

            # Compute subdomain prefix
            if name.endswith("." + parent_domain):
                prefix = name[:-len("." + parent_domain)]
            else:
                raise HTTPException(status_code=400, detail=f"Domain '{name}' is not a subdomain of '{parent_domain}'.")

            # Ensure parent zone exists in PowerDNS before adding record
            if not parent_obj.dns_zone_created:
                await dns_service.create_zone(parent_domain)
                steps_done.append(f"dns_zone:{parent_domain}")
                parent_obj.dns_zone_created = True
                await db.flush()

            # Add A record in parent zone
            await dns_service.add_a_record(parent_domain, prefix, config.SERVER_IP)
            steps_done.append(f"dns_parent_record:{parent_domain}:{prefix}")
            dns_zone_created = False
            saved_parent_domain = parent_domain

        elif ns_mode == "manual":
            # DNS managed manually (e.g. Cloudflare / 3rd party) — no local zone
            dns_zone_created = False
            saved_parent_domain = None

        elif ns_mode == "external":
            # DNS managed via external provider plugin (e.g. Hetzner)
            from services import external_dns_bridge
            if not external_dns_bridge.plugin_active():
                raise HTTPException(status_code=400, detail="External DNS plugin is not active.")
            if not external_provider or not external_credentials:
                raise HTTPException(status_code=400, detail="External provider and credentials are required.")
            dns_zone_created = False
            saved_parent_domain = None

        elif ns_mode == "child_ns":
            # Standalone PowerDNS zone with vanity child nameservers
            await dns_service.create_zone(name)
            steps_done.append("dns_zone")

            # DNS A record for @
            await dns_service.add_a_record(name, "@", config.SERVER_IP)
            steps_done.append("dns_record")

            # A records for ns1 and ns2
            await dns_service.add_a_record(name, "ns1", config.SERVER_IP)
            await dns_service.add_a_record(name, "ns2", config.SERVER_IP)
            steps_done.append("dns_child_a")

            # Set @ NS records
            await dns_service.set_zone_nameservers(name, [f"ns1.{name}", f"ns2.{name}"])
            steps_done.append("dns_child_ns")

            dns_zone_created = True
            saved_parent_domain = None

        else:
            # Standalone PowerDNS zone with Panel Default Nameservers
            await dns_service.create_zone(name)
            steps_done.append("dns_zone")

            # DNS A record → server IP
            await dns_service.add_a_record(name, "@", config.SERVER_IP)
            steps_done.append("dns_record")

            # Default nameservers from settings/config
            ns_list = []
            if getattr(config, "DEFAULT_NS1", ""):
                ns_list.append(config.DEFAULT_NS1)
            if getattr(config, "DEFAULT_NS2", ""):
                ns_list.append(config.DEFAULT_NS2)
            if getattr(config, "DEFAULT_NS3", ""):
                ns_list.append(config.DEFAULT_NS3)

            # Fallback if no default NS configured:
            # Check if panel domain is configured
            if not ns_list:
                panel_host = getattr(config, "PANEL_DOMAIN", "")
                if panel_host and panel_host != config.SERVER_IP and "." in panel_host:
                    ns_list = [f"ns1.{panel_host}", f"ns2.{panel_host}"]

            if ns_list:
                await dns_service.set_zone_nameservers(name, ns_list)
                steps_done.append("dns_default_ns")

            dns_zone_created = True
            saved_parent_domain = None

        webroot = None
        nginx_config_path = None
        nginx_active = False

        if project_type == "static":
            # Webroot + default page
            webroot = nginx_service.create_webroot(name)
            steps_done.append("webroot")

            # Nginx config (writes + nginx -t inside; raises if fails)
            nginx_config_path = await nginx_service.create_static_site(name)
            steps_done.append("nginx_config")

            # Reload nginx
            await nginx_service.reload()
            nginx_active = True

        # Save to DB
        domain = Domain(
            name=name,
            server_ip=config.SERVER_IP,
            nginx_config_path=nginx_config_path,
            webroot_path=webroot,
            dns_zone_created=dns_zone_created,
            parent_domain=saved_parent_domain,
            nginx_active=nginx_active,
            project_type=project_type,
        )
        db.add(domain)
        await db.flush()

        if ns_mode == "external" and external_provider and external_credentials:
            from services import external_dns_bridge
            await external_dns_bridge.bind(
                db=db,
                domain=name,
                provider=external_provider,
                credentials=external_credentials,
                zone_ref=external_zone_ref or name,
            )
            steps_done.append(f"external_dns_bind:{name}")

        logger.info("Domain created: %s (type=%s, dns_mode=%s, ns_mode=%s)", name, project_type, dns_mode, ns_mode)
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
        await _rollback(name, steps_done, db=db)
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(status_code=500, detail=str(exc)) from exc


async def _rollback(name: str, steps_done: list[str], db: AsyncSession | None = None) -> None:
    """Undo completed steps in reverse order."""
    for step in reversed(steps_done):
        try:
            if step == "nginx_config":
                await nginx_service.remove_site(name)
            elif step == "webroot":
                nginx_service.remove_webroot(name)
            elif step == "dns_zone":
                await dns_service.delete_zone(name)
            elif step.startswith("external_dns_bind:") and db is not None:
                from services import external_dns_bridge
                await external_dns_bridge.unbind(db, name)
            elif step.startswith("dns_parent_record:"):
                parts = step.split(":", 2)
                parent_dom = parts[1]
                prefix = parts[2]
                await dns_service.delete_record(parent_dom, prefix, "A")
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
    4. Remove DNS zone or parent DNS record
    5. Remove from DB
    """
    from models.proxy import ReverseProxy
    from models.php_website import PhpWebsite
    from models.ssl_cert import SslCert

    domain = await get_by_id(db, domain_id)

    php_site = await db.scalar(
        select(PhpWebsite.id).where(PhpWebsite.domain_id == domain_id)
    )
    if php_site:
        raise HTTPException(
            status_code=409,
            detail="Remove the managed PHP website before deleting this domain.",
        )

    # Guard: active reverse proxies
    proxy_count = await db.scalar(
        select(ReverseProxy).where(ReverseProxy.domain_id == domain_id)
    )
    if proxy_count and not force:
        raise HTTPException(
            status_code=409,
            detail="Domain has active reverse proxies. Remove them first."
        )

    certs = (await db.scalars(
        select(SslCert).where(SslCert.domain_id == domain_id)
    )).all()
    for cert in certs:
        await ssl_service.revoke_cert(db, cert.id, delete_only=True)

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

    # Remove DNS
    if domain.dns_zone_created:
        try:
            await dns_service.delete_zone(domain.name)
        except Exception as e:
            logger.warning("DNS cleanup failed for %s: %s", domain.name, e)
    elif domain.parent_domain:
        try:
            prefix = domain.name
            if prefix.endswith("." + domain.parent_domain):
                prefix = prefix[:-len("." + domain.parent_domain)]
            await dns_service.delete_record(domain.parent_domain, prefix, "A")
        except Exception as e:
            logger.warning("DNS record cleanup failed for %s in parent %s: %s", domain.name, domain.parent_domain, e)

    await db.delete(domain)
    logger.info("Domain deleted: %s", domain.name)


# ---------------------------------------------------------------
# PAGE EDIT
# ---------------------------------------------------------------
async def update_index_html(db: AsyncSession, domain_id: int, content: str) -> None:
    """Update the domain's default HTML page."""
    domain = await get_by_id(db, domain_id)
    if domain.project_type != "static":
        raise HTTPException(409, "The default HTML editor is available only for static sites.")
    nginx_service.write_index_html(domain.name, content)
    logger.info("index.html updated for: %s", domain.name)


# ---------------------------------------------------------------
# ENABLE STATIC SITE (UPGRADE FROM DNS ONLY)
# ---------------------------------------------------------------
async def enable_static_site(db: AsyncSession, domain_id: int) -> Domain:
    """Convert a DNS-only domain to a static HTML site with Nginx vhost & webroot."""
    domain = await get_by_id(db, domain_id)
    if domain.project_type not in {"static", "dns"}:
        raise HTTPException(409, "This domain is owned by another hosting feature.")
    if domain.nginx_active:
        return domain

    nginx_service.ensure_acme_root()
    webroot = nginx_service.create_webroot(domain.name)
    nginx_config_path = await nginx_service.create_static_site(domain.name)
    await nginx_service.reload()

    domain.project_type = "static"
    domain.nginx_active = True
    domain.webroot_path = webroot
    domain.nginx_config_path = nginx_config_path
    await db.flush()
    logger.info("Static site enabled for: %s", domain.name)
    return domain


# ---------------------------------------------------------------
# CONVERT SUBDOMAIN ZONE TO PARENT RECORD
# ---------------------------------------------------------------
async def convert_zone_to_parent_record(db: AsyncSession, domain_name: str) -> Domain:
    """
    Convert an existing standalone DNS zone for a subdomain into an A record inside its parent domain's zone.
    """
    domain = await get_by_name(db, domain_name)
    if not domain:
        raise HTTPException(status_code=404, detail=f"Domain '{domain_name}' not found.")

    parent, prefix = await find_parent_domain(db, domain.name)
    if not parent:
        raise HTTPException(
            status_code=400,
            detail=f"Domain '{domain.name}' has no registered parent domain in your panel."
        )

    # Ensure parent zone exists in PowerDNS
    if not parent.dns_zone_created:
        await dns_service.create_zone(parent.name)
        parent.dns_zone_created = True
        await db.flush()

    # Add A record in parent zone
    await dns_service.add_a_record(parent.name, prefix, domain.server_ip or config.SERVER_IP)

    # Delete standalone DNS zone for this subdomain
    if domain.dns_zone_created:
        await dns_service.delete_zone(domain.name)

    domain.dns_zone_created = False
    domain.parent_domain = parent.name
    await db.commit()
    logger.info("Converted zone '%s' to A record in parent zone '%s'", domain.name, parent.name)
    return domain
