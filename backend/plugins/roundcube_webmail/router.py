"""Management UI and secure mailbox launch endpoint for Roundcube."""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import re
import socket
import time
from typing import Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

import config
from database import AsyncSessionLocal, get_db
from plugins.maddy.service import maddy_service
from plugins.roundcube_webmail.service import roundcube_webmail_service
from services import nginx_service, ssl_service
from templating import templates


router = APIRouter(prefix="/plugins/roundcube_webmail", tags=["roundcube_webmail"])
logger = logging.getLogger(__name__)
_ssl_tasks: dict[str, asyncio.Task] = {}


async def _dns_status(state: dict[str, Any]) -> dict[str, Any]:
    host = state.get("public_host")
    expected = state.get("expected_ip")
    if not isinstance(host, str) or not host:
        return {"status": "not_configured", "ips": [], "expected_ip": expected}
    try:
        records = await asyncio.wait_for(
            asyncio.to_thread(
                socket.getaddrinfo,
                host,
                80,
                socket.AF_INET,
                socket.SOCK_STREAM,
            ),
            timeout=5,
        )
        ips = sorted({item[4][0] for item in records})
    except (OSError, asyncio.TimeoutError):
        ips = []
    ready = bool(expected and expected in ips)
    return {
        "status": "ready" if ready else ("mismatch" if ips else "pending"),
        "ips": ips,
        "expected_ip": expected,
    }


async def _status_payload() -> dict[str, Any]:
    state = roundcube_webmail_service.read_state()
    dns, container = await asyncio.gather(
        _dns_status(state),
        asyncio.to_thread(roundcube_webmail_service.get_status),
    )
    return {
        "configured": bool(state.get("public_host")),
        "public_host": state.get("public_host"),
        "configured_url": roundcube_webmail_service.get_configured_url(),
        "public_url": roundcube_webmail_service.get_public_url(),
        "ssl_status": state.get("ssl_status", "not_configured"),
        "ssl_error": state.get("ssl_error"),
        "ssl_error_detail": state.get("ssl_error_detail"),
        "dns_managed": bool(state.get("dns_managed")),
        "dns_error": state.get("dns_error"),
        "dns": dns,
        "container": container,
    }


def _friendly_ssl_error(host: str, exc: Exception) -> tuple[str, str]:
    detail = str(exc.detail) if isinstance(exc, HTTPException) else str(exc)
    summary = (
        f"Let's Encrypt could not verify {host}. Confirm its A record points to "
        f"this server and inbound port 80 is open, then retry."
    )
    return summary, detail[-1200:]


async def _issue_ssl_task(host: str) -> None:
    try:
        async with AsyncSessionLocal() as db:
            try:
                try:
                    await ssl_service.issue_cert(
                        db, None, host, include_www=False
                    )
                except HTTPException as exc:
                    if exc.status_code != 409:
                        raise

                current = roundcube_webmail_service.read_state()
                if current.get("public_host") != host:
                    raise RuntimeError("Webmail hostname changed during SSL setup.")
                cert_path = f"/etc/letsencrypt/live/{host}/fullchain.pem"
                key_path = f"/etc/letsencrypt/live/{host}/privkey.pem"
                await nginx_service.update_proxy_ssl(
                    host,
                    "127.0.0.1",
                    roundcube_webmail_service.host_port,
                    "http",
                    cert_path,
                    key_path,
                )
                await nginx_service.reload()
                await db.commit()
            except Exception:
                await db.rollback()
                raise
        roundcube_webmail_service.update_state(
            ssl_status="ready",
            ssl_started_at=None,
            ssl_error=None,
            ssl_error_detail=None,
        )
    except Exception as exc:
        logger.exception("Roundcube SSL setup failed for %s", host)
        summary, detail = _friendly_ssl_error(host, exc)
        current = roundcube_webmail_service.read_state()
        if current.get("public_host") == host:
            roundcube_webmail_service.update_state(
                ssl_status="error",
                ssl_started_at=None,
                ssl_error=summary,
                ssl_error_detail=detail,
            )
    finally:
        _ssl_tasks.pop(host, None)


