"""Selective, confirmed removal of Railpack application data."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.container_app import ContainerApp
from models.container_app_backup import ContainerAppBackup
from models.container_app_database import ContainerAppDatabase
from services import container_app_backup_service, container_app_cleanup_service
from services import container_app_database_lifecycle_service as database_lifecycle


async def remove_selected_data(
    db: AsyncSession, app: ContainerApp, attachments: list[ContainerAppDatabase], *,
    database_ids: list[int], delete_app_volume: bool, delete_wordpress_files: bool,
    delete_backups: bool,
) -> bool:
    selected = set(database_ids)
    managed = {item.id: item for item in attachments if item.provider in {"docker", "panel_postgres", "panel_mariadb"}}
    if selected - managed.keys():
        raise ValueError("Only this app's local managed services can be deleted here.")
    backups = list((await db.scalars(select(ContainerAppBackup).where(ContainerAppBackup.app_id == app.id))).all())
    await _delete_selected_backups(db, backups, selected, delete_backups)
    for database_id in selected:
        item = managed[database_id]
        database_lifecycle.purge_managed(item)
        await db.delete(item)
    if delete_app_volume:
        volumes_to_remove = set(await container_app_cleanup_service.list_app_storage_volumes(app.id))
        if app.data_volume:
            volumes_to_remove.add(app.data_volume)
            app.data_volume, app.data_mount_path = None, None
        if app.storage_mounts:
            import json as _json
            try:
                mounts = _json.loads(app.storage_mounts)
                if isinstance(mounts, list):
                    for mount in mounts:
                        vol = mount.get("volume")
                        if vol:
                            volumes_to_remove.add(vol)
            except Exception:
                pass
        for volume in sorted(volumes_to_remove):
            await container_app_cleanup_service.remove_volume(volume)
        app.storage_mounts = None
        if getattr(app, "deploy_type", None) == "official_stack":
            from services.official_stacks import stack_runtime_service
            from services.official_stacks.catalog import get_stack
            stack_id = getattr(app, "stack_catalog_id", None)
            if stack_id:
                stack = get_stack(stack_id)
                if stack:
                    await asyncio.to_thread(stack_runtime_service.purge_stack_volumes, app.id, stack)
    if delete_wordpress_files and app.wordpress_content_volume:
        await container_app_cleanup_service.remove_volume(app.wordpress_content_volume)
        app.wordpress_content_volume = None
    remaining = [item for item in attachments if item.id not in selected]
    if not any(item.provider in {"docker", "panel_postgres", "panel_mariadb"} for item in remaining):
        await container_app_cleanup_service.remove_private_network(app)
    await db.flush()
    remaining_backups = bool(await db.scalar(select(ContainerAppBackup.id).where(
        ContainerAppBackup.app_id == app.id,
    )))
    remaining_data = bool(remaining) or bool(app.data_volume) or bool(app.storage_mounts) or bool(app.wordpress_content_volume) or remaining_backups
    return remaining_data


async def _delete_selected_backups(
    db: AsyncSession, backups: list[ContainerAppBackup], database_ids: set[int], delete_all: bool,
) -> None:
    selected = {item.id for item in backups if item.database_id in database_ids}
    changed = True
    while changed:
        changed = False
        for item in backups:
            if item.database_backup_id in selected and item.id not in selected:
                selected.add(item.id)
                changed = True
    if delete_all:
        selected = {item.id for item in backups}
    await container_app_backup_service.delete_backups(db, [item for item in backups if item.id in selected])
