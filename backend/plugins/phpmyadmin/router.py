"""phpMyAdmin management UI and native PHP-FPM site endpoints."""
from __future__ import annotations

import asyncio
import logging
import re
import socket
import time
from typing import Any

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import select

import config
from database import AsyncSessionLocal
from models.ssl_cert import SslCert
from plugins.phpmyadmin.service import phpmyadmin_service
from services import dns_service, nginx_service, ssl_service
from templating import templates

router = APIRouter(prefix="/phpmyadmin", tags=["phpmyadmin"])
logger = logging.getLogger(__name__)
HOST_RE = re.compile(
    r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9-]{2,63}$"
)
DNS_TTL = 300
_ssl_tasks: dict[str, asyncio.Task] = {}


async def _zones() -> dict[str, str]:
    """Map panel-managed PowerDNS zones (normalized) to their raw names."""
    from utils.powerdns import list_zones

    zones: dict[str, str] = {}
    for zone in await list_zones():
        name = str(zone.get("name") or "").strip().lower()
        if name:
            zones[name.rstrip(".")] = name.rstrip(".")
    return zones


def _zone_for(host: str, zones: dict[str, str]) -> str | None:
    """Return the longest managed zone that hosts this hostname, if any."""
    candidates = [zone for zone in zones if host == zone or host.endswith(f".{zone}")]
    return max(candidates, key=len) if candidates else None


def _record_name(host: str, zone: str) -> str:
    return host[: -(len(zone) + 1)] or "@"


async def _dns_status(
    host: str | None,
    expected_ip: str | None,
) -> dict[str, Any]:
    if not host:
        return {"status": "not_configured", "ips": [], "expected_ip": expected_ip}
    try:
        records = await asyncio.wait_for(
            asyncio.to_thread(
                socket.getaddrinfo,
                host,
                443,
                socket.AF_INET,
                socket.SOCK_STREAM,
            ),
            timeout=5,
        )
        ips = sorted({item[4][0] for item in records})
    except (OSError, asyncio.TimeoutError):
        ips = []
    ready = bool(expected_ip and expected_ip in ips)
    return {
        "status": "ready" if ready else ("mismatch" if ips else "pending"),
        "ips": ips,
        "expected_ip": expected_ip,
    }


async def _site_payload() -> dict[str, Any] | None:
    site = phpmyadmin_service.get_site()
    if not site:
        return None
    host = site.get("public_host")
    expected_ip = getattr(config, "SERVER_IP", "")
    dns = await _dns_status(host if isinstance(host, str) else None, expected_ip)
    return {
        "public_host": host,
        "configured_url": phpmyadmin_service.get_configured_url(),
        "public_url": phpmyadmin_service.get_public_url(),
        "dns_managed": bool(site.get("dns_managed")),
        "dns_error": site.get("dns_error"),
        "dns": dns,
        "ssl_status": site.get("ssl_status", "not_configured"),
        "ssl_error": site.get("ssl_error"),
        "ssl_error_detail": site.get("ssl_error_detail"),
        "paused": phpmyadmin_service.is_paused(),
    }


async def _status_payload() -> dict[str, Any]:
    return {
        "status": phpmyadmin_service.get_status(),
        "site": await _site_payload(),
    }


def _friendly_ssl_error(host: str, exc: Exception) -> tuple[str, str]:
    detail = str(getattr(exc, "detail", exc))
    summary = (
        f"Let's Encrypt could not verify {host}. Check its A record and inbound "
        "port 80, then retry."
    )
    return summary, detail[-1200:]


