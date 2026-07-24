"""
backend/plugins/maddy/router.py — APIRouter for Maddy Mail Server plugin.
Exposes Mail Management UI and endpoints for accounts CRUD, DNS records,
and SSL certificate provisioning.
"""
import asyncio
import os
import logging
import subprocess
from pathlib import Path

from fastapi import APIRouter, Request, Form, Depends, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from database import AsyncSessionLocal, get_db
from models.domain import Domain
from models.mail_domain import MailDomain
from templating import templates
from plugins.maddy.service import maddy_service
from services import nginx_service, ssl_service
import config

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/plugins/maddy", tags=["maddy_mail"])

SCRIPT_DIR = Path(__file__).parent / "scripts"
_mail_ssl_tasks: dict[str, asyncio.Task] = {}
_mail_ssl_status: dict[str, dict] = {}


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

@router.get("/", response_class=HTMLResponse)
async def maddy_index(request: Request, db: AsyncSession = Depends(get_db)):
    """Render Maddy Mail Server Management Page."""
    from plugins.manager import plugin_manager
    plugin_info = plugin_manager.get_plugin("maddy")
    plugin_version = plugin_info["version"] if plugin_info else "1.0.0"

    status = maddy_service.get_status()
    accounts = maddy_service.list_accounts()
    mail_domains = await maddy_service.list_mail_domains(db)

    # Panel domains available to configure for mail
    # (exclude domains already added to mail)
    configured_names = {d["domain"] for d in mail_domains}
    all_panel_domains = (await db.execute(
        select(Domain).order_by(Domain.name)
    )).scalars().all()
    panel_domains = [
        {"name": d.name, "server_ip": d.server_ip}
        for d in all_panel_domains
        if d.name not in configured_names
    ]

    server_ip = getattr(config, "SERVER_IP", "127.0.0.1")
    webmail_sites = {}
    webmail_plugin = plugin_manager.get_plugin("roundcube_webmail")
    if webmail_plugin and webmail_plugin["effective_status"] == "active":
        try:
            from plugins.roundcube_webmail.service import roundcube_webmail_service

            container_healthy = roundcube_webmail_service.get_status()["healthy"]
            for item in mail_domains:
                domain = item["domain"].lower()
                site = roundcube_webmail_service.get_site(domain)
                public_url = roundcube_webmail_service.get_public_url(domain)
                if not site:
                    reason = "Set up webmail for this domain."
                elif not container_healthy:
                    reason = "Roundcube container is not healthy."
                elif not public_url:
                    reason = "Finish DNS and HTTPS setup."
                else:
                    reason = None
                webmail_sites[domain] = {
                    "ready": bool(container_healthy and public_url),
                    "reason": reason,
                    "setup_url": f"/plugins/roundcube_webmail/?domain={domain}",
                }
        except Exception:
            logger.exception("Could not determine Roundcube webmail availability.")
            for item in mail_domains:
                domain = item["domain"].lower()
                webmail_sites[domain] = {
                    "ready": False,
                    "reason": "Could not read Roundcube status.",
                    "setup_url": f"/plugins/roundcube_webmail/?domain={domain}",
                }
    else:
        for item in mail_domains:
            domain = item["domain"].lower()
            webmail_sites[domain] = {
                "ready": False,
                "reason": "Roundcube webmail is disabled or not installed.",
                "setup_url": "/plugins/",
            }

    return templates.TemplateResponse("maddy.html", {
        "request": request,
        "active_page": "plugins",
        "plugin_version": plugin_version,
        "status": status,
        "accounts": accounts,
        "mail_domains": mail_domains,
        "panel_domains": panel_domains,
        "server_ip": server_ip,
        "webmail_sites": webmail_sites,
    })


# ---------------------------------------------------------------------------
# Install / Uninstall
# ---------------------------------------------------------------------------

@router.post("/api/install")
async def install_maddy(request: Request):
    """Trigger Maddy installation script."""
    script_path = SCRIPT_DIR / "install_maddy.sh"
    if os.name == "nt":
        return JSONResponse({"status": "ok", "message": "Mock install on Windows."})

    try:
        res = subprocess.run(["bash", str(script_path)], capture_output=True, text=True)
        if res.returncode != 0:
            logger.error("Maddy install failed:\nSTDOUT: %s\nSTDERR: %s", res.stdout, res.stderr)
            return JSONResponse({"detail": res.stderr or res.stdout}, status_code=500)
        return RedirectResponse("/plugins/maddy/", status_code=303)
    except Exception as exc:
        logger.error("Error executing Maddy installer: %s", exc)
        return JSONResponse({"detail": str(exc)}, status_code=500)


