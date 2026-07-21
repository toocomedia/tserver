"""
routers/errors.py — Admin Error Tracker routes.
All paths under /admin/errors. Calls error_service only.
"""
import logging
from fastapi import APIRouter, Depends, Request, Query
from fastapi.responses import HTMLResponse, RedirectResponse, PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from services import error_service
from templating import templates

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin/errors", tags=["admin-errors"])

SOURCES = ["domain", "dns", "ssl", "proxy", "nginx", "powerdns", "system", "http"]


# ---------------------------------------------------------------
# LIST
# ---------------------------------------------------------------
@router.get("/", response_class=HTMLResponse)
async def errors_index(
    request: Request,
    source: str | None = Query(default=None),
    status: str = Query(default="open"),  # open | resolved | all
    q: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    resolved: bool | None
    if status == "open":
        resolved = False
    elif status == "resolved":
        resolved = True
    else:
        resolved = None

    src = source if source in SOURCES else None
    events = await error_service.list_errors(
        db, resolved=resolved, source=src, q=q, limit=100
    )
    open_count = await error_service.unresolved_count(db)

    return templates.TemplateResponse("pages/admin/errors/index.html", {
        "request": request,
        "active_page": "errors",
        "events": events,
        "sources": SOURCES,
        "filter_source": src or "",
        "filter_status": status,
        "filter_q": q or "",
        "open_count": open_count,
        "flash_error": (request.query_params.get("error") or "")[:500] or None,
        "flash_ok": (request.query_params.get("ok") or "")[:500] or None,
    })


# ---------------------------------------------------------------
# BULK ACTIONS (before /{error_id} routes)
# ---------------------------------------------------------------
@router.post("/clear-resolved")
async def errors_clear_resolved(db: AsyncSession = Depends(get_db)):
    n = await error_service.clear_resolved(db)
    return RedirectResponse(
        f"/admin/errors/?ok=Cleared+{n}+resolved+errors",
        status_code=303,
    )


@router.post("/clear-all")
async def errors_clear_all(db: AsyncSession = Depends(get_db)):
    n = await error_service.clear_all(db)
    return RedirectResponse(
        f"/admin/errors/?ok=Cleared+{n}+errors",
        status_code=303,
    )


# ---------------------------------------------------------------
# DETAIL
# ---------------------------------------------------------------
@router.get("/{error_id}", response_class=HTMLResponse)
async def errors_detail(
    request: Request,
    error_id: int,
    db: AsyncSession = Depends(get_db),
):
    try:
        event = await error_service.get(db, error_id)
    except Exception:
        return RedirectResponse("/admin/errors/?error=Error+not+found", status_code=303)

    report = error_service.format_report(event)
    return templates.TemplateResponse("pages/admin/errors/detail.html", {
        "request": request,
        "active_page": "errors",
        "event": event,
        "report": report,
    })


@router.get("/{error_id}/report.txt")
async def errors_report_txt(error_id: int, db: AsyncSession = Depends(get_db)):
    event = await error_service.get(db, error_id)
    report = error_service.format_report(event)
    return PlainTextResponse(
        report,
        media_type="text/plain",
        headers={
            "Content-Disposition": f'attachment; filename="error-{error_id}.txt"'
        },
    )


# ---------------------------------------------------------------
# PER-ITEM ACTIONS
# ---------------------------------------------------------------
@router.post("/{error_id}/resolve")
async def errors_resolve(error_id: int, db: AsyncSession = Depends(get_db)):
    try:
        await error_service.mark_resolved(db, error_id)
        return RedirectResponse(f"/admin/errors/{error_id}?ok=resolved", status_code=303)
    except Exception as exc:
        msg = str(getattr(exc, "detail", exc))
        return RedirectResponse(f"/admin/errors/?error={msg}", status_code=303)


@router.post("/{error_id}/reopen")
async def errors_reopen(error_id: int, db: AsyncSession = Depends(get_db)):
    try:
        await error_service.mark_unresolved(db, error_id)
        return RedirectResponse(f"/admin/errors/{error_id}?ok=reopened", status_code=303)
    except Exception as exc:
        msg = str(getattr(exc, "detail", exc))
        return RedirectResponse(f"/admin/errors/?error={msg}", status_code=303)


@router.post("/{error_id}/delete")
async def errors_delete(error_id: int, db: AsyncSession = Depends(get_db)):
    try:
        await error_service.delete(db, error_id)
        return RedirectResponse("/admin/errors/?ok=deleted", status_code=303)
    except Exception as exc:
        msg = str(getattr(exc, "detail", exc))
        return RedirectResponse(f"/admin/errors/?error={msg}", status_code=303)