async def _issue_ssl_task(host: str) -> None:
    try:
        async with AsyncSessionLocal() as db:
            try:
                try:
                    await ssl_service.issue_cert(db, None, host, include_www=False)
                except Exception as exc:
                    if getattr(exc, "status_code", None) != 409:
                        raise
                current = phpmyadmin_service.get_site()
                if not current or current.get("public_host") != host:
                    raise RuntimeError("phpMyAdmin hostname changed during SSL setup.")
                socket_path = phpmyadmin_service.socket_path()
                if not socket_path:
                    raise RuntimeError("PHP-FPM socket is unavailable.")
                await nginx_service.update_php_site_ssl(
                    host,
                    str(phpmyadmin_service.htdocs),
                    str(socket_path),
                    "/var/log/nginx/phpmyadmin.access.log",
                    "/var/log/nginx/phpmyadmin.error.log",
                    f"/etc/letsencrypt/live/{host}/fullchain.pem",
                    f"/etc/letsencrypt/live/{host}/privkey.pem",
                )
                await nginx_service.reload()
                await db.commit()
            except Exception:
                await db.rollback()
                raise
        phpmyadmin_service.update_state(
            ssl_status="ready",
            ssl_started_at=None,
            ssl_error=None,
            ssl_error_detail=None,
        )
    except Exception as exc:
        logger.exception("phpMyAdmin SSL setup failed for %s", host)
        summary, detail = _friendly_ssl_error(host, exc)
        current = phpmyadmin_service.get_site()
        if current and current.get("public_host") == host:
            phpmyadmin_service.update_state(
                ssl_status="error",
                ssl_started_at=None,
                ssl_error=summary,
                ssl_error_detail=detail,
            )
    finally:
        _ssl_tasks.pop(host, None)


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    from plugins.manager import plugin_manager

    plugin = plugin_manager.plugins.get("phpmyadmin")
    site = await _site_payload()
    status = phpmyadmin_service.get_status()
    return templates.TemplateResponse(
        "phpmyadmin.html",
        {
            "request": request,
            "active_page": "plugins",
            "plugin_version": (plugin or {}).get("version", "1.0.0"),
            "status": status,
            "site": site,
            "expected_ip": getattr(config, "SERVER_IP", ""),
            "default_host": "pma.example.com",
        },
    )


@router.get("/api/status")
async def phpmyadmin_status():
    return JSONResponse(await _status_payload())


@router.post("/api/install")
async def install_phpmyadmin(request: Request):
    from plugins.manager import plugin_manager

    success, message = await plugin_manager.run_plugin_script(
        "phpmyadmin", "install"
    )
    return JSONResponse(
        {"status": "ok" if success else "error", "message": message},
        status_code=200 if success else 500,
    )


@router.post("/api/uninstall")
async def uninstall_phpmyadmin(request: Request):
    from plugins.manager import plugin_manager

    success, message = await plugin_manager.run_plugin_script(
        "phpmyadmin", "uninstall"
    )
    return JSONResponse(
        {"status": "ok" if success else "error", "message": message},
        status_code=200 if success else 500,
    )


