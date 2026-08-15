"""Roundcube PHP plugin router: multi-domain management, settings, and launch endpoint."""
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
from plugins.roundcube_php.service import roundcube_php_service
from services import dns_service, nginx_service, ssl_service
from templating import templates

router = APIRouter(prefix="/plugins/roundcube_php", tags=["roundcube_php"])
logger = logging.getLogger(__name__)

HOST_RE = re.compile(r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9-]{2,63}$")
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


async def _dns_status(host: str | None, expected_ip: str | None) -> dict[str, Any]:
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


async def _site_payload(domain: str, site: dict[str, Any], domain_data: dict[str, Any]) -> dict[str, Any]:
    host = site.get("public_host")
    expected_ip = domain_data.get("server_ip") or getattr(config, "SERVER_IP", "")
    dns = await _dns_status(host if isinstance(host, str) else None, expected_ip)
    return {
        "domain": domain,
        "public_host": host,
        "configured_url": roundcube_php_service.get_configured_url(domain),
        "public_url": roundcube_php_service.get_public_url(domain),
        "dns_managed": bool(site.get("dns_managed")),
        "dns_error": site.get("dns_error"),
        "dns": dns,
        "ssl_status": site.get("ssl_status", "not_configured"),
        "ssl_error": site.get("ssl_error"),
        "ssl_error_detail": site.get("ssl_error_detail"),
    }


async def _issue_ssl_task(domain: str, host: str) -> None:
    try:
        async with AsyncSessionLocal() as db:
            try:
                try:
                    await ssl_service.issue_cert(db, None, host, include_www=False)
                except HTTPException as exc:
                    if exc.status_code != 409:
                        raise
                current = roundcube_php_service.get_site(domain)
                if not current or current.get("public_host") != host:
                    raise RuntimeError("Webmail hostname changed during SSL setup.")
                await nginx_service.update_proxy_ssl(
                    host,
                    "127.0.0.1",
                    roundcube_php_service.port,
                    "http",
                    f"/etc/letsencrypt/live/{host}/fullchain.pem",
                    f"/etc/letsencrypt/live/{host}/privkey.pem",
                )
                await nginx_service.reload()
                await db.commit()
            except Exception:
                await db.rollback()
                raise
        roundcube_php_service.update_site(
            domain,
            ssl_status="ready",
            ssl_started_at=None,
            ssl_error=None,
            ssl_error_detail=None,
        )
    except Exception as exc:
        logger.exception("Roundcube PHP SSL setup failed for %s", host)
        detail = str(exc)
        current = roundcube_php_service.get_site(domain)
        if current and current.get("public_host") == host:
            roundcube_php_service.update_site(
                domain,
                ssl_status="error",
                ssl_started_at=None,
                ssl_error=f"Let's Encrypt could not verify {host}. Check DNS A record.",
                ssl_error_detail=detail[-1200:],
            )
    finally:
        _ssl_tasks.pop(domain, None)


@router.get("/", response_class=HTMLResponse)
@router.get("", response_class=HTMLResponse)
async def index(request: Request, db: AsyncSession = Depends(get_db)):
    from plugins.manager import plugin_manager

    domains = await _mail_domains(db)
    accounts = maddy_service.list_accounts()
    counts: dict[str, int] = {}
    for account in accounts:
        account_domain = account["email"].rsplit("@", 1)[-1].lower()
        counts[account_domain] = counts.get(account_domain, 0) + 1

    sites = roundcube_php_service.get_sites()
    site_rows = []
    for domain, domain_data in domains.items():
        site = sites.get(domain)
        site_rows.append(
            {
                **domain_data,
                "mailbox_count": counts.get(domain, 0),
                "site": site,
                "public_url": roundcube_php_service.get_public_url(domain),
            }
        )

    plugin = plugin_manager.plugins.get("roundcube_php")
    return templates.TemplateResponse(
        "roundcube.html",
        {
            "request": request,
            "active_page": "plugins",
            "plugin_version": (plugin or {}).get("version", "1.0.0"),
            "domain_rows": site_rows,
            "mail_domains": domains,
            "status": roundcube_php_service.get_status(),
            "settings": roundcube_php_service.get_settings(),
            "db_stats": roundcube_php_service.get_db_stats(),
            "server_ip": getattr(config, "SERVER_IP", "127.0.0.1"),
        },
    )


@router.get("/api/status")
async def get_status(request: Request):
    return JSONResponse(
        {
            "status": roundcube_php_service.get_status(),
            "settings": roundcube_php_service.get_settings(),
            "db_stats": roundcube_php_service.get_db_stats(),
        }
    )


