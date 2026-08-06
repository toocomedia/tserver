"""
services/ssl_service.py — SSL certificate management via Certbot.

Strategy: certonly --webroot
  - Uses the shared /var/www/acme-challenge/ dir for HTTP-01 challenge
  - We keep full control of nginx configs (no certbot --nginx plugin)
  - After cert is issued we call nginx_service to update config to HTTPS
  - Works identically for domains and reverse-proxy subdomains
"""
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from models.domain import Domain
from models.hosted_app import HostedApp
from models.container_app import ContainerApp
from models.ssl_cert import SslCert
from models.proxy import ReverseProxy
from services import nginx_service, error_service
from utils import shell
from utils.validators import is_valid_domain
import config

logger = logging.getLogger(__name__)

_LE_LIVE = Path("/etc/letsencrypt/live")


# ---------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------
def _cert_path(domain: str) -> str:
    return str(_LE_LIVE / domain / "fullchain.pem")


def _key_path(domain: str) -> str:
    return str(_LE_LIVE / domain / "privkey.pem")


async def configure_hosted_app_ssl(
    db: AsyncSession, app: HostedApp, domain: Domain,
) -> None:
    """Attach an existing or new certificate to a hosted app's proxy."""
    cert = await db.scalar(
        select(SslCert).where(SslCert.full_domain == domain.name)
    )
    if cert is None:
        await issue_cert(db, domain.id, domain.name)
        return
    domain.nginx_config_path = await nginx_service.update_proxy_ssl(
        domain.name, "127.0.0.1", app.port, "http",
        cert.cert_path or _cert_path(domain.name), _key_path(domain.name),
    )
    app.ssl_requested = True
    await nginx_service.reload()


async def configure_container_app_ssl(
    db: AsyncSession, app: ContainerApp, domain: Domain,
) -> None:
    """Attach an existing or new certificate to a private container-app proxy."""
    cert = await db.scalar(select(SslCert).where(SslCert.full_domain == domain.name))
    if cert is None:
        await issue_cert(db, domain.id, domain.name)
        return
    domain.nginx_config_path = await nginx_service.update_proxy_ssl(
        domain.name, "127.0.0.1", app.host_port, "http",
        cert.cert_path or _cert_path(domain.name), _key_path(domain.name),
    )
    app.ssl_requested = True
    await nginx_service.reload()


def _parse_expiry_from_text(text: str) -> datetime | None:
    """
    Extract first expiry date from certbot output.
    Line format: Expiry Date: 2026-10-01 12:00:00+00:00 (VALID: 89 days)
    """
    match = re.search(
        r"Expiry Date:\s+(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}[+\-]\d{2}:\d{2})",
        text,
    )
    if match:
        try:
            return datetime.fromisoformat(match.group(1))
        except ValueError:
            pass
    return None


def _parse_expiry(certbot_output: str, domain: str) -> datetime | None:
    """Extract expiry for a domain from certbot output; optional PEM fallback via sudo."""
    # Prefer block that mentions this domain
    blocks = re.split(r"\n\s*Certificate Name:", certbot_output)
    for block in blocks:
        if domain in block:
            found = _parse_expiry_from_text(block)
            if found:
                return found
    found = _parse_expiry_from_text(certbot_output)
    if found:
        return found
    # Do not open /etc/letsencrypt as panel user — use sudo openssl if allowed
    return None


def _parse_certbot_certificates_map(output: str) -> dict[str, datetime]:
    """
    Parse `certbot certificates` into full_domain / cert-name → expiry.
    """
    result: dict[str, datetime] = {}
    # Split on Certificate Name lines
    parts = re.split(r"Certificate Name:\s*", output)
    for part in parts[1:]:
        lines = part.strip().splitlines()
        if not lines:
            continue
        cert_name = lines[0].strip()
        block = part
        expiry = _parse_expiry_from_text(block)
        if not expiry:
            continue
        result[cert_name] = expiry
        # Domains: example.com www.example.com
        m = re.search(r"Domains:\s*(.+)", block)
        if m:
            for d in m.group(1).split():
                d = d.strip().lower()
                if d:
                    result[d] = expiry
    return result


async def _certbot_expiry_map() -> dict[str, datetime]:
    """Load expiries via sudo certbot (panel cannot read /etc/letsencrypt directly)."""
    try:
        r = await shell.run(
            ["certbot", "certificates"],
            timeout=30,
        )
        if r.success or r.stdout:
            return _parse_certbot_certificates_map(r.stdout + "\n" + r.stderr)
    except Exception as e:
        logger.warning("certbot certificates failed: %s", e)
    return {}