@router.post("/api/uninstall")
async def uninstall_maddy(request: Request):
    """Trigger Maddy uninstallation script."""
    script_path = SCRIPT_DIR / "uninstall_maddy.sh"
    if os.name == "nt":
        return JSONResponse({"status": "ok", "message": "Mock uninstall on Windows."})

    try:
        res = subprocess.run(["bash", str(script_path)], capture_output=True, text=True)
        if res.returncode != 0:
            logger.error("Maddy uninstall failed:\nSTDOUT: %s\nSTDERR: %s", res.stdout, res.stderr)
            return JSONResponse({"detail": res.stderr or res.stdout}, status_code=500)
        return RedirectResponse("/plugins/maddy/", status_code=303)
    except Exception as exc:
        logger.error("Error executing Maddy uninstaller: %s", exc)
        return JSONResponse({"detail": str(exc)}, status_code=500)


# ---------------------------------------------------------------------------
# Account CRUD
# ---------------------------------------------------------------------------

@router.post("/api/accounts/create")
async def create_account(
    request: Request,
    password: str = Form(...),
    username: str = Form(None),
    domain: str = Form(None),
    email: str = Form(None),
):
    """Create a new mailbox account."""
    try:
        if username and domain:
            full_email = f"{username.strip().rstrip('@')}@{domain.strip()}"
        elif email:
            full_email = email.strip()
        else:
            return JSONResponse({"detail": "Username and domain are required."}, status_code=400)

        maddy_service.create_account(full_email, password.strip())
        return JSONResponse({"status": "ok", "email": full_email})
    except PermissionError as exc:
        logger.error("Sudo permission error creating account: %s", exc)
        return JSONResponse({"detail": str(exc)}, status_code=403)
    except ValueError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=400)
    except Exception as exc:
        logger.error("Failed creating account: %s", exc)
        return JSONResponse({"detail": str(exc)}, status_code=500)


@router.post("/api/accounts/delete")
async def delete_account(
    request: Request,
    email: str = Form(...),
):
    """Delete an existing mailbox account."""
    try:
        maddy_service.delete_account(email.strip())
        return JSONResponse({"status": "ok", "email": email.strip()})
    except PermissionError as exc:
        logger.error("Sudo permission error deleting account: %s", exc)
        return JSONResponse({"detail": str(exc)}, status_code=403)
    except Exception as exc:
        logger.error("Failed deleting account: %s", exc)
        return JSONResponse({"detail": str(exc)}, status_code=500)


# ---------------------------------------------------------------------------
# DNS Record Provisioning
# ---------------------------------------------------------------------------

@router.post("/api/dns/auto-setup")
async def auto_setup_dns(
    request: Request,
    domain_name: str = Form(...),
    server_ip: str = Form(...),
):
    """Auto-configure PowerDNS mail records (MX, A, SPF, DKIM, DMARC) for a domain."""
    try:
        res = await maddy_service.auto_setup_dns_records(domain_name.strip(), server_ip.strip())
        return JSONResponse({
            "status": "ok",
            "message": f"Created {res['created_records']} mail DNS records for {domain_name}.",
        })
    except Exception as exc:
        logger.error("Error setting up mail DNS: %s", exc)
        return JSONResponse({"detail": str(exc)}, status_code=500)


@router.post("/api/dns/remove")
async def remove_dns(
    request: Request,
    domain_name: str = Form(...),
):
    """Remove mail DNS records from PowerDNS for a domain."""
    try:
        res = await maddy_service.remove_dns_records(domain_name.strip())
        return JSONResponse({
            "status": "ok",
            "message": f"Removed {res['deleted_records']} mail DNS records for {domain_name}.",
        })
    except Exception as exc:
        logger.error("Error removing mail DNS: %s", exc)
        return JSONResponse({"detail": str(exc)}, status_code=500)


# ---------------------------------------------------------------------------
# Mail Domain Management (add / delete full domain)
# ---------------------------------------------------------------------------

@router.post("/api/domains/add")
async def add_mail_domain(
    request: Request,
    domain: str = Form(...),
    server_ip: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    """Register a domain for mail delivery, update maddy.conf, and auto-setup DNS."""
    try:
        result = await maddy_service.add_mail_domain(db, domain.strip(), server_ip.strip())
        dns_msg = (
            "DNS records (MX, A, SPF, DMARC, DKIM) created automatically."
            if result.get("dns_configured")
            else "Domain added to maddy — DNS setup may need a manual retry."
        )
        return JSONResponse({
            "status": "ok",
            "message": f"✅ {domain} added for mail. {dns_msg}",
            "dns_configured": result.get("dns_configured", False),
        })
    except ValueError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=400)
    except Exception as exc:
        logger.error("Failed to add mail domain %s: %s", domain, exc)
        return JSONResponse({"detail": str(exc)}, status_code=500)