@router.get("/api/mail-diagnostics")
async def mail_diagnostics():
    result = await asyncio.to_thread(roundcube_php_service.diagnose_mail_connection)
    return JSONResponse(result, status_code=200 if result.get("ok") else 503)


@router.post("/api/install")
async def install_plugin(request: Request):
    from plugins.manager import plugin_manager

    success, message = await plugin_manager.run_plugin_script("roundcube_php", "install")
    return JSONResponse(
        {"status": "ok" if success else "error", "message": message},
        status_code=200 if success else 500,
    )


@router.post("/api/restart")
async def restart_service(request: Request):
    try:
        roundcube_php_service.resume()
    except RuntimeError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=500)
    return JSONResponse(
        {
            "status": "ok",
            "message": "Roundcube PHP service restarted.",
            "server": roundcube_php_service.get_status(),
        }
    )


@router.post("/api/uninstall")
async def uninstall_plugin(request: Request):
    from plugins.manager import plugin_manager

    success, message = await plugin_manager.run_plugin_script("roundcube_php", "uninstall")
    return JSONResponse(
        {"status": "ok" if success else "error", "message": message},
        status_code=200 if success else 500,
    )


@router.post("/api/sites/add")
async def add_site(
    request: Request,
    mail_domain: str = Form(...),
    public_host: str = Form(...),
    manage_dns: bool = Form(False),
    db: AsyncSession = Depends(get_db),
):
    domain = mail_domain.strip().lower()
    public_host = public_host.strip().lower()
    domains = await _mail_domains(db)
    validation_error = _validate_site(domain, public_host, domains)
    if validation_error:
        return JSONResponse({"detail": validation_error}, status_code=400)
    sites = roundcube_php_service.get_sites()
    if domain in sites:
        return JSONResponse({"detail": "Webmail access already configured."}, status_code=409)

    expected_ip = domains[domain].get("server_ip") or getattr(config, "SERVER_IP", "")
    dns_error = None
    try:
        if manage_dns:
            try:
                await dns_service.add_record(
                    domain, _record_name(public_host, domain), "A", expected_ip, DNS_TTL
                )
            except Exception as exc:
                dns_error = str(exc)
        await nginx_service.create_proxy(
            public_host, "127.0.0.1", roundcube_php_service.port, "http"
        )
        await nginx_service.reload()
        saved = {
            "public_host": public_host,
            "dns_managed": manage_dns,
            "dns_error": dns_error,
            "ssl_status": "not_configured",
            "ssl_started_at": None,
            "ssl_error": None,
            "ssl_error_detail": None,
        }
        roundcube_php_service.save_site(domain, saved)
        await db.commit()
    except Exception as exc:
        await db.rollback()
        return JSONResponse({"detail": str(exc)}, status_code=500)

    payload = await _site_payload(domain, saved, domains[domain])
    return JSONResponse({"status": "ok", "message": "Webmail domain created.", "site": payload})