async def _read_expiry_from_cert(cert_path: str) -> datetime | None:
    """
    Read expiry via openssl (may use sudo -n). Never open the PEM in Python —
    /etc/letsencrypt is root-only and causes PermissionError for panel user.
    """
    try:
        result = await shell.run(
            ["openssl", "x509", "-enddate", "-noout", "-in", cert_path],
            timeout=10,
        )
        if result.success:
            m = re.search(r"notAfter=(.+)", result.stdout)
            if m:
                return datetime.strptime(
                    m.group(1).strip(), "%b %d %H:%M:%S %Y %Z"
                ).replace(tzinfo=timezone.utc)
    except Exception as e:
        logger.warning("openssl expiry read failed: %s", e)
    return None


# ---------------------------------------------------------------
# LIST CERTS (from certbot + DB)
# ---------------------------------------------------------------
async def list_certs_paginated(db: AsyncSession, limit: int = 3, offset: int = 0) -> tuple[list[dict], int]:
    """Return a page of certs from DB using LIMIT and OFFSET."""
    now = datetime.now(timezone.utc)
    try:
        total_res = await db.execute(select(func.count(SslCert.id)))
        total = total_res.scalar_one_or_none() or 0
        certs = (await db.execute(select(SslCert).order_by(SslCert.created_at.desc()).offset(offset).limit(limit))).scalars().all()
    except Exception as e:
        logger.error("list_certs_paginated DB failed: %s", e)
        return [], 0

    live_map: dict[str, datetime] = {}
    needs_live_check = any(not cert.expiry_date for cert in certs)
    if needs_live_check:
        try:
            live_map = await _certbot_expiry_map()
        except Exception as e:
            logger.warning("list_certs certbot map failed: %s", e)

    result = []
    for cert in certs:
        try:
            days_left = None
            expiry = cert.expiry_date
            if not expiry:
                expiry = live_map.get(cert.full_domain)
            if expiry:
                if expiry.tzinfo is None:
                    expiry = expiry.replace(tzinfo=timezone.utc)
                days_left = (expiry - now).days

            if days_left is None:
                status = "unknown"
            elif days_left <= 0:
                status = "expired"
            elif days_left <= 15:
                status = "warning"
            else:
                status = "ok"

            result.append({
                "cert": cert,
                "days_left": days_left,
                "status": status,
            })
        except Exception as err:
            logger.error("Error processing cert %s: %s", getattr(cert, "id", "unknown"), err)

    return result, total


async def list_certs(db: AsyncSession) -> list[dict]:
    items, _ = await list_certs_paginated(db, limit=1000, offset=0)
    return items


