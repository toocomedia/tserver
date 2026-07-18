"""
services/error_service.py — Admin error tracker business logic.
record() never raises — capture must not break user responses.
"""
import json
import logging
import traceback as tb_mod
from datetime import datetime, timezone

from fastapi import HTTPException, Request
from sqlalchemy import select, func, or_
from sqlalchemy.ext.asyncio import AsyncSession

from models.error_event import ErrorEvent
from database import AsyncSessionLocal

logger = logging.getLogger(__name__)

VALID_LEVELS = frozenset({"error", "warning", "critical"})
VALID_SOURCES = frozenset({
    "domain", "dns", "ssl", "proxy", "nginx", "powerdns", "system", "http",
})


def _truncate(text: str | None, max_len: int = 8000) -> str | None:
    if text is None:
        return None
    text = str(text)
    if len(text) <= max_len:
        return text
    return text[: max_len - 20] + "\n… [truncated]"


def _context_to_json(context: dict | None) -> str | None:
    if not context:
        return None
    try:
        return json.dumps(context, default=str, indent=2)
    except Exception:
        return str(context)


def _infer_source(path: str | None) -> str:
    if not path:
        return "http"
    p = path.lower()
    if p.startswith("/domains"):
        return "domain"
    if p.startswith("/dns"):
        return "dns"
    if p.startswith("/ssl"):
        return "ssl"
    if p.startswith("/proxy"):
        return "proxy"
    if p.startswith("/admin"):
        return "system"
    return "http"


# ---------------------------------------------------------------
# RECORD (never raises)
# ---------------------------------------------------------------
async def record(
    *,
    level: str = "error",
    source: str = "http",
    operation: str = "unknown",
    message: str,
    detail: str | None = None,
    traceback: str | None = None,
    context: dict | None = None,
    request: Request | None = None,
    db: AsyncSession | None = None,  # accepted for API compat; not used for write
) -> ErrorEvent | None:
    """
    Persist an error event on a dedicated session (always commits).
    Survives request-scoped rollbacks when the user action fails.
    Marks request.state.error_recorded when request is present.
    """
    del db  # writes always use a dedicated session
    try:
        level = level if level in VALID_LEVELS else "error"
        source = source if source in VALID_SOURCES else "http"
        message = (message or "Unknown error").strip()[:500]

        method = path = req_id = None
        if request is not None:
            method = request.method
            path = str(request.url.path)
            req_id = getattr(request.state, "request_id", None)

        event = ErrorEvent(
            level=level,
            source=source,
            operation=(operation or "unknown")[:64],
            message=message,
            detail=_truncate(detail),
            traceback=_truncate(traceback, 16000),
            request_method=method,
            request_path=path,
            request_id=req_id,
            context_json=_truncate(_context_to_json(context), 8000),
            resolved=False,
        )

        async with AsyncSessionLocal() as session:
            session.add(event)
            await session.commit()
            await session.refresh(event)

        if request is not None:
            request.state.error_recorded = True

        logger.info(
            "ErrorEvent recorded id=%s source=%s op=%s: %s",
            event.id, source, operation, message[:120],
        )
        return event
    except Exception as e:
        logger.error("error_service.record failed: %s", e)
        return None


async def record_exception(
    exc: BaseException,
    *,
    source: str | None = None,
    operation: str = "unhandled",
    request: Request | None = None,
    context: dict | None = None,
    db: AsyncSession | None = None,
) -> ErrorEvent | None:
    """Record from an exception object (stack included)."""
    path = str(request.url.path) if request else None
    src = source or _infer_source(path)
    detail = str(getattr(exc, "detail", None) or exc)
    return await record(
        level="error",
        source=src,
        operation=operation,
        message=detail[:500],
        detail=detail,
        traceback=tb_mod.format_exc(),
        context=context,
        request=request,
        db=db,
    )


# ---------------------------------------------------------------
# QUERIES
# ---------------------------------------------------------------
async def list_errors(
    db: AsyncSession,
    *,
    resolved: bool | None = None,
    source: str | None = None,
    q: str | None = None,
    limit: int = 100,
) -> list[ErrorEvent]:
    stmt = select(ErrorEvent).order_by(ErrorEvent.created_at.desc())
    if resolved is not None:
        stmt = stmt.where(ErrorEvent.resolved == resolved)
    if source:
        stmt = stmt.where(ErrorEvent.source == source)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(
                ErrorEvent.message.ilike(like),
                ErrorEvent.operation.ilike(like),
                ErrorEvent.detail.ilike(like),
            )
        )
    stmt = stmt.limit(min(max(limit, 1), 500))
    return list((await db.execute(stmt)).scalars().all())


async def get(db: AsyncSession, error_id: int) -> ErrorEvent:
    event = await db.scalar(
        select(ErrorEvent).where(ErrorEvent.id == error_id)
    )
    if not event:
        raise HTTPException(status_code=404, detail="Error event not found")
    return event


async def unresolved_count(db: AsyncSession) -> int:
    count = await db.scalar(
        select(func.count())
        .select_from(ErrorEvent)
        .where(ErrorEvent.resolved == False)  # noqa: E712
    )
    return int(count or 0)


# ---------------------------------------------------------------
# MUTATIONS
# ---------------------------------------------------------------
async def mark_resolved(db: AsyncSession, error_id: int) -> ErrorEvent:
    event = await get(db, error_id)
    event.resolved = True
    event.resolved_at = datetime.now(timezone.utc).replace(tzinfo=None)
    await db.flush()
    return event


async def mark_unresolved(db: AsyncSession, error_id: int) -> ErrorEvent:
    event = await get(db, error_id)
    event.resolved = False
    event.resolved_at = None
    await db.flush()
    return event


async def delete(db: AsyncSession, error_id: int) -> None:
    event = await get(db, error_id)
    await db.delete(event)


async def clear_resolved(db: AsyncSession) -> int:
    rows = (await db.execute(
        select(ErrorEvent).where(ErrorEvent.resolved == True)  # noqa: E712
    )).scalars().all()
    n = 0
    for row in rows:
        await db.delete(row)
        n += 1
    return n


async def clear_all(db: AsyncSession) -> int:
    rows = (await db.execute(select(ErrorEvent))).scalars().all()
    n = 0
    for row in rows:
        await db.delete(row)
        n += 1
    return n


# ---------------------------------------------------------------
# REPORT
# ---------------------------------------------------------------
def format_report(event: ErrorEvent) -> str:
    """Plain-text multi-line report for clipboard / download."""
    when = event.created_at.strftime("%Y-%m-%d %H:%M:%S") if event.created_at else "—"
    req = "—"
    if event.request_method or event.request_path:
        req = f"{event.request_method or ''} {event.request_path or ''}".strip()

    lines = [
        "=== SRV Panel Error Report ===",
        f"ID:        {event.id}",
        f"When:      {when}",
        f"Level:     {event.level}",
        f"Source:    {event.source}",
        f"Operation: {event.operation}",
        f"Request:   {req}",
        f"RequestID: {event.request_id or '—'}",
        f"Resolved:  {event.resolved}",
        f"Message:   {event.message}",
        "",
        "--- Detail ---",
        event.detail or "(none)",
        "",
        "--- Context ---",
        event.context_json or "(none)",
        "",
        "--- Traceback ---",
        event.traceback or "(none)",
        "=== End Report ===",
    ]
    return "\n".join(lines)
