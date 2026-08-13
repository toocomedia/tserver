"""MariaDB Manager routes. Database work stays in the root-owned helper."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from dependencies import dependency_manager
from database import get_db
from models.php_website_database import PhpWebsiteDatabase
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
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
    from plugins.manager import plugin_manager

    plugin_info = plugin_manager.get_plugin("mariadb_manager")
    plugin_version = plugin_info["version"] if plugin_info else "1.0.0"
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
            "plugin_version": plugin_version,
        },
    )


@router.get("/api/databases")
async def list_databases(db: AsyncSession = Depends(get_db)):
    _require_managed_mariadb()
    try:
        values = mariadb_manager_service.list_databases()
        owned = {item.database_name: item.site_id for item in (await db.scalars(select(PhpWebsiteDatabase))).all()}
        for item in values:
            if item.get("name") in owned:
                item["owner"] = {"type": "php_site", "site_id": owned[item["name"]]}
        return JSONResponse(values)
    except RuntimeError as exc:
        raise HTTPException(502, str(exc)) from exc


@router.post("/api/databases")
async def create_database(body: DatabaseCreate, db: AsyncSession = Depends(get_db)):
    _require_managed_mariadb()
    owner = await db.scalar(select(PhpWebsiteDatabase.id).where(
        (PhpWebsiteDatabase.database_name == body.database) | (PhpWebsiteDatabase.username == body.user)
    ))
    if owner:
        raise HTTPException(409, "Database or user belongs to a PHP website. Manage it from PHP Websites.")
    try:
        return JSONResponse(mariadb_manager_service.create_database(body.database, body.user), status_code=201)
    except RuntimeError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.delete("/api/databases/{database}")
async def delete_database(database: str, body: Confirmation, db: AsyncSession = Depends(get_db)):
    _require_managed_mariadb()
    owner = await db.scalar(select(PhpWebsiteDatabase).where(PhpWebsiteDatabase.database_name == database))
    if owner:
        raise HTTPException(409, f"Database belongs to PHP website {owner.site_id}. Remove it from PHP Websites.")
    if body.confirmation != f"DELETE DATABASE {database}":
        raise HTTPException(409, f"Type DELETE DATABASE {database} to confirm.")
    try:
        mariadb_manager_service.drop_database(database)
        return JSONResponse({"status": "ok", "database": database})
    except RuntimeError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/api/users")
async def list_users(db: AsyncSession = Depends(get_db)):
    _require_managed_mariadb()
    try:
        values = mariadb_manager_service.list_users()
        owned = {item.username: item.site_id for item in (await db.scalars(select(PhpWebsiteDatabase))).all()}
        for item in values:
            if item.get("name") in owned:
                item["owner"] = {"type": "php_site", "site_id": owned[item["name"]]}
        return JSONResponse(values)
    except RuntimeError as exc:
        raise HTTPException(502, str(exc)) from exc


@router.delete("/api/users/{user}")
async def delete_user(user: str, body: Confirmation, db: AsyncSession = Depends(get_db)):
    _require_managed_mariadb()
    owner = await db.scalar(select(PhpWebsiteDatabase).where(PhpWebsiteDatabase.username == user))
    if owner:
        raise HTTPException(409, f"User belongs to PHP website {owner.site_id}. Remove it from PHP Websites.")
    if body.confirmation != f"DELETE USER {user}":
        raise HTTPException(409, f"Type DELETE USER {user} to confirm.")
    try:
        mariadb_manager_service.drop_user(user)
        return JSONResponse({"status": "ok", "user": user})
    except RuntimeError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/api/users/{user}/password")
async def reset_user_password(user: str, body: Confirmation, db: AsyncSession = Depends(get_db)):
    _require_managed_mariadb()
    owner = await db.scalar(select(PhpWebsiteDatabase).where(PhpWebsiteDatabase.username == user))
    if owner:
        raise HTTPException(409, f"User belongs to PHP website {owner.site_id}. Rotate it from PHP Websites.")
    if body.confirmation != f"RESET PASSWORD {user}":
        raise HTTPException(409, f"Type RESET PASSWORD {user} to confirm.")
    try:
        return JSONResponse({"user": user, "password": mariadb_manager_service.reset_password(user)})
    except RuntimeError as exc:
        raise HTTPException(400, str(exc)) from exc