@router.get("/", response_class=HTMLResponse)
async def index(request: Request, db: AsyncSession = Depends(get_db)):
    mail_domains = await maddy_service.list_mail_domains(db)
    accounts = maddy_service.list_accounts()
    account_counts: dict[str, int] = {}
    for account in accounts:
        domain = account["email"].rsplit("@", 1)[-1].lower()
        account_counts[domain] = account_counts.get(domain, 0) + 1
    for item in mail_domains:
        item["mailbox_count"] = account_counts.get(item["domain"].lower(), 0)
    state = roundcube_webmail_service.read_state()
    selected = state.get("mail_domain")
    if not selected and mail_domains:
        selected = mail_domains[0]["domain"]
    selected_public_host = state.get("public_host")
    if not selected_public_host and selected:
        selected_public_host = f"webmail.{selected}"
    selected_server_ip = next(
        (
            item.get("server_ip")
            for item in mail_domains
            if item["domain"] == selected
        ),
        None,
    )
    return templates.TemplateResponse(
        "roundcube_webmail.html",
        {
            "request": request,
            "active_page": "plugins",
            "status": roundcube_webmail_service.get_status(),
            "mail_domains": mail_domains,
            "selected_domain": selected,
            "selected_public_host": selected_public_host,
            "public_url": roundcube_webmail_service.get_public_url(),
            "configured_url": roundcube_webmail_service.get_configured_url(),
            "webmail_state": state,
            "server_ip": getattr(config, "SERVER_IP", ""),
            "selected_server_ip": selected_server_ip,
            "mailbox_count": len(accounts),
        },
    )


@router.get("/api/status")
async def webmail_status():
    return JSONResponse(await _status_payload())


