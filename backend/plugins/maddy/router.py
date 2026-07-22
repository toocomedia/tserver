"""
backend/plugins/maddy/router.py — APIRouter for Maddy Mail Server plugin.
Exposes Mail Management UI and endpoints for accounts CRUD, DNS records,
and SSL certificate provisioning.
"""
import os
import logging
import subprocess
from pathlib import Path

from fastapi import APIRouter, Request, Form, Depends
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from database import get_db
from models.domain import Domain
from templating import templates
from plugins.maddy.service import maddy_service
from services import nginx_service, ssl_service
from utils import shell
import config

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/plugins/maddy", tags=["maddy_mail"])

SCRIPT_DIR = Path(__file__).parent / "scripts"


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

@router.get("/", response_class=HTMLResponse)
async def maddy_index(request: Request, db: AsyncSession = Depends(get_db)):
    """Render Maddy Mail Server Management Page."""
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

    return templates.TemplateResponse("maddy.html", {
        "request": request,
        "active_page": "plugins",
        "status": status,
        "accounts": accounts,
        "mail_domains": mail_domains,
        "panel_domains": panel_domains,
        "server_ip": server_ip,
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
    try:
        results = await maddy_service.delete_mail_domain(db, domain.strip())
        return JSONResponse({"status": "ok", "results": results})
    except Exception as exc:
        logger.error("Failed to delete mail domain %s: %s", domain, exc)
        return JSONResponse({"detail": str(exc)}, status_code=500)


# ---------------------------------------------------------------------------
# SSL Certificate Provisioning
# ---------------------------------------------------------------------------

@router.post("/api/ssl/issue")
async def issue_mail_ssl(
    request: Request,
    domain: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    """Request Let's Encrypt SSL for the mail subdomain and link to Maddy."""
    if os.name == "nt":
        return JSONResponse({"status": "ok", "message": "Mock SSL generation on Windows."})

    mail_domain = f"mail.{domain.strip()}"
    maddy_certs_dir = Path("/etc/maddy/certs")

    try:
        # 1. Set up Nginx webroot so ACME http-01 challenge works
        logger.info("Setting up Nginx webroot for %s", mail_domain)
        nginx_service.ensure_acme_root()
        webroot_path = nginx_service.create_webroot(
            mail_domain,
            "<html><head><title>Mail Server</title></head>"
            "<body style='font-family:sans-serif;text-align:center;padding:50px;'>"
            "<h1>Mail Server is Active</h1><p>IMAP/SMTP services are running.</p>"
            "</body></html>",
        )
        # chmod the parent of /public/ so nginx can serve the challenge files
        await shell.run(["sudo", "-n", "chmod", "-R", "755", str(Path(webroot_path).parent)])
        await nginx_service.create_static_site(mail_domain)
        await nginx_service.reload()

        # 2. Issue or re-use existing Let's Encrypt certificate
        logger.info("Requesting Let's Encrypt SSL for %s", mail_domain)
        from fastapi import HTTPException
        try:
            await ssl_service.issue_cert(db, None, mail_domain, include_www=False)
        except HTTPException as exc:
            if exc.status_code == 409:
                logger.info("Certificate already exists for %s — reusing it.", mail_domain)
            else:
                raise

        # 3. Update Nginx vhost to serve HTTPS
        cert_path = f"/etc/letsencrypt/live/{mail_domain}/fullchain.pem"
        key_path  = f"/etc/letsencrypt/live/{mail_domain}/privkey.pem"
        await nginx_service.update_static_site_ssl(mail_domain, cert_path, key_path)
        await nginx_service.reload()

        # 4. Copy certs to Maddy's cert directory
        #    Use sudo cp — clean, reliable, no encoding tricks.
        logger.info("Copying SSL certs to Maddy cert directory")
        le_live = Path(f"/etc/letsencrypt/live/{mail_domain}")

        copy_res = await shell.run([
            "sudo", "-n", "bash", "-c",
            f"cp '{le_live}/fullchain.pem' '{maddy_certs_dir}/fullchain.pem' && "
            f"cp '{le_live}/privkey.pem'   '{maddy_certs_dir}/privkey.pem'   && "
            f"chown maddy:maddy '{maddy_certs_dir}/fullchain.pem' '{maddy_certs_dir}/privkey.pem' && "
            f"chmod 640 '{maddy_certs_dir}/privkey.pem'"
        ])
        if not copy_res.success:
            raise RuntimeError(f"Failed to copy SSL certs to Maddy: {copy_res.stderr}")

        # 5. Restart Maddy to apply new TLS certificate
        logger.info("Restarting Maddy to apply new SSL")
        await shell.run(["sudo", "-n", "systemctl", "restart", "maddy"])

        return JSONResponse({
            "status": "ok",
            "message": f"SSL issued and linked to Maddy for {mail_domain}!",
        })

    except Exception as exc:
        logger.error("Error issuing mail SSL: %s", exc)
        return JSONResponse({"detail": str(exc)}, status_code=500)