@router.post("/api/domains/delete")
async def delete_mail_domain(
    request: Request,
    domain: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    """
    Nuclear domain deletion:
    Removes all accounts, DNS records, SSL cert, nginx config,
    and the maddy.conf local_domains entry for this domain.
    """
    normalized = domain.strip().lower()
    task = _mail_ssl_tasks.get(normalized)
    if task and not task.done():
        return JSONResponse(
            {"detail": "Wait for the current mail SSL operation to finish."},
            status_code=409,
        )
    try:
        results = await maddy_service.delete_mail_domain(db, normalized)
        return JSONResponse({"status": "ok", "results": results})
    except Exception as exc:
        logger.error("Failed to delete mail domain %s: %s", domain, exc)
        return JSONResponse({"detail": str(exc)}, status_code=500)


# ---------------------------------------------------------------------------
# SSL Certificate Provisioning
# ---------------------------------------------------------------------------

async def _issue_mail_ssl_task(domain: str) -> None:
    mail_host = f"mail.{domain}"
    _mail_ssl_status[domain] = {
        "status": "pending",
        "message": f"Issuing TLS for {mail_host}.",
    }
    try:
        async with AsyncSessionLocal() as db:
            nginx_service.ensure_acme_root()
            nginx_service.create_webroot(
                mail_host,
                "<html><head><title>Mail Server</title></head>"
                "<body><h1>Mail Server is Active</h1></body></html>",
            )
            await nginx_service.create_static_site(mail_host)
            await nginx_service.reload()
            try:
                await ssl_service.issue_cert(
                    db, None, mail_host, include_www=False
                )
            except HTTPException as exc:
                if exc.status_code != 409:
                    raise

            cert_path = f"/etc/letsencrypt/live/{mail_host}/fullchain.pem"
            key_path = f"/etc/letsencrypt/live/{mail_host}/privkey.pem"
            await nginx_service.update_static_site_ssl(
                mail_host, cert_path, key_path
            )
            await nginx_service.reload()
            await asyncio.to_thread(maddy_service.sync_certificate, mail_host)

            record = await db.scalar(
                select(MailDomain).where(MailDomain.domain == domain)
            )
            if record:
                record.ssl_configured = True
            await db.commit()
        _mail_ssl_status[domain] = {
            "status": "ready",
            "message": f"TLS is active for {mail_host}.",
        }
    except Exception as exc:
        logger.exception("Maddy SSL setup failed for %s", mail_host)
        _mail_ssl_status[domain] = {
            "status": "error",
            "message": str(exc),
        }
    finally:
        _mail_ssl_tasks.pop(domain, None)


@router.post("/api/ssl/issue")
async def issue_mail_ssl(
    request: Request,
    domain: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    """Start Maddy TLS provisioning without holding the browser request open."""
    domain = domain.strip().lower()
    configured = await db.scalar(
        select(MailDomain).where(MailDomain.domain == domain)
    )
    if configured is None:
        return JSONResponse(
            {"detail": "Select a configured Maddy domain."},
            status_code=404,
        )
    if os.name == "nt":
        _mail_ssl_status[domain] = {
            "status": "ready",
            "message": "Mock SSL generation on Windows.",
        }
        return JSONResponse(_mail_ssl_status[domain])
    task = _mail_ssl_tasks.get(domain)
    if task and not task.done():
        return JSONResponse(_mail_ssl_status[domain], status_code=202)
    if any(not running.done() for running in _mail_ssl_tasks.values()):
        return JSONResponse(
            {"detail": "Wait for the current mail SSL operation to finish."},
            status_code=409,
        )

    _mail_ssl_status[domain] = {
        "status": "pending",
        "message": f"TLS setup started for mail.{domain}.",
    }
    _mail_ssl_tasks[domain] = asyncio.create_task(_issue_mail_ssl_task(domain))
    return JSONResponse(_mail_ssl_status[domain], status_code=202)


@router.get("/api/ssl/status")
async def mail_ssl_status(
    domain: str,
    db: AsyncSession = Depends(get_db),
):
    domain = domain.strip().lower()
    status = _mail_ssl_status.get(domain)
    if status:
        return JSONResponse(status)
    configured = await db.scalar(
        select(MailDomain).where(MailDomain.domain == domain)
    )
    if configured is None:
        return JSONResponse({"detail": "Mail domain not found."}, status_code=404)
    return JSONResponse(
        {
            "status": "ready" if configured.ssl_configured else "not_configured",
            "message": (
                f"TLS is active for mail.{domain}."
                if configured.ssl_configured
                else "TLS is not configured."
            ),
        }
    )
