"""
routers/system.py — Health check + server status routes
Returns real nginx and PowerDNS status, not mocked data.
"""
import asyncio
import socket
import httpx
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from fastapi import Depends

from database import get_db
from models.domain import Domain
from models.ssl_cert import SslCert
from models.proxy import ReverseProxy
from services import error_service
from templating import templates
from utils.shell import run
import config

router = APIRouter()


async def _check_nginx() -> dict:
    result = await run(["nginx", "-t"])
    return {
        "ok": result.success,
        "detail": result.stderr if not result.success else "OK",
    }


async def _check_powerdns() -> dict:
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            r = await client.get(
                f"{config.PDNS_URL}/api/v1/servers/localhost",
                headers={"X-API-Key": config.PDNS_API_KEY},
            )
        return {"ok": r.status_code == 200, "detail": f"HTTP {r.status_code}"}
    except Exception as exc:
        return {"ok": False, "detail": str(exc)}


@router.get("/api/health")
async def health_check():
    """Returns live status of nginx and PowerDNS."""
    nginx_status, pdns_status = await asyncio.gather(
        _check_nginx(), _check_powerdns()
    )
    return {
        "nginx": nginx_status,
        "powerdns": pdns_status,
        "server_ip": config.SERVER_IP,
        "hostname": socket.gethostname(),
    }


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, db: AsyncSession = Depends(get_db)):
    """Render the dashboard page with live stats."""
    nginx_status, pdns_status = await asyncio.gather(
        _check_nginx(), _check_powerdns()
    )

    domain_count = await db.scalar(select(func.count()).select_from(Domain))
    cert_count = await db.scalar(select(func.count()).select_from(SslCert))
    proxy_count = await db.scalar(select(func.count()).select_from(ReverseProxy))
    open_errors = await error_service.unresolved_count(db)

    return templates.TemplateResponse("pages/dashboard.html", {
        "request": request,
        "active_page": "dashboard",
        "server_ip": config.SERVER_IP,
        "hostname": socket.gethostname(),
        "nginx": nginx_status,
        "powerdns": pdns_status,
        "domain_count": domain_count or 0,
        "cert_count": cert_count or 0,
        "proxy_count": proxy_count or 0,
        "open_errors": open_errors,
    })
