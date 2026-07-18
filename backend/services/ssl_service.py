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
from models.ssl_cert import SslCert
from models.proxy import ReverseProxy
from services import nginx_service, error_service
from utils import shell
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


def _parse_expiry(certbot_output: str, domain: str) -> datetime | None:
    """
    Extract expiry date from certbot certificates output.
    Line format: Expiry Date: 2026-10-01 12:00:00+00:00 (VALID: 89 days)
    """
    match = re.search(
        r"Expiry Date:\s+(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}[+\-]\d{2}:\d{2})",
        certbot_output,
    )
    if match:
        try:
            return datetime.fromisoformat(match.group(1))
        except ValueError:
            pass
    # Fallback: read expiry from the cert file itself
    return _read_expiry_from_cert(_cert_path(domain))


def _read_expiry_from_cert(cert_path: str) -> datetime | None:
    """Use openssl to read expiry date from cert file."""
    import subprocess
    try:
        result = subprocess.run(
            ["openssl", "x509", "-enddate", "-noout", "-in", cert_path],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            # notAfter=Oct  1 12:00:00 2026 GMT
            m = re.search(r"notAfter=(.+)", result.stdout)
            if m:
                return datetime.strptime(m.group(1).strip(), "%b %d %H:%M:%S %Y %Z").replace(
                    tzinfo=timezone.utc
                )
    except Exception as e:
        logger.warning("openssl expiry read failed: %s", e)
    return None


# ---------------------------------------------------------------
# LIST CERTS (from certbot + DB)
# ---------------------------------------------------------------
async def list_certs(db: AsyncSession) -> list[dict]:
    """
    Return all certs from DB enriched with live expiry status.
    Days remaining computed from expiry_date. Status: ok / warning / expired / issued.
    Backfills missing expiry from the PEM file when possible.
    """
    certs = (await db.execute(select(SslCert))).scalars().all()
    result = []
    now = datetime.now(timezone.utc)

    for cert in certs:
        # Backfill expiry if missing (old rows / certbot parse miss)
        if not cert.expiry_date:
            path = cert.cert_path or _cert_path(cert.full_domain)
            filled = _read_expiry_from_cert(path)
            if filled:
                cert.expiry_date = filled
                logger.info("Backfilled expiry for %s → %s", cert.full_domain, filled)

        days_left = None
        status = "issued"  # cert exists in DB even if expiry unreadable
        if cert.expiry_date:
            expiry = cert.expiry_date
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)
            delta = expiry - now
            days_left = delta.days
            if days_left > 30:
                status = "ok"
            elif days_left > 0:
                status = "warning"
            else:
                status = "expired"
        else:
            # Still no expiry — check live file
            path = cert.cert_path or _cert_path(cert.full_domain)
            if Path(path).is_file():
                status = "ok"
            else:
                status = "issued"

        result.append({
            "cert": cert,
            "days_left": days_left,
            "status": status,
        })

    return result


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

    # Guard: cert already exists
    existing = await db.scalar(
        select(SslCert).where(SslCert.full_domain == full_domain)
    )
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

    # Parse expiry from certbot output
    expiry = _parse_expiry(result.stdout + result.stderr, full_domain)

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
        elif domain_obj and domain_obj.name == full_domain:
            new_config = await nginx_service.update_static_site_ssl(
                full_domain, cert_path, key_path
            )
            domain_obj.nginx_config_path = new_config
        else:
            raise HTTPException(
                status_code=400,
                detail=f"No domain or reverse proxy found for {full_domain}",
            )
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
async def revoke_cert(db: AsyncSession, cert_id: int) -> None:
    """Revoke cert, revert nginx to HTTP-only, delete from DB."""
    cert = await db.scalar(select(SslCert).where(SslCert.id == cert_id))
    if not cert:
        raise HTTPException(status_code=404, detail="Cert not found")

    domain_name = cert.full_domain

    # Revoke via certbot
    cmd = [
        "certbot", "revoke",
        f"--cert-name={domain_name}",
        "--non-interactive",
        "--delete-after-revoke",
    ]
    result = await shell.run(cmd, timeout=60)
    if not result.success:
        logger.warning("Certbot revoke warning for %s: %s", domain_name, result.stderr)
        # Non-fatal — continue to revert nginx

    # Revert nginx to HTTP-only
    domain_obj = await db.scalar(select(Domain).where(Domain.name == domain_name))
    proxy_obj  = await db.scalar(
        select(ReverseProxy).where(ReverseProxy.full_domain == domain_name)
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
