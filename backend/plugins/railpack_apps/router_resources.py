"""Railpack managed-service and runtime-control endpoints."""
from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models.container_app import ContainerApp
from models.container_app_backup import ContainerAppBackup
from models.container_app_database import ContainerAppDatabase
from models.domain import Domain
from services import container_app_backup_service, container_app_control_service
from services import container_app_database_service, container_app_service, container_app_wordpress_service
from services import container_app_database_lifecycle_service as database_lifecycle
from services.resource_guard_service import resource_guard_service

router = APIRouter()


@router.post("/{app_id}/databases/{database_id}/reconnect")
async def reconnect_database(app_id: int, database_id: int, db: AsyncSession = Depends(get_db)):
    item = await _database(db, app_id, database_id)
    database_lifecycle.reconnect(item)
    await db.commit()
    return _back(app_id)


@router.post("/{app_id}/databases/{database_id}/rotate")
async def rotate_database(app_id: int, database_id: int, db: AsyncSession = Depends(get_db)):
    app, item = await _app(db, app_id), await _database(db, app_id, database_id)
    database_lifecycle.rotate_credentials(app, item)
    container_app_database_service.rebuild_environment(app, await container_app_database_service.attachments_for(db, app_id), container_app_database_service.read_app_environment(app))
    await db.commit()
    return _back(app_id)


@router.post("/{app_id}/databases/{database_id}/backup")
async def backup_database(app_id: int, database_id: int, db: AsyncSession = Depends(get_db)):
    await container_app_backup_service.create_database_backup(db, await _app(db, app_id), await _database(db, app_id, database_id))
    await db.commit()
    return _back(app_id)


@router.post("/{app_id}/databases/{database_id}/restore/{backup_id}")
async def restore_database(app_id: int, database_id: int, backup_id: int, confirmation: str = Form(""), db: AsyncSession = Depends(get_db)):
    app, item, backup = await _app(db, app_id), await _database(db, app_id, database_id), await db.get(ContainerAppBackup, backup_id)
    if backup is None:
        raise HTTPException(404, "Backup not found.")
    await _stop_for_restore(db, app)
    await container_app_backup_service.restore_database_backup(db, app, item, backup, confirmation)
    await db.commit()
    return _back(app_id)


@router.post("/{app_id}/databases/{database_id}/delete")
async def delete_database(app_id: int, database_id: int, confirmation: str = Form(""), db: AsyncSession = Depends(get_db)):
    item = await _database(db, app_id, database_id)
    database_lifecycle.delete_managed(item, confirmation)
    await db.execute(delete(ContainerAppBackup).where(ContainerAppBackup.database_id == item.id))
    await db.delete(item)
    await db.commit()
    return _back(app_id)


@router.post("/{app_id}/wordpress/update")
async def update_wordpress(app_id: int, db: AsyncSession = Depends(get_db)):
    app = await _app(db, app_id)
    if app.preset != "wordpress":
        raise HTTPException(404, "WordPress preset not found.")
    container_app_wordpress_service.update(app)
    return RedirectResponse(f"/plugins/railpack_apps/{app_id}", status_code=303)


@router.post("/{app_id}/wordpress/backup")
async def backup_wordpress(app_id: int, db: AsyncSession = Depends(get_db)):
    app, item = await _wordpress(db, app_id)
    await container_app_backup_service.create_wordpress_backup(db, app, item)
    await db.commit()
    return _back(app_id)


@router.post("/{app_id}/wordpress/restore/{backup_id}")
async def restore_wordpress(app_id: int, backup_id: int, confirmation: str = Form(""), db: AsyncSession = Depends(get_db)):
    app, item = await _wordpress(db, app_id)
    backup = await db.get(ContainerAppBackup, backup_id)
    if backup is None:
        raise HTTPException(404, "WordPress backup not found.")
    await _stop_for_restore(db, app)
    await container_app_backup_service.restore_wordpress_backup(db, app, item, backup, confirmation)
    await db.commit()
    return _back(app_id)


@router.post("/{app_id}/wordpress/purge")
async def purge_wordpress_data(app_id: int, confirmation: str = Form(""), db: AsyncSession = Depends(get_db)):
    app = await _app(db, app_id)
    if app.preset != "wordpress" or confirmation != f"DELETE WORDPRESS {app.id}":
        raise HTTPException(400, f"Type DELETE WORDPRESS {app.id} to permanently erase WordPress files.")
    result = container_app_service._run(["docker", "volume", "rm", app.wordpress_content_volume or ""], timeout=45)
    if result.returncode:
        raise HTTPException(502, (result.stderr or result.stdout or "Could not remove WordPress content.")[-1000:])
    app.wordpress_content_volume = None
    await db.commit()
    return _back(app_id)


@router.post("/{app_id}/{action}")
async def control(app_id: int, action: str, db: AsyncSession = Depends(get_db)):
    app = await _app(db, app_id)
    domain = await db.get(Domain, app.domain_id)
    if domain is None or app.status in {"deleting", "delete_failed", "data_preserved"}:
        raise HTTPException(409, "This application cannot be controlled now.")
    if action != "stop":
        try:
            await resource_guard_service.allow_start(db)
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc
    await container_app_control_service.control(db, app, domain, action)
    return RedirectResponse(f"/plugins/railpack_apps/{app.id}", status_code=303)


async def _app(db, app_id):
    app = await db.get(ContainerApp, app_id)
    if app is None:
        raise HTTPException(404, "Container app not found.")
    return app


async def _database(db, app_id, database_id):
    item = await db.get(ContainerAppDatabase, database_id)
    if item is None or item.app_id != app_id:
        raise HTTPException(404, "Managed database not found.")
    return item


async def _wordpress(db, app_id):
    app = await _app(db, app_id)
    item = next((item for item in await container_app_database_service.attachments_for(db, app.id) if item.kind == "mariadb"), None)
    if app.preset != "wordpress" or item is None:
        raise HTTPException(404, "WordPress data is not configured.")
    return app, item


async def _stop_for_restore(db, app):
    if app.status == "running":
        domain = await db.get(Domain, app.domain_id)
        if domain is None:
            raise HTTPException(409, "App domain is missing.")
        await container_app_control_service.control(db, app, domain, "stop")


def _back(app_id):
    return RedirectResponse(f"/plugins/railpack_apps/{app_id}#databases", status_code=303)