async def _save_site(
    request: Request,
    public_host: str,
    manage_dns: bool,
    confirm_host_change: bool,
) -> JSONResponse:
    host = public_host.strip().lower()
    if not HOST_RE.fullmatch(host):
        return JSONResponse(
            {"detail": "Enter a valid hostname such as pma.example.com."},
            status_code=400,
        )
    if host == "localhost" or host.endswith(".local"):
        return JSONResponse(
            {"detail": "Use a real public hostname for phpMyAdmin."},
            status_code=400,
        )
    zones = await _zones()
    current = phpmyadmin_service.get_site()
    previous_host = current.get("public_host") if current else None
    host_changed = bool(current and previous_host and previous_host != host)
    if host_changed and not confirm_host_change:
        return JSONResponse(
            {
                "detail": (
                    "Confirm the hostname change. Its existing SSL certificate, "
                    "proxy, and panel-managed DNS record will be removed."
                )
            },
            status_code=409,
        )
    if (not current or host_changed) and nginx_service.server_name_in_use(host):
        return JSONResponse(
            {"detail": "That hostname is already used by another site."},
            status_code=409,
        )
    if any(not task.done() for task in _ssl_tasks.values()):
        return JSONResponse(
            {"detail": "Wait for the current SSL operation to finish."},
            status_code=409,
        )

    zone = _zone_for(host, zones) if manage_dns else None
    if manage_dns and not zone:
        return JSONResponse(
            {
                "detail": (
                    "Automatic DNS needs a hostname under a panel-managed DNS "
                    "zone. Add the A record manually instead."
                )
            },
            status_code=400,
        )

    expected_ip = getattr(config, "SERVER_IP", "")
    keep_ssl = bool(
        current
        and previous_host == host
        and current.get("ssl_status") == "ready"
    )
    dns_error = None
    try:
        if manage_dns:
            try:
                await dns_service.add_record(
                    zone, _record_name(host, zone), "A", expected_ip, DNS_TTL
                )
            except Exception as exc:
                dns_error = str(exc)
        socket_path = phpmyadmin_service.socket_path()
        if not socket_path:
            return JSONResponse(
                {"detail": "PHP-FPM socket is unavailable. Reinstall phpMyAdmin."},
                status_code=409,
            )
        if keep_ssl:
            await nginx_service.update_php_site_ssl(
                host,
                str(phpmyadmin_service.htdocs),
                str(socket_path),
                "/var/log/nginx/phpmyadmin.access.log",
                "/var/log/nginx/phpmyadmin.error.log",
                f"/etc/letsencrypt/live/{host}/fullchain.pem",
                f"/etc/letsencrypt/live/{host}/privkey.pem",
            )
        else:
            await nginx_service.create_php_site(
                host,
                str(phpmyadmin_service.htdocs),
                str(socket_path),
                "/var/log/nginx/phpmyadmin.access.log",
                "/var/log/nginx/phpmyadmin.error.log",
            )
        await nginx_service.reload()

        if host_changed:
            if current.get("dns_managed"):
                old_zone = _zone_for(previous_host, await _zones())
                if old_zone:
                    await dns_service.delete_record(
                        old_zone, _record_name(previous_host, old_zone), "A"
                    )
            old_cert = await _cert_for_host(previous_host)
            if old_cert:
                async with AsyncSessionLocal() as db:
                    await ssl_service.revoke_cert(db, old_cert.id, delete_only=True)
            await nginx_service.remove_site(previous_host)
            await nginx_service.reload()
        elif (
            current
            and previous_host == host
            and current.get("dns_managed")
            and not manage_dns
        ):
            old_zone = _zone_for(host, zones)
            if old_zone:
                await dns_service.delete_record(
                    old_zone, _record_name(host, old_zone), "A"
                )

        saved = {
            "public_host": host,
            "dns_managed": manage_dns,
            "dns_error": dns_error,
            "ssl_status": "ready" if keep_ssl else "not_configured",
            "ssl_started_at": None,
            "ssl_error": None,
            "ssl_error_detail": None,
        }
        phpmyadmin_service.save_site(saved)
    except Exception as exc:
        logger.exception("phpMyAdmin site save failed")
        return JSONResponse({"detail": str(exc)}, status_code=500)
    if dns_error:
        message = (
            "Hostname changed and old managed resources were removed. "
            "Automatic DNS for the new hostname failed; add the displayed A record."
            if host_changed
            else "phpMyAdmin saved; automatic DNS failed. Add the displayed A record."
        )
    elif host_changed:
        message = (
            "Hostname changed. The old SSL certificate, proxy, and any "
            "panel-managed DNS record were removed."
        )
    else:
        message = "phpMyAdmin access saved."
    return JSONResponse(
        {
            "status": "ok",
            "message": message,
            "site": await _site_payload(),
        }
    )


async def _cert_for_host(host: str):
    async with AsyncSessionLocal() as db:
        return await db.scalar(select(SslCert).where(SslCert.full_domain == host))