# ---------------------------------------------------------------
# ISSUE CERT
# ---------------------------------------------------------------
async def issue_cert(
    db: AsyncSession,
    domain_id: int | None,
    full_domain: str,
    include_www: bool = False,
) -> SslCert:
    """
    Issue a Let's Encrypt cert via certbot certonly --webroot.
    Updates nginx config to HTTPS after success.
    Works for static domains, managed proxies, and external proxies.
    domain_id may be None for external reverse-proxy hosts.
    """
    full_domain = full_domain.strip().lower()

    if not is_valid_domain(full_domain):
        raise HTTPException(status_code=400, detail=f"Invalid domain name: {full_domain!r}")

    # Guard: cert already exists
    existing = await db.scalar(
        select(SslCert).where(SslCert.full_domain == full_domain)
    )
    if existing:
        if existing.cert_path:
            res = await shell.run(
                ["openssl", "x509", "-in", existing.cert_path, "-noout"],
                timeout=10,
            )
            if not res.success:
                logger.warning(f"Certificate for {full_domain} found in DB but missing on disk. Deleting stale record and regenerating.")
                await db.delete(existing)
                await db.flush()
                existing = None
        if existing:
            raise HTTPException(status_code=409, detail=f"Cert already exists for: {full_domain}")

    # Guard: nginx must exist for this exact host (not parent domain)
    if not nginx_service.config_exists(full_domain):
        raise HTTPException(
            status_code=400,
            detail=f"Nginx config not found for {full_domain}. HTTP must be active before issuing SSL."
        )

    nginx_service.ensure_acme_root()

    # Build certbot command
    cmd = [
        "certbot", "certonly",
        "--webroot",
        f"--webroot-path={config.NGINX_WEBROOT}/acme-challenge",
        "--non-interactive",
        "--agree-tos",
        f"--email={config.CERTBOT_EMAIL}",
        f"--cert-name={full_domain}",
        "-d", full_domain,
    ]
    if include_www and not full_domain.startswith("www."):
        cmd += ["-d", f"www.{full_domain}"]

    logger.info("Running certbot for: %s (www=%s)", full_domain, include_www)
    result = await shell.run(cmd, timeout=120)

    if not result.success:
        logger.error("Certbot failed for %s: %s", full_domain, result.stderr)
        await error_service.record(
            db=db,
            level="error",
            source="ssl",
            operation="issue_cert",
            message=f"Certbot failed for {full_domain}",
            detail=result.stderr or result.stdout,
            context={
                "full_domain": full_domain,
                "domain_id": domain_id,
                "include_www": include_www,
            },
        )
        raise HTTPException(
            status_code=500,
            detail=f"Certbot failed: {result.stderr[-300:]}"
        )

    logger.info("Certbot success for: %s", full_domain)

    # Parse expiry from certbot output (never open LE files as panel user)
    expiry = _parse_expiry(result.stdout + result.stderr, full_domain)
    if not expiry:
        expiry = await _read_expiry_from_cert(_cert_path(full_domain))

    cert_path = _cert_path(full_domain)
    key_path  = _key_path(full_domain)

    # Update nginx config to HTTPS — determine if domain or proxy
    domain_obj = None
    if domain_id is not None:
        domain_obj = await db.scalar(select(Domain).where(Domain.id == domain_id))
    if domain_obj is None:
        domain_obj = await db.scalar(select(Domain).where(Domain.name == full_domain))

    proxy_obj = await db.scalar(
        select(ReverseProxy).where(ReverseProxy.full_domain == full_domain)
    )
    hosted_app = None
    container_app = None
    if domain_obj:
        hosted_app = await db.scalar(
            select(HostedApp).where(HostedApp.domain_id == domain_obj.id)
        )
        container_app = await db.scalar(
            select(ContainerApp).where(ContainerApp.domain_id == domain_obj.id)
        )

    try:
        if proxy_obj:
            new_config = await nginx_service.update_proxy_ssl(
                full_domain,
                proxy_obj.target_ip,
                proxy_obj.target_port,
                proxy_obj.protocol,
                cert_path,
                key_path,
            )
            proxy_obj.ssl_enabled = True
            proxy_obj.nginx_config_path = new_config
        elif hosted_app:
            new_config = await nginx_service.update_proxy_ssl(
                full_domain, "127.0.0.1", hosted_app.port, "http", cert_path, key_path,
            )
            hosted_app.ssl_requested = True
            domain_obj.nginx_config_path = new_config
        elif container_app:
            new_config = await nginx_service.update_proxy_ssl(
                full_domain, "127.0.0.1", container_app.host_port, "http", cert_path, key_path,
            )
            container_app.ssl_requested = True
            domain_obj.nginx_config_path = new_config
        elif domain_obj and domain_obj.name == full_domain:
            new_config = await nginx_service.update_static_site_ssl(
                full_domain, cert_path, key_path
            )
            domain_obj.nginx_config_path = new_config
        else:
            logger.info("Unmanaged domain %s, generating default static SSL config", full_domain)
            await nginx_service.update_static_site_ssl(full_domain, cert_path, key_path)
        await nginx_service.reload()
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Nginx SSL config update failed after cert issue: %s", e)
        await error_service.record(
            db=db,
            level="error",
            source="ssl",
            operation="issue_cert_nginx",
            message=f"Cert issued but nginx update failed: {full_domain}",
            detail=str(e),
            context={"full_domain": full_domain, "domain_id": domain_id},
        )
        raise HTTPException(status_code=500, detail=f"Cert issued but nginx update failed: {e}")

    # Resolve domain_id for DB (nullable for external proxies)
    resolved_domain_id = domain_id
    if resolved_domain_id is None and proxy_obj is not None:
        resolved_domain_id = proxy_obj.domain_id
    if resolved_domain_id is None and domain_obj is not None:
        resolved_domain_id = domain_obj.id

    # Save cert to DB
    cert = SslCert(
        domain_id=resolved_domain_id,
        full_domain=full_domain,
        cert_path=cert_path,
        expiry_date=expiry,
        auto_renew=True,
    )
    db.add(cert)
    await db.flush()

    # Link cert to proxy if applicable
    if proxy_obj:
        proxy_obj.ssl_cert_id = cert.id

    logger.info("SSL cert saved for: %s (expiry=%s)", full_domain, expiry)
    return cert


