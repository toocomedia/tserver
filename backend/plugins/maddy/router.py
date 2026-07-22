"""
backend/plugins/maddy/router.py — APIRouter for Maddy Mail Server plugin.
Exposes Mail Management UI and endpoints for accounts CRUD and PowerDNS records.
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
from backend.plugins.maddy.service import maddy_service
import config

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/plugins/maddy", tags=["maddy_mail"])

SCRIPT_DIR = Path(__file__).parent / "scripts"


@router.get("/", response_class=HTMLResponse)
async def maddy_index(request: Request, db: AsyncSession = Depends(get_db)):
    """Render Maddy Mail Server Management Page."""
    status = maddy_service.get_status()
    accounts = maddy_service.list_accounts()

    domains = (await db.execute(
        select(Domain).order_by(Domain.name)
    )).scalars().all()

    server_ip = getattr(config, "SERVER_IP", "127.0.0.1")

    return templates.TemplateResponse("maddy.html", {
        "request": request,
        "active_page": "plugins",
        "status": status,
        "accounts": accounts,
        "domains": domains,
        "server_ip": server_ip,
    })


@router.post("/api/install")
async def install_maddy(request: Request):
    """Trigger Maddy installation script."""
    script_path = SCRIPT_DIR / "install_maddy.sh"
    if os.name == "nt":
        return JSONResponse({"status": "ok", "message": "Mock install on Windows."})

    try:
        subprocess.run(["bash", str(script_path)], check=True)
        return RedirectResponse("/plugins/maddy/", status_code=303)
    except Exception as exc:
        logger.error("Error executing Maddy installer: %s", exc)
        return JSONResponse({"detail": f"Installer failed: {exc}"}, status_code=500)


@router.post("/api/uninstall")
async def uninstall_maddy(request: Request):
    """Trigger Maddy uninstallation script."""
    script_path = SCRIPT_DIR / "uninstall_maddy.sh"
    if os.name == "nt":
        return JSONResponse({"status": "ok", "message": "Mock uninstall on Windows."})

    try:
        subprocess.run(["bash", str(script_path)], check=True)
        return RedirectResponse("/plugins/maddy/", status_code=303)
    except Exception as exc:
        logger.error("Error executing Maddy uninstaller: %s", exc)
        return JSONResponse({"detail": f"Uninstaller failed: {exc}"}, status_code=500)


@router.post("/api/accounts/create")
async def create_account(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
):
    """Create a new mailbox account."""
    try:
        maddy_service.create_account(email.strip(), password.strip())
        return RedirectResponse("/plugins/maddy/", status_code=303)
    except Exception as exc:
        logger.error("Failed creating account: %s", exc)
        return JSONResponse({"detail": str(exc)}, status_code=400)


@router.post("/api/accounts/delete")
async def delete_account(
    request: Request,
    email: str = Form(...),
):
    """Delete an existing mailbox account."""
    try:
        maddy_service.delete_account(email.strip())
        return RedirectResponse("/plugins/maddy/", status_code=303)
    except Exception as exc:
        logger.error("Failed deleting account: %s", exc)
        return JSONResponse({"detail": str(exc)}, status_code=400)


@router.post("/api/dns/auto-setup")
async def auto_setup_dns(
    request: Request,
    domain_name: str = Form(...),
    server_ip: str = Form(...),
):
    """Auto-configure PowerDNS mail records (MX, A, SPF, DKIM, DMARC) for a domain."""
    try:
        res = await maddy_service.auto_setup_dns_records(domain_name.strip(), server_ip.strip())
        return JSONResponse({"status": "ok", "message": f"Created {res['created_records']} mail DNS records for {domain_name}."})
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
        return JSONResponse({"status": "ok", "message": f"Removed {res['deleted_records']} mail DNS records for {domain_name}."})
    except Exception as exc:
        logger.error("Error removing mail DNS: %s", exc)
        return JSONResponse({"detail": str(exc)}, status_code=500)
