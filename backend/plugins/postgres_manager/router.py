"""
router.py — FastAPI routes for the PostgreSQL Manager plugin.

UI routes:   GET  /plugins/postgres_manager/
API routes:  /plugins/postgres_manager/api/*

All business logic is delegated to service.py and queries.py.
No direct subprocess or DB calls here.
"""
import logging
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from database import get_db
from templating import templates
from plugins.postgres_manager.service import postgres_service
from dependencies import dependency_manager
from plugins.postgres_manager import queries as pg
from plugins.postgres_manager.schemas import (
    DatabaseCreate, UserCreate, PasswordChange, QueryRequest, RemoteConfigRequest,
)
from services.task_manager_service import task_manager_service
from utils.search_and_bulk import BulkActionRequest

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/plugins/postgres_manager", tags=["postgres_manager"])
# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

@router.get("/", response_class=HTMLResponse)
async def pg_index(request: Request, db: AsyncSession = Depends(get_db)):
    """Render the PostgreSQL Manager main page."""
    from plugins.manager import plugin_manager
    info = plugin_manager.get_plugin("postgres_manager")
    plugin_version = info["version"] if info else "1.0.0"

    databases: list[dict] = []
    users: list[dict] = []
    system_roles: list[dict] = []
    from models.domain import Domain
    domains = list((await db.scalars(select(Domain).order_by(Domain.name))).all())
    if dependency_manager.is_healthy("postgresql"):
        try:
            databases = pg.list_databases()
            users = pg.list_users()
            system_roles = pg.list_system_roles()
        except RuntimeError as exc:
            logger.warning("PostgreSQL stopped while loading manager: %s", exc)

    return templates.TemplateResponse("postgres.html", {

        "request": request,
        "active_page": "plugins",
        "plugin_version": plugin_version,
        "databases": databases,
        "users": users,
        "system_roles": system_roles,
        "remote_domains": await postgres_service.list_remote_domains(db),
        "domains": domains,
    })


@router.get("/remote", response_class=HTMLResponse)
async def pg_remote_list(request: Request, db: AsyncSession = Depends(get_db)):
    return RedirectResponse("/plugins/postgres_manager/?tab=remote", status_code=303)


@router.get("/remote/new", response_class=HTMLResponse)
async def pg_remote_new(request: Request, db: AsyncSession = Depends(get_db)):
    return RedirectResponse("/plugins/postgres_manager/?tab=remote", status_code=303)





# ---------------------------------------------------------------------------
# Databases
# ---------------------------------------------------------------------------

@router.get("/api/databases")
async def api_list_databases():
    return JSONResponse(pg.list_databases())


@router.post("/api/databases")
async def api_create_database(body: DatabaseCreate):
    try:
        pg.create_database(body.name, body.owner)
        return JSONResponse({"status": "ok", "name": body.name})
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/api/bulk")
async def postgres_bulk_action(payload: BulkActionRequest):
    """Bulk delete PostgreSQL databases."""
    if payload.action not in ["delete"]:
        raise HTTPException(400, f"Invalid bulk action '{payload.action}'.")
    target_ids = payload.target_ids
    if not target_ids:
        return {"success": False, "processed": 0, "failed": 0, "message": "No database names provided."}

    processed = 0
    errors = []

    for db_name in target_ids:
        db_name_str = str(db_name)
        try:
            pg.drop_database(db_name_str)
            processed += 1
        except Exception as exc:
            errors.append(f"DB '{db_name_str}': {str(exc)}")

    await task_manager_service.record_completed_task(
        category="postgres",
        action=f"bulk_{payload.action}",
        target_id="bulk",
        label=f"Bulk Delete PostgreSQL Databases ({processed})",
        success=len(errors) == 0 or processed > 0,
        message=f"Bulk dropped {processed} PostgreSQL database(s)."
    )

    return {
        "success": len(errors) == 0 or processed > 0,
        "processed": processed,
        "failed": len(errors),
        "message": f"Successfully dropped {processed} database(s)." + (f" ({len(errors)} failed)" if errors else ""),
        "errors": errors
    }


@router.delete("/api/databases/{name}")
async def api_drop_database(name: str):
    try:
        pg.drop_database(name)
        return JSONResponse({"status": "ok", "name": name})
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/api/databases/{name}/tables")
async def api_list_tables(name: str):
    try:
        return JSONResponse(pg.list_tables(name))
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

@router.get("/api/users")
async def api_list_users():
    return JSONResponse(pg.list_users())


@router.get("/api/system-roles")
async def api_list_system_roles():
    return JSONResponse(pg.list_system_roles())



@router.post("/api/users")
async def api_create_user(body: UserCreate):
    try:
        pg.create_user(body.name, body.password)
        return JSONResponse({"status": "ok", "name": body.name})
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.delete("/api/users/{name}")
async def api_drop_user(name: str):
    try:
        pg.drop_user(name)
        return JSONResponse({"status": "ok", "name": name})
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/api/users/{name}/password")
async def api_change_password(name: str, body: PasswordChange):
    try:
        pg.change_password(name, body.password)
        return JSONResponse({"status": "ok"})
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# ---------------------------------------------------------------------------
# Query runner
# ---------------------------------------------------------------------------

@router.post("/api/query")
async def api_query(body: QueryRequest):
    try:
        rows = pg.run_query(body.db, body.sql)
        return JSONResponse({"rows": rows, "count": len(rows)})
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
# ---------------------------------------------------------------------------
# Remote access
# ---------------------------------------------------------------------------

@router.get("/api/remote/domains")
async def api_remote_domains(db: AsyncSession = Depends(get_db)):
    return JSONResponse(await postgres_service.list_remote_domains(db))


@router.post("/api/remote/domains")
async def api_add_remote_domain(body: RemoteConfigRequest, db: AsyncSession = Depends(get_db)):
    try:
        result = await postgres_service.add_remote_domain(
            db, body.mode, body.domain, body.subdomain, body.hostname,
            allowed_cidrs=body.allowed_cidrs, encryption_enabled=body.encryption_enabled,
        )
        return JSONResponse(result, status_code=201)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.exception("Could not create PostgreSQL remote endpoint")
        raise HTTPException(status_code=400, detail=str(exc) or "Could not save the remote endpoint.")


@router.delete("/api/remote/domains/{domain}")
async def api_delete_remote_domain(domain: str, db: AsyncSession = Depends(get_db)):
    try:
        await postgres_service.delete_remote_domain(db, domain)
        return JSONResponse({"status": "ok"})
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        logger.exception("Could not delete PostgreSQL remote endpoint %s", domain)
        raise HTTPException(status_code=400, detail=str(exc) or "Could not delete remote endpoint.")


@router.post("/api/remote/domains/{domain}/ssl")
async def api_reissue_remote_ssl(domain: str, db: AsyncSession = Depends(get_db)):
    try:
        return JSONResponse(await postgres_service.reissue_remote_ssl(db, domain))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.exception("Could not reissue SSL for %s", domain)
        raise HTTPException(status_code=400, detail=str(exc) or "SSL reissue failed.")


@router.post("/api/remote/domains/{domain}/test")
async def api_test_remote_domain(domain: str, db: AsyncSession = Depends(get_db)):
    try:
        return JSONResponse(await postgres_service.test_remote_domain(db, domain))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.exception("Could not test PostgreSQL remote endpoint %s", domain)
        raise HTTPException(status_code=400, detail=str(exc) or "Connection test failed.")