# ---------------------------------------------------------------
# RENEW CERT
# ---------------------------------------------------------------
async def renew_cert(db: AsyncSession, cert_id: int) -> SslCert:
    """Renew a specific cert by cert-name. Updates expiry in DB."""
    cert = await db.scalar(select(SslCert).where(SslCert.id == cert_id))
    if not cert:
        raise HTTPException(status_code=404, detail="Cert not found")

    cmd = [
        "certbot", "renew",
        f"--cert-name={cert.full_domain}",
        "--non-interactive",
    ]
    result = await shell.run(cmd, timeout=120)
    if not result.success:
        raise HTTPException(
            status_code=500,
            detail=f"Certbot renew failed: {result.stderr[-300:]}"
        )

    # Refresh expiry date
    new_expiry = _parse_expiry(result.stdout + result.stderr, cert.full_domain)
    if new_expiry:
        cert.expiry_date = new_expiry

    await nginx_service.reload()
    logger.info("SSL cert renewed: %s (new expiry=%s)", cert.full_domain, new_expiry)
    return cert


# ---------------------------------------------------------------
# REVOKE CERT
# ---------------------------------------------------------------
async def revoke_cert(
    db: AsyncSession, cert_id: int, delete_only: bool = False
) -> None:
    """Revoke/delete cert, revert nginx to HTTP-only, delete from DB."""
    cert = await db.scalar(select(SslCert).where(SslCert.id == cert_id))
    if not cert:
        raise HTTPException(status_code=404, detail="Cert not found")

    domain_name = cert.full_domain

    # Delete or revoke via certbot
    if delete_only:
        cmd = [
            "certbot", "delete",
            f"--cert-name={domain_name}",
            "--non-interactive",
        ]
        timeout = 15
    else:
        cmd = [
            "certbot", "revoke",
            f"--cert-name={domain_name}",
            "--non-interactive",
            "--delete-after-revoke",
        ]
        timeout = 60
    result = await shell.run(cmd, timeout=timeout)
    if not result.success:
        logger.warning("Certbot cleanup warning for %s: %s", domain_name, result.stderr)
        # Non-fatal — continue to revert nginx

    # Revert nginx to HTTP-only
    domain_obj = await db.scalar(select(Domain).where(Domain.name == domain_name))
    proxy_obj  = await db.scalar(
        select(ReverseProxy).where(ReverseProxy.full_domain == domain_name)
    )
    hosted_app = None
    container_app = None
    if domain_obj:
        hosted_app = await db.scalar(
            select(HostedApp).where(HostedApp.domain_id == domain_obj.id)
        )
        container_app = await db.scalar(
            select(ContainerApp).where(ContainerApp.domain_id == domain_obj.id)
        )
    try:
        if proxy_obj:
            new_config = await nginx_service.create_proxy(
                proxy_obj.full_domain,
                proxy_obj.target_ip,
                proxy_obj.target_port,
                proxy_obj.protocol,
            )
            proxy_obj.ssl_enabled = False
            proxy_obj.ssl_cert_id = None
            proxy_obj.nginx_config_path = new_config
        elif hosted_app:
            new_config = await nginx_service.create_proxy(
                domain_name, "127.0.0.1", hosted_app.port, "http",
            )
            hosted_app.ssl_requested = False
            domain_obj.nginx_config_path = new_config
        elif container_app:
            new_config = await nginx_service.create_proxy(
                domain_name, "127.0.0.1", container_app.host_port, "http",
            )
            container_app.ssl_requested = False
            domain_obj.nginx_config_path = new_config
        elif domain_obj:
            from utils.nginx_templates import static_site_config
            from pathlib import Path
            import config as _cfg
            webroot = str(Path(_cfg.NGINX_WEBROOT) / domain_name / "public")
            new_config = await nginx_service.create_static_site(domain_name)
            domain_obj.nginx_config_path = new_config
        await nginx_service.reload()
    except Exception as e:
        logger.error("Nginx HTTP revert failed after revoke: %s", e)

    await db.delete(cert)
    logger.info("SSL cert revoked and deleted: %s", domain_name)
