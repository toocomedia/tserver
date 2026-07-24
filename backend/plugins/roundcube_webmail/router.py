"""Management UI and secure mailbox launch endpoint for Roundcube."""
from __future__ import annotations

import re

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

import config
from database import get_db
from plugins.maddy.service import maddy_service
from plugins.roundcube_webmail.service import roundcube_webmail_service
from services import nginx_service, ssl_service
from templating import templates


router = APIRouter(prefix="/plugins/roundcube_webmail", tags=["roundcube_webmail"])


@router.get("/", response_class=HTMLResponse)
async def index(request: Request, db: AsyncSession = Depends(get_db)):
    mail_domains = await maddy_service.list_mail_domains(db)
    state = roundcube_webmail_service.read_state()
    selected = state.get("mail_domain")
    if not selected and mail_domains:
        selected = mail_domains[0]["domain"]
    selected_public_host = state.get("public_host")
    if not selected_public_host and selected:
        selected_public_host = f"webmail.{selected}"
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
        },
    )


@router.post("/api/configure")
async def configure(
    request: Request,
    mail_domain: str = Form(...),
    public_host: str = Form(...),
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
    previous_host = roundcube_webmail_service.read_state().get("public_host")
    try:
        from services import dns_service

        try:
            await dns_service.add_record(
                domain=mail_domain,
                name=record_name,
                rtype="A",
                content=chosen["server_ip"] or config.SERVER_IP,
                ttl=3600,
            )
        except Exception:
            # Existing/external DNS is allowed; certificate issuance will prove reachability.
            pass

        await nginx_service.create_proxy(
            public_host,
            "127.0.0.1",
            roundcube_webmail_service.host_port,
            "http",
        )
        try:
            await ssl_service.issue_cert(db, None, public_host, include_www=False)
        except HTTPException as exc:
            if exc.status_code != 409:
                raise

        cert_path = f"/etc/letsencrypt/live/{public_host}/fullchain.pem"
        key_path = f"/etc/letsencrypt/live/{public_host}/privkey.pem"
        await nginx_service.update_proxy_ssl(
            public_host,
            "127.0.0.1",
            roundcube_webmail_service.host_port,
            "http",
            cert_path,
            key_path,
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
            }
        )
    except HTTPException as exc:
        return JSONResponse({"detail": str(exc.detail)}, status_code=exc.status_code)
    except Exception as exc:
        return JSONResponse({"detail": str(exc)}, status_code=500)
    return RedirectResponse("/plugins/roundcube_webmail/", status_code=303)


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
