"""Roundcube multi-domain management UI and secure mailbox launch endpoint."""
from __future__ import annotations

import asyncio
import logging
import re
import socket
import time
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import config
from database import AsyncSessionLocal, get_db
from models.mail_domain import MailDomain
from models.ssl_cert import SslCert
from plugins.maddy.service import maddy_service
from plugins.roundcube_webmail.service import roundcube_webmail_service
from services import dns_service, nginx_service, ssl_service
from templating import templates


router = APIRouter(prefix="/plugins/roundcube_webmail", tags=["roundcube_webmail"])
logger = logging.getLogger(__name__)
HOST_RE = re.compile(
    r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9-]{2,63}$"
)
DNS_TTL = 300
_ssl_tasks: dict[str, asyncio.Task] = {}


async def _mail_domains(db: AsyncSession) -> dict[str, dict[str, Any]]:
    return {
        item["domain"].lower(): item
        for item in await maddy_service.list_mail_domains(db)
    }


def _record_name(host: str, domain: str) -> str:
    return host[: -(len(domain) + 1)]


def _validate_site(domain: str, host: str, domains: dict[str, dict[str, Any]]) -> str | None:
    if domain not in domains:
        return "Select a configured Maddy domain."
    if not HOST_RE.fullmatch(host) or not host.endswith(f".{domain}"):
        return f"Webmail hostname must be a subdomain of {domain}."
    return None


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


async def _site_payload(
    domain: str,
    site: dict[str, Any],
    domain_data: dict[str, Any],
) -> dict[str, Any]:
    host = site.get("public_host")
    expected_ip = domain_data.get("server_ip") or getattr(config, "SERVER_IP", "")
    dns = await _dns_status(host if isinstance(host, str) else None, expected_ip)
    return {
        "domain": domain,
        "public_host": host,
        "configured_url": roundcube_webmail_service.get_configured_url(domain),
        "public_url": roundcube_webmail_service.get_public_url(domain),
        "dns_managed": bool(site.get("dns_managed")),
        "dns_error": site.get("dns_error"),
        "dns": dns,
        "ssl_status": site.get("ssl_status", "not_configured"),
        "ssl_error": site.get("ssl_error"),
        "ssl_error_detail": site.get("ssl_error_detail"),
    }


async def _status_payload(
    db: AsyncSession,
    domain: str | None = None,
) -> dict[str, Any]:
    domains = await _mail_domains(db)
    sites = roundcube_webmail_service.get_sites()
    selected = domain if domain in sites else next(iter(sites), None)
    site_payload = None
    if selected and selected in domains:
        site_payload = await _site_payload(selected, sites[selected], domains[selected])
    container = await asyncio.to_thread(roundcube_webmail_service.get_status)
    return {
        "selected_domain": selected,
        "site": site_payload,
        "container": container,
    }


def _friendly_ssl_error(host: str, exc: Exception) -> tuple[str, str]:
    detail = str(exc.detail) if isinstance(exc, HTTPException) else str(exc)
    summary = (
        f"Let's Encrypt could not verify {host}. Check its A record and inbound "
        "port 80, then retry."
    )
    return summary, detail[-1200:]


async def _issue_ssl_task(domain: str, host: str) -> None:
    try:
        async with AsyncSessionLocal() as db:
            try:
                try:
                    await ssl_service.issue_cert(db, None, host, include_www=False)
                except HTTPException as exc:
                    if exc.status_code != 409:
                        raise
                current = roundcube_webmail_service.get_site(domain)
                if not current or current.get("public_host") != host:
                    raise RuntimeError("Webmail hostname changed during SSL setup.")
                await nginx_service.update_proxy_ssl(
                    host,
                    "127.0.0.1",
                    roundcube_webmail_service.host_port,
                    "http",
                    f"/etc/letsencrypt/live/{host}/fullchain.pem",
                    f"/etc/letsencrypt/live/{host}/privkey.pem",
                )
                await nginx_service.reload()
                await db.commit()
            except Exception:
                await db.rollback()
                raise
        roundcube_webmail_service.update_site(
            domain,
            ssl_status="ready",
            ssl_started_at=None,
            ssl_error=None,
            ssl_error_detail=None,
        )
    except Exception as exc:
        logger.exception("Roundcube SSL setup failed for %s", host)
        summary, detail = _friendly_ssl_error(host, exc)
        current = roundcube_webmail_service.get_site(domain)
        if current and current.get("public_host") == host:
            roundcube_webmail_service.update_site(
                domain,
                ssl_status="error",
                ssl_started_at=None,
                ssl_error=summary,
                ssl_error_detail=detail,
            )
    finally:
        _ssl_tasks.pop(domain, None)


