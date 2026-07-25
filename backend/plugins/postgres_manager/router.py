"""
router.py — FastAPI routes for the PostgreSQL Manager plugin.

UI routes:   GET  /plugins/postgres_manager/
API routes:  /plugins/postgres_manager/api/*

All business logic is delegated to service.py and queries.py.
No direct subprocess or DB calls here.
"""
import logging
import os
import subprocess
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

import config
from database import get_db
from templating import templates
from plugins.postgres_manager.service import postgres_service
from plugins.postgres_manager import queries as pg
from plugins.postgres_manager.schemas import (
    DatabaseCreate, UserCreate, PasswordChange, QueryRequest,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/plugins/postgres_manager", tags=["postgres_manager"])
SCRIPT_DIR = Path(__file__).parent / "scripts"


def _sudo_cmd(script_path: Path) -> list[str]:
    cmd = ["bash", str(script_path)]
    if hasattr(os, "geteuid") and os.geteuid() != 0 and getattr(config, "PRIVILEGED_SUDO", True):
        cmd = ["sudo", "-n", *cmd]
    return cmd


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

@router.get("/", response_class=HTMLResponse)
async def pg_index(request: Request):
    """Render the PostgreSQL Manager main page."""
    from plugins.manager import plugin_manager
    info = plugin_manager.get_plugin("postgres_manager")
    plugin_version = info["version"] if info else "1.0.0"

    status = postgres_service.get_status()
    plugin_installed = status["installed"]
    databases = pg.list_databases() if status["running"] else []
    users = pg.list_users() if status["running"] else []
    system_roles = pg.list_system_roles() if status["running"] else []

    return templates.TemplateResponse("postgres.html", {

        "request": request,
        "active_page": "plugins",
        "plugin_version": plugin_version,
        "plugin_installed": plugin_installed,
        "status": status,
        "databases": databases,
        "users": users,
        "system_roles": system_roles,
    })


@router.get("/remote/new", response_class=HTMLResponse)
async def pg_remote_new():
    """Keep old links working after endpoint creation moved into the plugin tab."""
    return RedirectResponse("/plugins/postgres_manager/", status_code=303)





# ---------------------------------------------------------------------------
# Install / Uninstall
# ---------------------------------------------------------------------------

@router.post("/api/install")
async def install_postgres(request: Request):
    """Trigger the install.sh script to install PostgreSQL."""
    script = SCRIPT_DIR / "install.sh"
    if os.name == "nt":
        return JSONResponse({"status": "ok", "message": "Mock install on Windows."})
    try:
        res = subprocess.run(
            _sudo_cmd(script), capture_output=True, text=True, timeout=180,
        )
        if res.returncode != 0:
            logger.error("PostgreSQL install failed: %s", res.stderr or res.stdout)
            raise HTTPException(status_code=500, detail=res.stderr or res.stdout)
        postgres_service.pause()  # invalidate any stale cache
        return JSONResponse({"status": "ok", "message": "PostgreSQL installed."})
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=500, detail="Install script timed out.")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/api/uninstall")
async def uninstall_postgres(request: Request):
    """Trigger the uninstall.sh script. Data is preserved."""
    script = SCRIPT_DIR / "uninstall.sh"
    if os.name == "nt":
        return JSONResponse({"status": "ok", "message": "Mock uninstall on Windows."})
    try:
        res = subprocess.run(
            _sudo_cmd(script), capture_output=True, text=True, timeout=60,
        )
        if res.returncode != 0:
            raise HTTPException(status_code=500, detail=res.stderr or res.stdout)
        postgres_service.pause()
        return JSONResponse({"status": "ok", "message": "PostgreSQL uninstalled. Data preserved."})
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

@router.get("/api/status")
async def api_status():
    """Return current service status. Cached for 30 seconds."""
    return JSONResponse(postgres_service.get_status())


# ---------------------------------------------------------------------------
# Service control
# ---------------------------------------------------------------------------

@router.post("/api/service/{action}")
async def service_action(action: str):
    """Start, stop, or restart the PostgreSQL service."""
    if action not in ("start", "stop", "restart"):
        raise HTTPException(status_code=400, detail="Action must be start, stop, or restart.")
    try:
        getattr(postgres_service, action)()
        return JSONResponse({"status": "ok", "action": action})
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


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

