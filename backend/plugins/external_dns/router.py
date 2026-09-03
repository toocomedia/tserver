"""
plugins/external_dns/router.py — Routes for the External DNS Manager plugin.

UI:  GET  /plugins/external_dns/            (landing: bound domains + health)
API: /plugins/external_dns/api/*            (JSON; CSRF enforced by core middleware)

This router owns binding lifecycle + provider metadata only. Record CRUD is
served through the core DNS Manager (routers/dns.py) via the external_dns bridge,
so editing feels native. Every mutation records a Task Manager entry.
"""
import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from templating import templates
from services.task_manager_service import task_manager_service
from plugins.external_dns.providers.base import ExternalDnsError
from plugins.external_dns.schemas import BindRequest, TestRequest, UnbindRequest
from plugins.external_dns.service import external_dns_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/plugins/external_dns", tags=["external_dns"])


async def _task(action: str, domain: str, label: str, success: bool, message: str) -> None:
    await task_manager_service.record_completed_task(
        category="dns", action=action, target_id=domain,
        label=label, success=success, message=message,
    )


@router.get("/", response_class=HTMLResponse)
async def index(request: Request, db: AsyncSession = Depends(get_db)):
    from plugins.manager import plugin_manager
    info = plugin_manager.get_plugin("external_dns")
    return templates.TemplateResponse("external_dns.html", {
        "request": request,
        "active_page": "dns",
        "plugin_version": info["version"] if info else "1.0.0",
        "bindings": await external_dns_service.list_bindings(db),
        "providers": external_dns_service.providers(),
    })


@router.get("/api/providers")
async def api_providers():
    return JSONResponse({"providers": external_dns_service.providers()})


@router.get("/api/binding/{domain}")
async def api_binding(domain: str, db: AsyncSession = Depends(get_db)):
    binding = await external_dns_service.get_binding(db, domain)
    return JSONResponse({"binding": external_dns_service.binding_public(binding)})


@router.post("/api/test")
async def api_test(body: TestRequest):
    try:
        return JSONResponse(await external_dns_service.test_connection(
            body.provider, body.credentials, body.zone_ref
        ))
    except ExternalDnsError as exc:
        return JSONResponse({"ok": False, "error": exc.message}, status_code=exc.status_code)
    except Exception as exc:
        logger.warning("External DNS test failed: %s", exc)
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)


@router.post("/api/bind")
async def api_bind(body: BindRequest, db: AsyncSession = Depends(get_db)):
    label = f"Connect external DNS: {body.domain} ({body.provider})"
    try:
        binding = await external_dns_service.bind(
            db, body.domain, body.provider, body.credentials, body.zone_ref
        )
        await _task("external_bind", body.domain, label, True, f"{body.domain} now uses {body.provider} DNS.")
        return JSONResponse({"ok": True, "binding": binding})
    except ExternalDnsError as exc:
        await _task("external_bind", body.domain, label, False, exc.message)
        return JSONResponse({"ok": False, "error": exc.message}, status_code=exc.status_code)
    except Exception as exc:
        logger.warning("External DNS bind failed: %s", exc)
        await _task("external_bind", body.domain, label, False, str(exc))
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)


@router.post("/api/unbind")
async def api_unbind(body: UnbindRequest, db: AsyncSession = Depends(get_db)):
    label = f"Disconnect external DNS: {body.domain}"
    try:
        await external_dns_service.unbind(db, body.domain)
        await _task("external_unbind", body.domain, label, True, f"{body.domain} no longer uses external DNS.")
        return JSONResponse({"ok": True})
    except Exception as exc:
        logger.warning("External DNS unbind failed: %s", exc)
        await _task("external_unbind", body.domain, label, False, str(exc))
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