@router.get("/", response_class=HTMLResponse)
async def index(request: Request, db: AsyncSession = Depends(get_db)):
    from plugins.manager import plugin_manager

    domains = await _mail_domains(db)
    accounts = maddy_service.list_accounts()
    counts: dict[str, int] = {}
    for account in accounts:
        account_domain = account["email"].rsplit("@", 1)[-1].lower()
        counts[account_domain] = counts.get(account_domain, 0) + 1

    sites = roundcube_webmail_service.get_sites()
    selected = request.query_params.get("domain", "").strip().lower()
    if selected not in domains:
        selected = next(
            (item for item in sites if item in domains),
            next(iter(domains), None),
        )
    site_rows = []
    for domain, domain_data in domains.items():
        site = sites.get(domain)
        site_rows.append(
            {
                **domain_data,
                "mailbox_count": counts.get(domain, 0),
                "site": site,
                "public_url": roundcube_webmail_service.get_public_url(domain),
            }
        )
    selected_site = sites.get(selected) if selected else None
    selected_domain_data = dict(domains.get(selected) or {}) if selected else None
    if selected_domain_data is not None:
        selected_domain_data["mailbox_count"] = counts.get(selected, 0)
    expected_ip = (
        (selected_domain_data or {}).get("server_ip")
        or getattr(config, "SERVER_IP", "")
    )
    plugin = plugin_manager.get_plugin("roundcube_webmail")
    return templates.TemplateResponse(
        "roundcube_webmail.html",
        {
            "request": request,
            "active_page": "plugins",
            "plugin_version": (plugin or {}).get("version", "1.0.0"),
            "status": roundcube_webmail_service.get_status(),
            "domain_rows": site_rows,
            "selected_domain": selected,
            "selected_site": selected_site,
            "selected_domain_data": selected_domain_data,
            "expected_ip": expected_ip,
            "default_host": f"webmail.{selected}" if selected else "",
            "mailbox_count": len(accounts),
        },
    )


