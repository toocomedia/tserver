"""MariaDB Manager routes. Database work stays in the root-owned helper."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from dependencies import dependency_manager
from plugins.mariadb_manager.schemas import Confirmation, DatabaseCreate
from plugins.mariadb_manager.service import mariadb_manager_service
from templating import templates


router = APIRouter(prefix="/plugins/mariadb_manager", tags=["mariadb_manager"])


def _require_managed_mariadb() -> None:
    status = dependency_manager.get_status("mariadb")
    if not status or not status.get("healthy"):
        raise HTTPException(409, "Start MariaDB from Dependencies before using MariaDB Manager.")
    if status.get("install_origin") != "panel_managed":
        raise HTTPException(409, "MariaDB Manager is available only for panel-managed MariaDB.")


@router.get("/", response_class=HTMLResponse)
async def mariadb_index(request: Request):
    status = dependency_manager.get_status("mariadb", cached=True) or {}
    databases: list[dict] = []
    users: list[dict] = []
    error = None
    if status.get("healthy") and status.get("install_origin") == "panel_managed":
        try:
            databases = mariadb_manager_service.list_databases()
            users = mariadb_manager_service.list_users()
        except RuntimeError as exc:
            error = str(exc)
    return templates.TemplateResponse(
        "mariadb.html",
        {
            "request": request,
            "active_page": "plugins",
            "status": status,
            "databases": databases,
            "users": users,
            "manager_error": error,
        },
    )


@router.get("/api/databases")
async def list_databases():
    _require_managed_mariadb()
    try:
        return JSONResponse(mariadb_manager_service.list_databases())
    except RuntimeError as exc:
        raise HTTPException(502, str(exc)) from exc


@router.post("/api/databases")
async def create_database(body: DatabaseCreate):
    _require_managed_mariadb()
    try:
        return JSONResponse(mariadb_manager_service.create_database(body.database, body.user), status_code=201)
    except RuntimeError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.delete("/api/databases/{database}")
async def delete_database(database: str, body: Confirmation):
    _require_managed_mariadb()
    if body.confirmation != f"DELETE DATABASE {database}":
        raise HTTPException(409, f"Type DELETE DATABASE {database} to confirm.")
    try:
        mariadb_manager_service.drop_database(database)
        return JSONResponse({"status": "ok", "database": database})
    except RuntimeError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/api/users")
async def list_users():
    _require_managed_mariadb()
    try:
        return JSONResponse(mariadb_manager_service.list_users())
    except RuntimeError as exc:
        raise HTTPException(502, str(exc)) from exc


@router.delete("/api/users/{user}")
async def delete_user(user: str, body: Confirmation):
    _require_managed_mariadb()
    if body.confirmation != f"DELETE USER {user}":
        raise HTTPException(409, f"Type DELETE USER {user} to confirm.")
    try:
        mariadb_manager_service.drop_user(user)
        return JSONResponse({"status": "ok", "user": user})
    except RuntimeError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/api/users/{user}/password")
async def reset_user_password(user: str, body: Confirmation):
    _require_managed_mariadb()
    if body.confirmation != f"RESET PASSWORD {user}":
        raise HTTPException(409, f"Type RESET PASSWORD {user} to confirm.")
    try:
        return JSONResponse({"user": user, "password": mariadb_manager_service.reset_password(user)})
    except RuntimeError as exc:
        raise HTTPException(400, str(exc)) from exc