@router.post("/api/sites/delete")
async def delete_site(
    request: Request,
    mail_domain: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    domain = mail_domain.strip().lower()
    site = roundcube_php_service.get_site(domain)
    if not site:
        return JSONResponse({"detail": "Webmail access not found."}, status_code=404)
    host = site.get("public_host")
    try:
        if isinstance(host, str) and host:
            if site.get("dns_managed"):
                await dns_service.delete_record(domain, _record_name(host, domain), "A")
            cert = await db.scalar(select(SslCert).where(SslCert.full_domain == host))
            if cert:
                await ssl_service.revoke_cert(db, cert.id, delete_only=True)
            await nginx_service.remove_site(host)
            await nginx_service.reload()
        roundcube_php_service.delete_site(domain)
        await db.commit()
    except Exception as exc:
        await db.rollback()
        return JSONResponse({"detail": str(exc)}, status_code=500)
    return JSONResponse({"status": "ok", "message": f"Webmail removed for {domain}."})


@router.post("/api/sites/ssl")
async def issue_ssl(
    request: Request,
    mail_domain: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    domain = mail_domain.strip().lower()
    domains = await _mail_domains(db)
    site = roundcube_php_service.get_site(domain)
    if domain not in domains or not site:
        return JSONResponse({"detail": "Configure webmail first."}, status_code=409)
    host = site.get("public_host")
    expected_ip = domains[domain].get("server_ip") or getattr(config, "SERVER_IP", "")
    dns = await _dns_status(host, expected_ip)
    if dns["status"] != "ready":
        return JSONResponse({"detail": f"DNS not ready for {host}."}, status_code=409)

    roundcube_php_service.update_site(domain, ssl_status="pending", ssl_started_at=int(time.time()))
    _ssl_tasks[domain] = asyncio.create_task(_issue_ssl_task(domain, host))
    return JSONResponse({"status": "pending", "message": "SSL issuance started in background."}, status_code=202)


@router.post("/api/settings/update")
async def update_settings(request: Request):
    content_type = request.headers.get("content-type", "")
    updates: dict[str, Any] = {}

    if "application/json" in content_type:
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}

        if "skin" in payload:
            skin = str(payload["skin"]).strip().lower()
            if skin in {"elastic", "larry", "classic"}:
                updates["skin"] = skin

        if "product_name" in payload:
            pname = str(payload["product_name"]).strip()
            if pname:
                updates["product_name"] = pname

        if "max_message_size" in payload:
            msize = str(payload["max_message_size"]).strip()
            if msize:
                updates["max_message_size"] = msize

        if "session_lifetime" in payload:
            try:
                updates["session_lifetime"] = max(5, min(1440, int(payload["session_lifetime"])))
            except (ValueError, TypeError):
                pass

        if "plugins" in payload:
            raw_plugins = payload["plugins"]
            if isinstance(raw_plugins, list):
                cleaned_plugins = [str(p).strip() for p in raw_plugins if str(p).strip()]
                if "srvpanel_launch" not in cleaned_plugins:
                    cleaned_plugins.append("srvpanel_launch")
                updates["plugins"] = cleaned_plugins
    else:
        form_data = await request.form()

        if "skin" in form_data:
            skin = str(form_data.get("skin", "")).strip().lower()
            if skin in {"elastic", "larry", "classic"}:
                updates["skin"] = skin

        if "product_name" in form_data:
            pname = str(form_data.get("product_name", "")).strip()
            if pname:
                updates["product_name"] = pname

        if "max_message_size" in form_data:
            msize = str(form_data.get("max_message_size", "")).strip()
            if msize:
                updates["max_message_size"] = msize

        if "session_lifetime" in form_data:
            try:
                updates["session_lifetime"] = max(5, min(1440, int(form_data.get("session_lifetime", 30))))
            except (ValueError, TypeError):
                pass

        if "plugins" in form_data:
            plugin_list = form_data.getlist("plugins")
            cleaned_plugins = [str(p).strip() for p in plugin_list if str(p).strip()]
            if "srvpanel_launch" not in cleaned_plugins:
                cleaned_plugins.append("srvpanel_launch")
            updates["plugins"] = cleaned_plugins

    saved = roundcube_php_service.update_settings(**updates)
    return JSONResponse({"status": "ok", "message": "Settings updated.", "settings": saved})


@router.post("/api/maintenance/optimize")
async def optimize_database(request: Request):
    try:
        await asyncio.to_thread(roundcube_php_service.optimize_db)
    except Exception as exc:
        return JSONResponse({"detail": str(exc)}, status_code=500)
    return JSONResponse({"status": "ok", "message": "SQLite database optimized.", "stats": roundcube_php_service.get_db_stats()})


@router.post("/api/maintenance/purge-cache")
async def purge_cache(request: Request):
    purged = await asyncio.to_thread(roundcube_php_service.purge_cache)
    return JSONResponse({"status": "ok", "message": f"Purged {purged} temporary / expired session files."})


@router.post("/api/launch")
async def launch_webmail(request: Request, email: str = Form(...)):
    status = roundcube_php_service.get_status()
    normalized = email.strip().lower()
    accounts = {item["email"].lower() for item in maddy_service.list_accounts()}
    if normalized not in accounts:
        return JSONResponse({"detail": "Mailbox not found."}, status_code=404)
    domain = normalized.rsplit("@", 1)[-1]
    public_url = roundcube_php_service.get_public_url(domain)
    if not status["healthy"]:
        return JSONResponse({"detail": "Roundcube PHP service is not healthy."}, status_code=503)
    if not public_url:
        return JSONResponse({"detail": f"HTTPS webmail is not ready for {domain}."}, status_code=503)
    try:
        token = roundcube_php_service.create_launch_token(normalized)
    except Exception as exc:
        return JSONResponse({"detail": str(exc)}, status_code=400)
    launch_url = f"{public_url}?{urlencode({'_launch': token})}"
    if "application/json" in request.headers.get("accept", ""):
        return JSONResponse({"status": "ok", "launch_url": launch_url})
    return RedirectResponse(launch_url, status_code=303)