@router.get("/api/status")
async def webmail_status(
    domain: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    return JSONResponse(await _status_payload(db, domain))


@router.get("/api/mail-diagnostics")
async def mail_diagnostics():
    result = await asyncio.to_thread(
        roundcube_webmail_service.diagnose_mail_connection
    )
    return JSONResponse(result, status_code=200 if result.get("ok") else 503)


async def _save_site(
    db: AsyncSession,
    domain: str,
    public_host: str,
    manage_dns: bool,
    confirm_host_change: bool,
    *,
    creating: bool,
) -> JSONResponse:
    domain = domain.strip().lower()
    public_host = public_host.strip().lower()
    domains = await _mail_domains(db)
    validation_error = _validate_site(domain, public_host, domains)
    if validation_error:
        return JSONResponse({"detail": validation_error}, status_code=400)
    sites = roundcube_webmail_service.get_sites()
    current = sites.get(domain)
    if creating and current:
        return JSONResponse({"detail": "Webmail access already exists."}, status_code=409)
    if not creating and not current:
        return JSONResponse({"detail": "Webmail access is not configured."}, status_code=404)
    if any(
        item_domain != domain and item.get("public_host") == public_host
        for item_domain, item in sites.items()
    ):
        return JSONResponse({"detail": "That webmail hostname is already used."}, status_code=409)
    if any(not task.done() for task in _ssl_tasks.values()):
        return JSONResponse(
            {"detail": "Wait for the current SSL operation to finish."},
            status_code=409,
        )

    expected_ip = domains[domain].get("server_ip") or getattr(config, "SERVER_IP", "")
    previous_host = current.get("public_host") if current else None
    host_changed = bool(current and previous_host and previous_host != public_host)
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
    keep_ssl = bool(
        current
        and previous_host == public_host
        and current.get("ssl_status") == "ready"
    )
    dns_error = None
    try:
        if manage_dns:
            try:
                await dns_service.add_record(
                    domain,
                    _record_name(public_host, domain),
                    "A",
                    expected_ip,
                    DNS_TTL,
                )
            except Exception as exc:
                dns_error = str(exc)
        await nginx_service.create_proxy(
            public_host,
            "127.0.0.1",
            roundcube_webmail_service.host_port,
            "http",
        )
        if keep_ssl:
            await nginx_service.update_proxy_ssl(
                public_host,
                "127.0.0.1",
                roundcube_webmail_service.host_port,
                "http",
                f"/etc/letsencrypt/live/{public_host}/fullchain.pem",
                f"/etc/letsencrypt/live/{public_host}/privkey.pem",
            )
        await nginx_service.reload()

        if host_changed:
            if current.get("dns_managed"):
                await dns_service.delete_record(
                    domain, _record_name(previous_host, domain), "A"
                )
            old_cert = await db.scalar(
                select(SslCert).where(SslCert.full_domain == previous_host)
            )
            if old_cert:
                await ssl_service.revoke_cert(db, old_cert.id, delete_only=True)
            await nginx_service.remove_site(previous_host)
            await nginx_service.reload()
        elif (
            current
            and previous_host == public_host
            and current.get("dns_managed")
            and not manage_dns
        ):
            await dns_service.delete_record(
                domain, _record_name(public_host, domain), "A"
            )

        saved = {
            "public_host": public_host,
            "dns_managed": manage_dns,
            "dns_error": dns_error,
            "ssl_status": "ready" if keep_ssl else "not_configured",
            "ssl_started_at": None,
            "ssl_error": None,
            "ssl_error_detail": None,
        }
        roundcube_webmail_service.save_site(domain, saved)
        await db.commit()
    except Exception as exc:
        await db.rollback()
        return JSONResponse({"detail": str(exc)}, status_code=500)
    if dns_error:
        message = (
            "Hostname changed and old managed resources were removed. "
            "Automatic DNS for the new hostname failed; add the displayed A record."
            if host_changed
            else "Webmail saved; automatic DNS failed. Add the displayed A record."
        )
    elif host_changed:
        message = (
            "Hostname changed. The old SSL certificate, proxy, and any "
            "panel-managed DNS record were removed."
        )
    else:
        message = "Webmail access saved."
    payload = await _site_payload(domain, saved, domains[domain])
    return JSONResponse(
        {
            "status": "ok",
            "message": message,
            "site": payload,
        }
    )


@router.post("/api/sites/add")
async def add_site(
    request: Request,
    mail_domain: str = Form(...),
    public_host: str = Form(...),
    manage_dns: bool = Form(False),
    confirm_host_change: bool = Form(False),
    db: AsyncSession = Depends(get_db),
):
    return await _save_site(
        db, mail_domain, public_host, manage_dns, confirm_host_change, creating=True
    )


@router.post("/api/sites/update")
async def update_site(
    request: Request,
    mail_domain: str = Form(...),
    public_host: str = Form(...),
    manage_dns: bool = Form(False),
    confirm_host_change: bool = Form(False),
    db: AsyncSession = Depends(get_db),
):
    return await _save_site(
        db, mail_domain, public_host, manage_dns, confirm_host_change, creating=False
    )


@router.post("/api/sites/delete")
async def delete_site(
    request: Request,
    mail_domain: str = Form(...),
    confirmation: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    domain = mail_domain.strip().lower()
    if confirmation.strip().lower() != domain:
        return JSONResponse(
            {"detail": f"Type {domain} to confirm."},
            status_code=400,
        )
    site = roundcube_webmail_service.get_site(domain)
    if not site:
        return JSONResponse({"detail": "Webmail access was not found."}, status_code=404)
    task = _ssl_tasks.get(domain)
    if task and not task.done():
        return JSONResponse(
            {"detail": "Wait for the current SSL operation to finish."},
            status_code=409,
        )
    host = site.get("public_host")
    try:
        if isinstance(host, str) and host:
            if site.get("dns_managed"):
                await dns_service.delete_record(
                    domain, _record_name(host, domain), "A"
                )
            cert = await db.scalar(
                select(SslCert).where(SslCert.full_domain == host)
            )
            if cert:
                await ssl_service.revoke_cert(db, cert.id, delete_only=True)
            await nginx_service.remove_site(host)
            await nginx_service.reload()
        roundcube_webmail_service.delete_site(domain)
        await db.commit()
    except Exception as exc:
        await db.rollback()
        return JSONResponse({"detail": str(exc)}, status_code=500)
    return JSONResponse(
        {
            "status": "ok",
            "message": f"Webmail access removed for {domain}. Mailboxes were preserved.",
        }
    )


@router.post("/api/sites/ssl")
async def issue_webmail_ssl(
    request: Request,
    mail_domain: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    domain = mail_domain.strip().lower()
    domains = await _mail_domains(db)
    site = roundcube_webmail_service.get_site(domain)
    if domain not in domains or not site:
        return JSONResponse(
            {"detail": "Configure webmail access first."},
            status_code=409,
        )
    host = site.get("public_host")
    expected_ip = domains[domain].get("server_ip") or getattr(config, "SERVER_IP", "")
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
    task = _ssl_tasks.get(domain)
    if task and not task.done():
        return JSONResponse(
            {"status": "pending", "message": "SSL setup is already running."},
            status_code=202,
        )
    if any(not running.done() for running in _ssl_tasks.values()):
        return JSONResponse(
            {"detail": "Wait for the current webmail SSL operation to finish."},
            status_code=409,
        )
    roundcube_webmail_service.update_site(
        domain,
        ssl_status="pending",
        ssl_started_at=int(time.time()),
        ssl_error=None,
        ssl_error_detail=None,
    )
    _ssl_tasks[domain] = asyncio.create_task(_issue_ssl_task(domain, host))
    return JSONResponse(
        {"status": "pending", "message": "SSL setup started in the background."},
        status_code=202,
    )


@router.post("/api/launch")
async def launch(request: Request, email: str = Form(...)):
    status = roundcube_webmail_service.get_status()
    normalized = email.strip().lower()
    accounts = {item["email"].lower() for item in maddy_service.list_accounts()}
    if normalized not in accounts:
        return JSONResponse({"detail": "Mailbox not found."}, status_code=404)
    domain = normalized.rsplit("@", 1)[-1]
    public_url = roundcube_webmail_service.get_public_url(domain)
    if not status["healthy"]:
        return JSONResponse(
            {"detail": "Roundcube container is not healthy."},
            status_code=503,
        )
    if not public_url:
        return JSONResponse(
            {"detail": f"HTTPS webmail is not ready for {domain}."},
            status_code=503,
        )
    try:
        token = roundcube_webmail_service.create_launch_token(normalized)
    except (ValueError, RuntimeError) as exc:
        return JSONResponse({"detail": str(exc)}, status_code=400)
    launch_url = f"{public_url}?{urlencode({'_launch': token})}"
    if "application/json" in request.headers.get("accept", ""):
        return JSONResponse({"status": "ok", "launch_url": launch_url})
    return RedirectResponse(launch_url, status_code=303)
