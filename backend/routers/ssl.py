"""
routers/ssl.py — SSL Manager routes.
Routes call ssl_service only. No direct certbot or nginx calls here.
"""
import logging
from fastapi import APIRouter, Depends, Request, Form, Query, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from urllib.parse import quote

from database import get_db
from models.domain import Domain
from models.ssl_cert import SslCert
from models.proxy import ReverseProxy
from services import ssl_service, nginx_service
from templating import templates

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/ssl", tags=["ssl"])


async def _build_eligible(db: AsyncSession) -> list[dict]:
    """Domains and proxies with nginx active and no existing cert."""
    issued_domains = {
        r.full_domain
        for r in (await db.scalars(select(SslCert))).all()
    }

    eligible: list[dict] = []

    domains = (await db.scalars(
        select(Domain).where(Domain.nginx_active == True)
    )).all()
    for d in domains:
        if d.name not in issued_domains:
            eligible.append({
                "id": d.id,
                "label": d.name,
                "full_domain": d.name,
                "type": "domain",
            })

    proxies = (await db.scalars(
        select(ReverseProxy).where(ReverseProxy.nginx_config_path.isnot(None))
    )).all()
    for p in proxies:
        if p.full_domain not in issued_domains:
            eligible.append({
                "id": p.domain_id,  # may be None for external
                "label": f"{p.full_domain} (proxy → {p.target_ip}:{p.target_port})",
                "full_domain": p.full_domain,
                "type": "proxy",
            })

    return eligible


# ---------------------------------------------------------------
# CERTS LIST (DB LIMIT + OFFSET PAGINATED)
# ---------------------------------------------------------------
@router.get("/", response_class=HTMLResponse)
async def ssl_index(
    request: Request,
    offset: int = 0,
    limit: int = 8,
    domain_id: int | None = Query(default=None),
    full_domain: str | None = Query(default=None),
    open_issue: int | None = Query(default=None),
    db: AsyncSession = Depends(get_db)
):
    """Show issued SSL certs with live expiry status and slide drawer for issuing."""
    cert_list, total = await ssl_service.list_certs_paginated(db, limit=limit, offset=offset)
    eligible = await _build_eligible(db)

    preselect_full = (full_domain or "").strip().lower() or None
    if not preselect_full and domain_id is not None:
        for item in eligible:
            if item.get("id") == domain_id and item.get("type") == "domain":
                preselect_full = item["full_domain"]
                break
        if not preselect_full:
            for item in eligible:
                if item.get("id") == domain_id:
                    preselect_full = item["full_domain"]
                    break

    auto_open = bool(open_issue or preselect_full)

    return templates.TemplateResponse("pages/ssl/index.html", {
        "request": request,
        "active_page": "ssl",
        "cert_list": cert_list,
        "total_count": total,
        "current_offset": offset,
        "current_limit": limit,
        "eligible": eligible,
        "preselect_full_domain": preselect_full,
        "auto_open_issue": auto_open,
    })


@router.get("/api/rows", response_class=HTMLResponse)
async def ssl_api_rows(
    request: Request,
    offset: int = 0,
    limit: int = 8,
    db: AsyncSession = Depends(get_db)
):
    """API endpoint returning pre-rendered HTML table rows for Load More."""
    cert_list, total = await ssl_service.list_certs_paginated(db, limit=limit, offset=offset)
    rendered = ""
    template = templates.get_template("pages/ssl/_row.html")
    for item in cert_list:
        rendered += template.render({
            "request": request,
            "item": item,
            "_": getattr(request.state, "_", lambda k: k),
        })
    has_more = (offset + len(cert_list)) < total
    response = HTMLResponse(content=rendered)
    response.headers["X-Has-More"] = "1" if has_more else "0"
    response.headers["X-Loaded-Count"] = str(len(cert_list))
    response.headers["X-Total-Count"] = str(total)
    return response


@router.get("/api/list")
async def ssl_api_list(
    offset: int = 0,
    limit: int = 8,
    db: AsyncSession = Depends(get_db)
):
    """API endpoint returning paginated certificates for Load More."""
    cert_list, total = await ssl_service.list_certs_paginated(db, limit=limit, offset=offset)
    items = []
    for item in cert_list:
        c = item["cert"]
        items.append({
            "id": c.id,
            "full_domain": c.full_domain,
            "domain_id": c.domain_id,
            "expiry_date": c.expiry_date.strftime('%Y-%m-%d') if c.expiry_date else None,
            "days_left": item["days_left"],
            "status": item["status"],
            "auto_renew": c.auto_renew,
        })
    return {
        "items": items,
        "total": total,
        "offset": offset,
        "limit": limit,
        "has_more": (offset + limit) < total
    }


# ---------------------------------------------------------------
# ISSUE — form (redirects to index with side drawer open)
# ---------------------------------------------------------------
@router.get("/issue", response_class=HTMLResponse)
async def ssl_issue_page(
    request: Request,
    domain_id: int | None = Query(default=None),
    full_domain: str | None = Query(default=None),
):
    """Redirect to SSL index and open slide-out issue drawer."""
    query_parts = ["open_issue=1"]
    if full_domain:
        query_parts.append(f"full_domain={quote(full_domain.strip().lower())}")
    if domain_id:
        query_parts.append(f"domain_id={domain_id}")
    return RedirectResponse(f"/ssl/?{'&'.join(query_parts)}", status_code=303)