@router.post("/api/site")
async def save_site(
    request: Request,
    public_host: str = Form(...),
    manage_dns: bool = Form(False),
    confirm_host_change: bool = Form(False),
):
    return await _save_site(request, public_host, manage_dns, confirm_host_change)


@router.post("/api/site/delete")
async def delete_site(
    request: Request,
    public_host: str = Form(...),
    confirmation: str = Form(...),
):
    host = public_host.strip().lower()
    if confirmation.strip().lower() != host:
        return JSONResponse(
            {"detail": f"Type {host} to confirm."},
            status_code=400,
        )
    site = phpmyadmin_service.get_site()
    if not site or site.get("public_host") != host:
        return JSONResponse(
            {"detail": "phpMyAdmin access was not found."},
            status_code=404,
        )
    task = _ssl_tasks.get(host)
    if task and not task.done():
        return JSONResponse(
            {"detail": "Wait for the current SSL operation to finish."},
            status_code=409,
        )
    try:
        if site.get("dns_managed"):
            zones = await _zones()
            zone = _zone_for(host, zones)
            if zone:
                await dns_service.delete_record(
                    zone, _record_name(host, zone), "A"
                )
        cert = await _cert_for_host(host)
        if cert:
            async with AsyncSessionLocal() as db:
                await ssl_service.revoke_cert(db, cert.id, delete_only=True)
        await nginx_service.remove_site(host)
        await nginx_service.reload()
        phpmyadmin_service.delete_site()
    except Exception as exc:
        logger.exception("phpMyAdmin site delete failed")
        return JSONResponse({"detail": str(exc)}, status_code=500)
    return JSONResponse(
        {
            "status": "ok",
            "message": "phpMyAdmin public access removed. The app is still installed.",
        }
    )


@router.post("/api/site/ssl")
async def issue_ssl(request: Request, public_host: str = Form(...)):
    host = public_host.strip().lower()
    site = phpmyadmin_service.get_site()
    if not site or site.get("public_host") != host:
        return JSONResponse(
            {"detail": "Configure public phpMyAdmin access first."},
            status_code=409,
        )
    expected_ip = getattr(config, "SERVER_IP", "")
    dns = await _dns_status(host, expected_ip)
    if dns["status"] != "ready":
        found = ", ".join(dns["ips"]) if dns["ips"] else "no A record"
        return JSONResponse(
            {
                "detail": (
                    f"DNS is not ready. {host} resolves to {found}; "
                    f"it must resolve to {expected_ip}."
                )
            },
            status_code=409,
        )
    task = _ssl_tasks.get(host)
    if task and not task.done():
        return JSONResponse(
            {"status": "pending", "message": "SSL setup is already running."},
            status_code=202,
        )
    if any(not running.done() for running in _ssl_tasks.values()):
        return JSONResponse(
            {"detail": "Wait for the current phpMyAdmin SSL operation to finish."},
            status_code=409,
        )
    phpmyadmin_service.update_state(
        ssl_status="pending",
        ssl_started_at=int(time.time()),
        ssl_error=None,
        ssl_error_detail=None,
    )
    _ssl_tasks[host] = asyncio.create_task(_issue_ssl_task(host))
    return JSONResponse(
        {"status": "pending", "message": "SSL setup started in the background."},
        status_code=202,
    )


@router.get("/api/launch")
async def launch():
    status = phpmyadmin_service.get_status()
    url = phpmyadmin_service.get_public_url() or phpmyadmin_service.get_configured_url()
    if not status["installed"]:
        return JSONResponse(
            {"detail": "Install phpMyAdmin before opening it."}, status_code=409
        )
    if not status["healthy"]:
        return JSONResponse(
            {"detail": "phpMyAdmin is not healthy. Check PHP-FPM and MariaDB."},
            status_code=503,
        )
    if not url:
        return JSONResponse(
            {"detail": "Configure public phpMyAdmin access first."},
            status_code=409,
        )
    return RedirectResponse(url, status_code=303)