@router.post("/api/configure")
async def configure(
    request: Request,
    mail_domain: str = Form(...),
    public_host: str = Form(...),
    expected_ip: str = Form(...),
    dns_ttl: int = Form(300),
    manage_dns: bool = Form(False),
    db: AsyncSession = Depends(get_db),
):
    mail_domain = mail_domain.strip().lower()
    public_host = public_host.strip().lower()
    domains = await maddy_service.list_mail_domains(db)
    chosen = next((item for item in domains if item["domain"] == mail_domain), None)
    if chosen is None:
        return JSONResponse({"detail": "Select a configured Maddy domain."}, status_code=400)
    if not re.fullmatch(
        r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9-]{2,63}",
        mail_domain,
    ):
        return JSONResponse({"detail": "Invalid mail domain."}, status_code=400)

    if (
        not re.fullmatch(
            r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9-]{2,63}",
            public_host,
        )
        or not public_host.endswith(f".{mail_domain}")
    ):
        return JSONResponse(
            {"detail": f"Webmail host must be a subdomain of {mail_domain}."},
            status_code=400,
        )
    record_name = public_host[: -(len(mail_domain) + 1)]
    try:
        server_ip = str(ipaddress.IPv4Address(expected_ip.strip()))
    except ipaddress.AddressValueError:
        return JSONResponse(
            {"detail": "Enter a valid public IPv4 address."},
            status_code=400,
        )
    if dns_ttl not in {300, 900, 3600}:
        return JSONResponse({"detail": "Invalid DNS TTL."}, status_code=400)
    previous_state = roundcube_webmail_service.read_state()
    previous_host = previous_state.get("public_host")
    ssl_recently_started = (
        previous_state.get("ssl_status") == "pending"
        and isinstance(previous_state.get("ssl_started_at"), int)
        and previous_state["ssl_started_at"] > int(time.time()) - 180
    )
    if ssl_recently_started or any(not task.done() for task in _ssl_tasks.values()):
        return JSONResponse(
            {"detail": "Wait for the current SSL operation to finish."},
            status_code=409,
        )
    dns_error = None
    keep_ssl = (
        previous_host == public_host
        and previous_state.get("ssl_status") == "ready"
    )
    try:
        from services import dns_service

        if manage_dns:
            try:
                await dns_service.add_record(
                    domain=mail_domain,
                    name=record_name,
                    rtype="A",
                    content=server_ip,
                    ttl=dns_ttl,
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
        if (
            isinstance(previous_host, str)
            and previous_host
            and previous_host != public_host
        ):
            await nginx_service.remove_site(previous_host)
        await nginx_service.reload()
        roundcube_webmail_service.write_state(
            {
                "mail_domain": mail_domain,
                "mail_host": f"mail.{mail_domain}",
                "public_host": public_host,
                "expected_ip": server_ip,
                "dns_managed": manage_dns,
                "dns_ttl": dns_ttl,
                "dns_error": dns_error,
                "ssl_status": "ready" if keep_ssl else "not_configured",
                "ssl_started_at": None,
                "ssl_error": None,
                "ssl_error_detail": None,
            }
        )
    except HTTPException as exc:
        return JSONResponse({"detail": str(exc.detail)}, status_code=exc.status_code)
    except Exception as exc:
        return JSONResponse({"detail": str(exc)}, status_code=500)
    message = "Webmail hostname and HTTP proxy configured."
    if dns_error:
        message += " Automatic DNS failed; add the shown A record manually."
    return JSONResponse(
        {
            "status": "ok",
            "message": message,
        }
    )


@router.post("/api/ssl")
async def issue_webmail_ssl(request: Request):
    state = roundcube_webmail_service.read_state()
    host = state.get("public_host")
    if not isinstance(host, str) or not host:
        return JSONResponse(
            {"detail": "Configure the webmail hostname first."},
            status_code=409,
        )
    existing_task = _ssl_tasks.get(host)
    recently_started = (
        state.get("ssl_status") == "pending"
        and isinstance(state.get("ssl_started_at"), int)
        and state["ssl_started_at"] > int(time.time()) - 180
    )
    if recently_started or (existing_task and not existing_task.done()):
        return JSONResponse(
            {"status": "pending", "message": "SSL setup is already running."},
            status_code=202,
        )
    dns = await _dns_status(state)
    if dns["status"] != "ready":
        found = ", ".join(dns["ips"]) if dns["ips"] else "no A record"
        return JSONResponse(
            {
                "detail": (
                    f"DNS is not ready. {host} resolves to {found}; "
                    f"it must resolve to {dns['expected_ip']}."
                )
            },
            status_code=409,
        )

    roundcube_webmail_service.update_state(
        ssl_status="pending",
        ssl_started_at=int(time.time()),
        ssl_error=None,
        ssl_error_detail=None,
    )
    _ssl_tasks[host] = asyncio.create_task(_issue_ssl_task(host))
    return JSONResponse(
        {"status": "pending", "message": "SSL setup started."},
        status_code=202,
    )


@router.post("/api/launch")
async def launch(request: Request, email: str = Form(...)):
    status = roundcube_webmail_service.get_status()
    public_url = roundcube_webmail_service.get_public_url()
    if not status["healthy"] or not public_url:
        return JSONResponse({"detail": "Roundcube webmail is unavailable."}, status_code=503)
    normalized = email.strip().lower()
    accounts = {item["email"].lower() for item in maddy_service.list_accounts()}
    if normalized not in accounts:
        return JSONResponse({"detail": "Mailbox not found."}, status_code=404)
    try:
        token = roundcube_webmail_service.create_launch_token(normalized)
    except (ValueError, RuntimeError) as exc:
        return JSONResponse({"detail": str(exc)}, status_code=400)
    return RedirectResponse(f"{public_url}?_launch={token}", status_code=303)