# ---------------------------------------------------------------
# ISSUE — submit
# ---------------------------------------------------------------
@router.post("/issue")
async def ssl_issue_submit(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Run certbot for the selected domain/subdomain. Supports both JSON and Form requests."""
    content_type = request.headers.get("content-type", "").lower()
    is_json_request = "application/json" in content_type or request.headers.get("accept", "").startswith("application/json")

    full_domain = ""
    domain_id_raw = ""
    include_www = False
    auto_renew = True

    if "application/json" in content_type:
        try:
            body = await request.json()
            full_domain = str(body.get("full_domain") or "").strip().lower()
            domain_id_raw = body.get("domain_id")
            include_www = bool(body.get("include_www", False))
            if "auto_renew" in body:
                auto_renew = bool(body.get("auto_renew"))
        except Exception:
            raise HTTPException(400, "Invalid JSON payload")
    else:
        form_data = await request.form()
        full_domain = str(form_data.get("full_domain") or "").strip().lower()
        domain_id_raw = form_data.get("domain_id")
        include_www = form_data.get("include_www") in ("true", "yes", "1", "on", True)
        if "auto_renew" in form_data:
            auto_renew = form_data.get("auto_renew") in ("true", "yes", "1", "on", True)

    if not full_domain:
        if is_json_request:
            raise HTTPException(400, "Domain name is required.")
        eligible = await _build_eligible(db)
        return templates.TemplateResponse("pages/ssl/issue.html", {
            "request": request,
            "active_page": "ssl",
            "eligible": eligible,
            "preselect_full_domain": None,
            "error": "Please select a valid domain.",
        }, status_code=400)

    # Automatically force include_www = False if full_domain is a subdomain
    if full_domain.count(".") > 1 or full_domain.startswith("www."):
        include_www = False

    resolved_domain_id: int | None = None
    if domain_id_raw is not None and str(domain_id_raw).strip().isdigit():
        resolved_domain_id = int(str(domain_id_raw).strip())

    # Resolve domain_id from host if missing (external proxy or form omit)
    if resolved_domain_id is None:
        domain = await db.scalar(select(Domain).where(Domain.name == full_domain))
        if domain:
            resolved_domain_id = domain.id
        else:
            proxy = await db.scalar(
                select(ReverseProxy).where(ReverseProxy.full_domain == full_domain)
            )
            if proxy:
                resolved_domain_id = proxy.domain_id

    try:
        cert = await ssl_service.issue_cert(
            db, resolved_domain_id, full_domain, include_www, auto_renew
        )
        if is_json_request:
            return JSONResponse({"status": "ok", "full_domain": cert.full_domain})
        return RedirectResponse(f"/ssl/?issued={cert.full_domain}", status_code=303)
    except Exception as exc:
        error_msg = str(exc.detail) if hasattr(exc, "detail") else str(exc)
        if is_json_request:
            raise HTTPException(status_code=getattr(exc, "status_code", 400), detail=error_msg)
        eligible = await _build_eligible(db)
        return templates.TemplateResponse("pages/ssl/issue.html", {
            "request": request,
            "active_page": "ssl",
            "eligible": eligible,
            "preselect_full_domain": full_domain,
            "error": error_msg,
        }, status_code=400)


# ---------------------------------------------------------------
# RENEW
# ---------------------------------------------------------------
@router.post("/{cert_id}/renew")
async def ssl_renew(cert_id: int, db: AsyncSession = Depends(get_db)):
    """Renew a specific cert by ID."""
    try:
        await ssl_service.renew_cert(db, cert_id)
        return RedirectResponse(f"/ssl/?renewed=1", status_code=303)
    except Exception as exc:
        error = str(exc.detail) if hasattr(exc, "detail") else str(exc)
        return RedirectResponse(f"/ssl/?error={error}", status_code=303)


# ---------------------------------------------------------------
# REVOKE
# ---------------------------------------------------------------
@router.post("/{cert_id}/revoke")
async def ssl_revoke(cert_id: int, db: AsyncSession = Depends(get_db)):
    """Revoke cert, revert nginx to HTTP-only."""
    try:
        await ssl_service.revoke_cert(db, cert_id)
        return RedirectResponse("/ssl/?revoked=1", status_code=303)
    except Exception as exc:
        error = str(exc.detail) if hasattr(exc, "detail") else str(exc)
        return RedirectResponse(f"/ssl/?error={error}", status_code=303)

from pydantic import BaseModel
class AutoRenewPayload(BaseModel):
    auto_renew: bool

@router.post("/api/{cert_id}/auto-renew")
async def ssl_auto_renew_toggle(cert_id: int, payload: AutoRenewPayload, db: AsyncSession = Depends(get_db)):
    """Toggle auto_renew for a certificate."""
    cert = await db.scalar(select(SslCert).where(SslCert.id == cert_id))
    if cert:
        cert.auto_renew = payload.auto_renew
        await db.commit()
    return {"status": "ok", "auto_renew": payload.auto_renew}
